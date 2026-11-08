"""Samples and network tests placed on the trip shape and its skeleton bin.

Trip samples are projected onto the trip's shape; those further than
max_offline_m from it, or more than stop_slack_m before the boarding stop or
past the alighting stop along it, are dropped. The rest take the bin of the
skeleton edge the shape is matched to at that point. Station samples count within
station_radius_m of the picked stop. A network test takes the place of the
sample nearest in time, if that sample was kept.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import shapely
from shapely import Point
from shapely.ops import substring

from gtfs_zone_wifi_pipeline.config import url_safe
from gtfs_zone_wifi_pipeline.geo import haversine
from gtfs_zone_wifi_pipeline.network import bin_of, trip_path

if TYPE_CHECKING:
    from gtfs_zone_wifi_pipeline.config import Params
    from gtfs_zone_wifi_pipeline.network import Network
    from gtfs_zone_wifi_pipeline.sessions import Session
    from gtfs_zone_wifi_pipeline.transitous import TripShape

# A net test belongs to the sample nearest in time within TEST_MATCH_S; a
# connected sample is working or not by the wifi test within WIFI_TEST_S.
TEST_MATCH_S = 30.0
WIFI_TEST_S = 90.0


def _t(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


@dataclass(frozen=True)
class Sample:
    t: str
    bin: str | None
    wifi_connected: bool
    wifi_working: bool | None  # None: connected, no wifi test near in time
    wifi_rssi: int | None
    cell_tech: str | None
    cell_dbm: int | None
    cell_level: int | None


@dataclass(frozen=True)
class NetTest:
    t: str
    bin: str | None
    reachable: bool
    captive_portal: bool | None
    latency_ms: float | None


@dataclass
class TripSession:
    session_id: str
    route_key: str
    route_id: str
    trip_id: str
    route_name: str
    mode: str | None
    agency: str | None
    feed: str | None
    reports: dict[str, Any]
    samples: list[Sample] = field(default_factory=list)
    tests: list[NetTest] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)


@dataclass
class StationSession:
    session_id: str
    station_key: str
    name: str
    lat: float
    lon: float
    checklist: dict[str, bool | None]
    samples: list[Sample] = field(default_factory=list)
    tests: list[NetTest] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)


@dataclass
class Snapped:
    trips: list[TripSession] = field(default_factory=list)
    stations: list[StationSession] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # session_id -> reason


def wifi_working(session: Session) -> list[bool | None]:
    """Per sample: no wifi is False; connected follows the wifi test nearest
    in time within WIFI_TEST_S (reachable without a portal), else None."""
    tests = sorted(
        (_t(n["t"]), n["reachable"] and n["captive_portal"] is not True)
        for n in session["net_tests"]
        if n["transport"] == "wifi"
    )
    times = [t for t, _ in tests]
    out: list[bool | None] = []
    for s in session["samples"]:
        if s["wifi"] is None:
            out.append(False)
            continue
        t = _t(s["t"])
        i = bisect_left(times, t)
        near = [k for k in (i - 1, i) if 0 <= k < len(times)]
        k = min(near, key=lambda k: abs(times[k] - t), default=None)
        out.append(
            tests[k][1] if k is not None and abs(times[k] - t) <= WIFI_TEST_S else None
        )
    return out


def _sample(raw: dict[str, Any], working: bool | None, bin_id: str | None) -> Sample:
    wifi, cell = raw["wifi"], raw["cell"]
    return Sample(
        t=raw["t"],
        bin=bin_id,
        wifi_connected=wifi is not None,
        wifi_working=working,
        wifi_rssi=wifi["rssi"] if wifi else None,
        cell_tech=cell["tech"] if cell else None,
        cell_dbm=cell["level_dbm"] if cell else None,
        cell_level=cell["level_0_4"] if cell else None,
    )


def _tests(
    session: Session, kept: list[bool], bins: list[str | None]
) -> tuple[list[NetTest], int]:
    """Net tests at the kept sample nearest in time; and how many were dropped."""
    times = [_t(s["t"]) for s in session["samples"]]
    order = sorted(range(len(times)), key=times.__getitem__)
    sorted_t = [times[i] for i in order]
    out, dropped = [], 0
    for n in session["net_tests"]:
        t = _t(n["t"])
        i = bisect_left(sorted_t, t)
        near = [order[k] for k in (i - 1, i) if 0 <= k < len(order)]
        j = min(near, key=lambda j: abs(times[j] - t), default=None)
        if j is None or abs(times[j] - t) > TEST_MATCH_S or not kept[j]:
            dropped += 1
            continue
        out.append(
            NetTest(
                t=n["t"],
                bin=bins[j],
                reachable=n["reachable"],
                captive_portal=n["captive_portal"],
                latency_ms=n["latency_ms"],
            )
        )
    return out, dropped


def snap_trip(
    session: Session, shape: TripShape, network: Network, params: Params
) -> TripSession:
    trip = session["trip"]
    cluster = network.clusters[network.cluster_of[shape.trip_id]]
    frame = cluster.frame
    path = trip_path(cluster, frame.xy(shape.line), params.brush_m)
    line = path.line
    b, a = (
        frame.xy([(trip[k]["lat"], trip[k]["lon"])])[0]
        for k in ("boarding", "alighting")
    )
    m_b = float(line.project(Point(b)))
    m_a = m_b + float(substring(line, m_b, line.length).project(Point(a)))

    raw = session["samples"]
    pts = shapely.points(
        np.array(frame.xy([(s["lat"], s["lon"]) for s in raw])).reshape(-1, 2)
    )
    m = shapely.line_locate_point(line, pts) if raw else np.array([])
    d = shapely.distance(line, pts) if raw else np.array([])
    working = wifi_working(session)
    dropped: Counter[str] = Counter()
    kept, bins, samples = [], [], []
    for s, mi, di, w in zip(raw, m, d, working, strict=True):
        bin_ = None
        if di > params.max_offline_m:
            dropped["off_line"] += 1
        elif mi < m_b - params.stop_slack_m or mi > m_a + params.stop_slack_m:
            dropped["outside_trip"] += 1
        elif (bin_ := bin_of(cluster, path, float(mi))) is None:
            dropped["no_edge"] += 1
        kept.append(bin_ is not None)
        bins.append(bin_.id if bin_ else None)
        if bin_ is not None:
            samples.append(_sample(s, w, bin_.id))
    tests, lost = _tests(session, kept, bins)
    if lost:
        dropped["net_tests"] = lost
    gtfs = trip.get("gtfs") or {}
    return TripSession(
        session_id=session["session_id"],
        route_key=url_safe(trip["transitous_route_id"]),
        route_id=trip["transitous_route_id"],
        trip_id=shape.trip_id,
        route_name=trip["route_name"],
        mode=trip.get("mode"),
        agency=trip.get("agency_name"),
        feed=gtfs.get("feed") or trip["transitous_route_id"].split("_", 1)[0],
        reports=session.get("reports") or {},
        samples=samples,
        tests=tests,
        dropped=dict(dropped),
    )


def station_key(station: dict[str, Any]) -> str:
    return station.get("transitous_parent_id") or station["transitous_stop_id"]


def snap_station(session: Session, params: Params) -> StationSession:
    st = session["station"]
    working = wifi_working(session)
    kept = [
        haversine((s["lat"], s["lon"]), (st["lat"], st["lon"]))
        <= params.station_radius_m
        for s in session["samples"]
    ]
    tests, lost = _tests(session, kept, [None] * len(kept))
    dropped = {"far": kept.count(False)} if False in kept else {}
    if lost:
        dropped["net_tests"] = lost
    return StationSession(
        session_id=session["session_id"],
        station_key=station_key(st),
        name=st["name"],
        lat=st["lat"],
        lon=st["lon"],
        checklist=session["station_checklist"],
        samples=[
            _sample(s, w, None)
            for s, w, k in zip(session["samples"], working, kept, strict=True)
            if k
        ],
        tests=tests,
        dropped=dropped,
    )


def snap_all(
    sessions: list[Session],
    shapes: dict[str, TripShape],
    network: Network,
    params: Params,
) -> Snapped:
    out = Snapped()
    for session in sessions:
        sid = session["session_id"]
        if session["kind"] == "station":
            out.stations.append(snap_station(session, params))
            continue
        shape = shapes.get(session["trip"]["transitous_trip_id"])
        if shape is None:
            out.skipped[sid] = "no trip shape"
            continue
        out.trips.append(snap_trip(session, shape, network, params))
    return out
