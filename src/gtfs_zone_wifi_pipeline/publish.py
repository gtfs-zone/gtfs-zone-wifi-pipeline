"""Writes the public artifacts to data.wifi.gtfs.zone.

Layout (docs/overview.md): index.json, routes/<route_key>.json,
stations.json, bins.pmtiles (left out when no bin is published) and
LICENSE. index.json is written last, so its
updated_at only moves once everything it lists is in place. Route files no
longer listed are removed.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import obstore

from gtfs_zone_wifi_pipeline.aggregate import bin_line
from gtfs_zone_wifi_pipeline.store import put as store_put

if TYPE_CHECKING:
    from obstore.store import ObjectStore

    from gtfs_zone_wifi_pipeline.aggregate import RouteAggregate
    from gtfs_zone_wifi_pipeline.network import Network

LICENSE_ID = "ODbL-1.0"
ATTRIBUTION = (
    "wifi.gtfs.zone contributors (ODbL); trip shapes and stops via Transitous"
    " and its GTFS sources; OpenStreetMap contributors"
)
LICENSE_TEXT = f"""\
wifi.gtfs.zone data

The files in this bucket are made available under the Open Database License
(ODbL) v1.0: https://opendatacommons.org/licenses/odbl/1-0/
Any rights in individual contents of the database are licensed under the
Database Contents License: https://opendatacommons.org/licenses/dbcl/1-0/

Attribution: {ATTRIBUTION}.
Transitous sources: https://transitous.org/sources/
OpenStreetMap: https://www.openstreetmap.org/copyright
"""
_TYPES = {
    ".json": "application/json",
    ".pmtiles": "application/vnd.pmtiles",
    "": "text/plain; charset=utf-8",
}
CACHE_CONTROL = "public, max-age=300"


def put(store: ObjectStore, key: str, data: bytes) -> None:
    content_type = _TYPES.get(Path(key).suffix, "application/octet-stream")
    store_put(store, key, data, content_type, CACHE_CONTROL)


def _json(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def bin_features(
    network: Network, bins: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """GeoJSON line features of the published bins, with flat properties."""
    geoms = network.bins()
    out = []
    for bid, values in sorted(bins.items()):
        props = {
            "bin_id": bid,
            **{
                k: v
                for k, v in values.items()
                if k not in ("routes", "cell", "reports") and v is not None
            },
            "routes": ",".join(values["routes"]),
            "cell": json.dumps(values["cell"], separators=(",", ":")),
            "reports": json.dumps(values["reports"], separators=(",", ":")),
        }
        out.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": bin_line(geoms[bid])},
                "properties": props,
            }
        )
    return out


def build_pmtiles(features: list[dict[str, Any]], tippecanoe: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "bins.geojson"
        out = Path(tmp) / "bins.pmtiles"
        src.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        subprocess.run(
            [
                tippecanoe,
                "--output",
                str(out),
                "--layer",
                "bins",
                "--name",
                "wifi.gtfs.zone bins",
                "--attribution",
                ATTRIBUTION,
                "--minimum-zoom",
                "0",
                "--maximum-zoom",
                "14",
                "--no-tile-size-limit",
                "--no-feature-limit",
                "--force",
                "--quiet",
                str(src),
            ],
            check=True,
        )
        return out.read_bytes()


def publish(
    store: ObjectStore,
    routes: list[RouteAggregate],
    bins: dict[str, dict[str, Any]],
    stations: list[dict[str, Any]],
    network: Network,
    updated_at: str,
    tippecanoe: str,
) -> dict[str, Any]:
    """Writes every artifact; returns counts for the run's metadata."""
    keys = set()
    for r in routes:
        key = f"routes/{r.route_key}.json"
        put(store, key, _json({"updated_at": updated_at, **asdict(r)}))
        keys.add(key)
    stale = sorted(
        meta["path"]
        for batch in obstore.list(store, prefix="routes/")
        for meta in batch
        if meta["path"] not in keys
    )
    for key in stale:
        obstore.delete(store, key)
    put(store, "stations.json", _json({"updated_at": updated_at, "stations": stations}))
    features = bin_features(network, bins)
    if features:
        put(store, "bins.pmtiles", build_pmtiles(features, tippecanoe))
    else:
        # tippecanoe fails on no features; index.json then has bin_count 0.
        with contextlib.suppress(FileNotFoundError):
            obstore.delete(store, "bins.pmtiles")
    put(store, "LICENSE", LICENSE_TEXT.encode())
    index = {
        "updated_at": updated_at,
        "license": LICENSE_ID,
        "attribution": ATTRIBUTION,
        "routes": [
            {
                "route_key": r.route_key,
                "name": r.name,
                "mode": r.mode,
                "agency": r.agency,
                "feed": r.feed,
                "session_count": r.session_count,
                "bin_count": len(r.bins),
            }
            for r in routes
        ],
        "station_count": len(stations),
        "bin_count": len(features),
    }
    put(store, "index.json", _json(index))
    return {
        "routes": len(routes),
        "stations": len(stations),
        "bins": len(features),
        "removed": stale,
    }
