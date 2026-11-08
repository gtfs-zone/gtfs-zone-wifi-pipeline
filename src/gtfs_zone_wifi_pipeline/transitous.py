"""Trip geometry and stops from Transitous /api/v6/trip, cached in wifi-raw."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import obstore
import polyline

from gtfs_zone_wifi_pipeline.config import url_safe
from gtfs_zone_wifi_pipeline.store import put

if TYPE_CHECKING:
    import httpx
    from obstore.store import ObjectStore

CACHE_PREFIX = "cache/trips/"
_LEG_FIELDS = (
    "tripId",
    "routeId",
    "routeShortName",
    "routeLongName",
    "displayName",
    "mode",
    "routeType",
    "agencyId",
    "agencyName",
    "headsign",
    "legGeometry",
)
_STOP_FIELDS = ("stopId", "parentId", "name", "lat", "lon")


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class TripShape:
    trip_id: str
    route_id: str
    route_name: str
    mode: str | None
    line: tuple[tuple[float, float], ...]  # (lat, lon)
    stops: tuple[Stop, ...]


def cache_key(trip_id: str) -> str:
    return f"{CACHE_PREFIX}{url_safe(trip_id)}.json"


def trim(leg: dict[str, Any]) -> dict[str, Any]:
    """The leg fields the pipeline uses, with stops cut to their place fields."""
    out = {k: leg.get(k) for k in _LEG_FIELDS}
    out["stops"] = [
        {k: s.get(k) for k in _STOP_FIELDS}
        for s in [leg["from"], *leg.get("intermediateStops", []), leg["to"]]
    ]
    return out


def parse(leg: dict[str, Any]) -> TripShape:
    """A trimmed leg as a TripShape."""
    geom = leg["legGeometry"]
    line = tuple(
        (float(lat), float(lon))
        for lat, lon in polyline.decode(geom["points"], geom.get("precision", 6))
    )
    return TripShape(
        trip_id=leg["tripId"],
        route_id=leg["routeId"],
        route_name=leg.get("routeShortName")
        or leg.get("displayName")
        or leg.get("routeLongName")
        or "",
        mode=leg.get("mode"),
        line=line,
        stops=tuple(
            Stop(s["stopId"], s["name"], s["lat"], s["lon"]) for s in leg["stops"]
        ),
    )


def fetch_leg(http: httpx.Client, trip_id: str) -> dict[str, Any]:
    resp = http.get("/api/v6/trip", params={"tripId": trip_id})
    resp.raise_for_status()
    legs = [leg for leg in resp.json().get("legs", []) if leg.get("legGeometry")]
    if not legs:
        raise ValueError("no leg with a geometry")
    return trim(legs[0])


def load_shapes(
    store: ObjectStore, http: httpx.Client, trip_ids: list[str]
) -> tuple[dict[str, TripShape], dict[str, str], int]:
    """Shapes by trip id from the cache, fetching the missing ones.

    Returns the shapes, the error per trip that could not be fetched (retried
    on the next run) and how many were fetched.
    """
    shapes: dict[str, TripShape] = {}
    failed: dict[str, str] = {}
    fetched = 0
    for trip_id in sorted(set(trip_ids)):
        key = cache_key(trip_id)
        try:
            leg = json.loads(bytes(obstore.get(store, key).bytes()))["leg"]
        except FileNotFoundError:
            try:
                leg = fetch_leg(http, trip_id)
            except Exception as exc:
                failed[trip_id] = repr(exc)
                continue
            doc = {"fetched_at": datetime.now(UTC).isoformat(), "leg": leg}
            put(store, key, json.dumps(doc).encode(), "application/json")
            fetched += 1
        shapes[trip_id] = parse(leg)
    return shapes, failed, fetched
