"""Dagster entrypoint: raw sessions to the public wifi.gtfs.zone artifacts.

Every asset is rebuilt from the raw bucket on each run, hourly. Assets pass
their values through the default IO manager, which a run keeps inside the
code-server pod.
"""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dagster import (
    AssetExecutionContext,
    DefaultScheduleStatus,
    Definitions,
    EnvVar,
    MaterializeResult,
    MetadataValue,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from gtfs_zone_wifi_pipeline import aggregate
from gtfs_zone_wifi_pipeline.config import USER_AGENT, Bucket, Params, Transitous
from gtfs_zone_wifi_pipeline.network import Network, build_network
from gtfs_zone_wifi_pipeline.publish import publish as write_public
from gtfs_zone_wifi_pipeline.sessions import RawSessions, load_excluded, read_raw
from gtfs_zone_wifi_pipeline.snap import Snapped, snap_all
from gtfs_zone_wifi_pipeline.transitous import TripShape, load_shapes


def client(transitous: Transitous) -> httpx.Client:
    return httpx.Client(
        base_url=transitous.base_url,
        headers={"User-Agent": USER_AGENT},
        timeout=transitous.timeout_s,
    )


@asset
def raw_sessions(
    context: AssetExecutionContext, raw: Bucket, params: Params
) -> RawSessions:
    """Valid sessions under raw/ in wifi-raw, less the excluded ones."""
    out = read_raw(raw.store(), load_excluded(Path(params.excluded_sessions)))
    for entry in out.invalid:
        context.log.warning("invalid %s: %s", entry["key"], "; ".join(entry["errors"]))
    context.add_output_metadata(
        {
            "sessions": len(out.sessions),
            "trips": sum(s["kind"] == "trip" for s in out.sessions),
            "stations": sum(s["kind"] == "station" for s in out.sessions),
            "invalid": len(out.invalid),
            "invalid_report": MetadataValue.json(out.invalid),
            "excluded": len(out.excluded),
            "duplicates": MetadataValue.json(out.duplicates),
        }
    )
    return out


@asset
def trip_shapes(
    context: AssetExecutionContext,
    raw_sessions: RawSessions,
    raw: Bucket,
    transitous: Transitous,
) -> dict[str, TripShape]:
    """Trip geometry and stops per Transitous trip id, cached under cache/trips/."""
    ids = [
        s["trip"]["transitous_trip_id"]
        for s in raw_sessions.sessions
        if s["kind"] == "trip"
    ]
    with client(transitous) as http:
        shapes, failed, fetched = load_shapes(raw.store(), http, ids)
    for trip_id, error in sorted(failed.items()):
        context.log.warning("trip %s: %s", trip_id, error)
    context.add_output_metadata(
        {"trips": len(shapes), "fetched": fetched, "failed": MetadataValue.json(failed)}
    )
    return shapes


@asset
def skeleton(
    context: AssetExecutionContext, trip_shapes: dict[str, TripShape], params: Params
) -> Network:
    """The skeleton network of every trip shape, cut into bins."""
    network = build_network(trip_shapes, params)
    context.add_output_metadata(
        {
            "clusters": len(network.clusters),
            "edges": sum(len(c.edges) for c in network.clusters),
            "bins": len(network.bins()),
            "stats": MetadataValue.json([c.stats for c in network.clusters]),
        }
    )
    return network


@asset
def snapped_samples(
    context: AssetExecutionContext,
    raw_sessions: RawSessions,
    trip_shapes: dict[str, TripShape],
    skeleton: Network,
    params: Params,
) -> Snapped:
    """Samples and net tests placed in bins (trips) or at stations."""
    out = snap_all(raw_sessions.sessions, trip_shapes, skeleton, params)
    dropped: dict[str, dict[str, int]] = {
        s.session_id: s.dropped for s in [*out.trips, *out.stations] if s.dropped
    }
    context.add_output_metadata(
        {
            "trips": len(out.trips),
            "stations": len(out.stations),
            "samples": sum(len(s.samples) for s in [*out.trips, *out.stations]),
            "skipped": MetadataValue.json(out.skipped),
            "dropped": MetadataValue.json(dropped),
        }
    )
    return out


@asset
def route_aggregates(
    context: AssetExecutionContext,
    snapped_samples: Snapped,
    skeleton: Network,
    trip_shapes: dict[str, TripShape],
    params: Params,
) -> tuple[list[aggregate.RouteAggregate], dict[str, dict[str, Any]]]:
    """Per route and bin, and per bin over all routes."""
    routes, bins = aggregate.route_aggregates(
        snapped_samples, skeleton, trip_shapes, params.min_sessions
    )
    context.add_output_metadata(
        {
            "routes": len(routes),
            "route_bins": sum(len(r.bins) for r in routes),
            "bins": len(bins),
        }
    )
    return routes, bins


@asset
def station_aggregates(
    context: AssetExecutionContext, snapped_samples: Snapped, params: Params
) -> list[dict[str, Any]]:
    """Checklist counts and wifi measures per station."""
    out = aggregate.station_aggregates(snapped_samples.stations, params.min_sessions)
    context.add_output_metadata({"stations": len(out)})
    return out


@asset
def publish(
    route_aggregates: tuple[list[aggregate.RouteAggregate], dict[str, dict[str, Any]]],
    station_aggregates: list[dict[str, Any]],
    skeleton: Network,
    public: Bucket,
    params: Params,
) -> MaterializeResult:
    """index.json, routes/, stations.json, bins.pmtiles and LICENSE."""
    tippecanoe = shutil.which(params.tippecanoe)
    if tippecanoe is None:
        raise FileNotFoundError(f"{params.tippecanoe} is not on PATH")
    routes, bins = route_aggregates
    updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = write_public(
        public.store(),
        routes,
        bins,
        station_aggregates,
        skeleton,
        updated_at,
        tippecanoe,
    )
    return MaterializeResult(
        metadata={
            "updated_at": updated_at,
            **{k: v for k, v in counts.items() if k != "removed"},
            "removed": MetadataValue.json(counts["removed"]),
        }
    )


ASSETS = [
    raw_sessions,
    trip_shapes,
    skeleton,
    snapped_samples,
    route_aggregates,
    station_aggregates,
    publish,
]


def _bucket(prefix: str, default: str) -> Bucket:
    return Bucket(
        endpoint=EnvVar("S3_ENDPOINT"),
        bucket=os.environ.get(f"{prefix}S3_BUCKET", default),
        region=os.environ.get("S3_REGION", "garage"),
        access_key=EnvVar(f"{prefix}S3_ACCESS_KEY"),
        secret_key=EnvVar(f"{prefix}S3_SECRET_KEY"),
    )


def resources() -> dict[str, Any]:
    env = os.environ.get
    return {
        "raw": _bucket("RAW_", "wifi-raw"),
        "public": _bucket("PUBLIC_", "data.wifi.gtfs.zone"),
        "transitous": Transitous(
            base_url=env("TRANSITOUS_URL", "https://api.transitous.org")
        ),
        "params": Params(
            min_sessions=int(env("WIFI_MIN_SESSIONS", "1")),
            bin_m=float(env("WIFI_BIN_M", "500")),
            **(
                {"excluded_sessions": env("WIFI_EXCLUDED_SESSIONS", "")}
                if env("WIFI_EXCLUDED_SESSIONS")
                else {}
            ),
        ),
    }


wifi_job = define_asset_job("wifi", selection=ASSETS)

hourly = ScheduleDefinition(
    job=wifi_job,
    cron_schedule="40 * * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(
    assets=ASSETS, jobs=[wifi_job], schedules=[hourly], resources=resources()
)
