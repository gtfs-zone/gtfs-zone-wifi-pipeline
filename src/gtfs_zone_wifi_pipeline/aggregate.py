"""Per bin, route and station aggregates of the snapped sessions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING, Any

from shapely import LineString, Point

from gtfs_zone_wifi_pipeline.geo import haversine

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gtfs_zone_wifi_pipeline.network import Bin, Network
    from gtfs_zone_wifi_pipeline.snap import (
        NetTest,
        Sample,
        Snapped,
        StationSession,
        TripSession,
    )
    from gtfs_zone_wifi_pipeline.transitous import TripShape

REPORT_FIELDS = ("outlets", "wifi", "portal", "data_cap", "vpn_blocked")


def _median(xs: list[float]) -> float | None:
    return round(float(median(xs)), 1) if xs else None


def _share(yes: int, n: int) -> float | None:
    return round(yes / n, 3) if n else None


def measures(samples: list[Sample], tests: list[NetTest]) -> dict[str, Any]:
    """Wifi, cell and network test values of a set of samples and tests."""
    judged = [s.wifi_working for s in samples if s.wifi_working is not None]
    cell: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        if s.cell_tech:
            cell[s.cell_tech].append(s)
    ok = [t for t in tests if t.reachable]
    return {
        "samples": len(samples),
        "wifi_connected_share": _share(
            sum(s.wifi_connected for s in samples), len(samples)
        ),
        "wifi_working_share": _share(sum(judged), len(judged)),
        "wifi_rssi_median": _median(
            [s.wifi_rssi for s in samples if s.wifi_rssi is not None]
        ),
        "net_tests": len(tests),
        "net_test_success_rate": _share(len(ok), len(tests)),
        "latency_ms_median": _median(
            [t.latency_ms for t in ok if t.latency_ms is not None]
        ),
        "captive_portal_tests": sum(t.captive_portal is True for t in tests),
        "no_cell_samples": sum(s.cell_tech is None for s in samples),
        "cell": {
            tech: {
                "samples": len(group),
                "level_dbm_median": _median(
                    [s.cell_dbm for s in group if s.cell_dbm is not None]
                ),
                "level_0_4_median": _median(
                    [s.cell_level for s in group if s.cell_level is not None]
                ),
            }
            for tech, group in sorted(cell.items())
        },
    }


def report_counts(trips: Iterable[TripSession]) -> dict[str, dict[str, int]]:
    """Per report field, how many sessions gave each answer."""
    out: dict[str, Counter[str]] = {f: Counter() for f in REPORT_FIELDS}
    for trip in trips:
        for f in REPORT_FIELDS:
            value = trip.reports.get(f)
            if value is not None:
                out[f][str(value).lower()] += 1
    return {f: dict(sorted(c.items())) for f, c in out.items()}


def bin_values(trips: list[TripSession]) -> dict[str, dict[str, Any]]:
    """Per bin id: session count, measures and report counts of the trips."""
    samples: dict[str, list[Sample]] = defaultdict(list)
    tests: dict[str, list[NetTest]] = defaultdict(list)
    sessions: dict[str, dict[str, TripSession]] = defaultdict(dict)
    for trip in trips:
        for s in trip.samples:
            assert s.bin is not None
            samples[s.bin].append(s)
            sessions[s.bin][trip.session_id] = trip
        for t in trip.tests:
            assert t.bin is not None
            tests[t.bin].append(t)
    return {
        b: {
            "session_count": len(sessions[b]),
            **measures(samples[b], tests[b]),
            "reports": report_counts(sessions[b].values()),
        }
        for b in sorted(samples)
    }


@dataclass(frozen=True)
class RouteAggregate:
    route_key: str
    route_id: str
    name: str
    mode: str | None
    agency: str | None
    feed: str | None
    session_count: int
    length_m: float
    stops: list[dict[str, Any]]
    bins: list[dict[str, Any]]  # in order along the route
    reports: dict[str, dict[str, int]]


def _reference(trips: list[TripSession], shapes: dict[str, TripShape]) -> TripShape:
    """The route's longest trip shape (ties by trip id): bins are ordered along it."""

    def size(s: TripShape) -> float:
        return sum(haversine(a, b) for a, b in pairwise(s.line))

    return max((shapes[t.trip_id] for t in trips), key=lambda s: (size(s), s.trip_id))


def route_aggregates(
    snapped: Snapped,
    network: Network,
    shapes: dict[str, TripShape],
    min_sessions: int,
) -> tuple[list[RouteAggregate], dict[str, dict[str, Any]]]:
    """Per route, its bins in order with their values; and every bin's values
    over all routes, with the route keys that pass it. Bins and routes under
    min_sessions sessions are left out."""
    by_route: dict[str, list[TripSession]] = defaultdict(list)
    for trip in snapped.trips:
        by_route[trip.route_key].append(trip)
    all_bins = network.bins()
    routes = []
    bin_routes: dict[str, set[str]] = defaultdict(set)
    for key, trips in sorted(by_route.items()):
        sessions = {t.session_id for t in trips if t.samples}
        if len(sessions) < min_sessions:
            continue
        ref = _reference(trips, shapes)
        frame = network.clusters[network.cluster_of[ref.trip_id]].frame
        line = LineString(frame.xy(ref.line))

        def along(
            pts: list[tuple[float, float]], line: LineString = line
        ) -> list[float]:
            return [round(float(line.project(Point(p))), 1) for p in pts]

        rows = []
        for bid, values in bin_values(trips).items():
            if values["session_count"] < min_sessions:
                continue
            b = all_bins[bid]
            ends = along(frame.xy([b.line[0], b.line[-1]]))
            rows.append(
                {"bin_id": bid, "from_m": min(ends), "to_m": max(ends), **values}
            )
            bin_routes[bid].add(key)
        if not rows:
            continue
        rows.sort(key=lambda r: (r["from_m"], r["to_m"], r["bin_id"]))
        first = trips[0]
        routes.append(
            RouteAggregate(
                route_key=key,
                route_id=first.route_id,
                name=first.route_name,
                mode=first.mode,
                agency=first.agency,
                feed=first.feed,
                session_count=len(sessions),
                length_m=round(line.length, 1),
                stops=[
                    {"stop_id": s.stop_id, "name": s.name, "m": m}
                    for s, m in zip(
                        ref.stops,
                        along(frame.xy([(s.lat, s.lon) for s in ref.stops])),
                        strict=True,
                    )
                ],
                bins=rows,
                reports=report_counts(trips),
            )
        )
    overall = {
        bid: {"routes": sorted(bin_routes[bid]), **values}
        for bid, values in bin_values(snapped.trips).items()
        if bid in bin_routes
    }
    return routes, overall


def station_aggregates(
    stations: list[StationSession], min_sessions: int
) -> list[dict[str, Any]]:
    """Per station: checklist answer counts and the wifi measures nearby."""
    by_key: dict[str, list[StationSession]] = defaultdict(list)
    for s in stations:
        by_key[s.station_key].append(s)
    out = []
    for key, group in sorted(by_key.items()):
        if len(group) < min_sessions:
            continue
        first = min(group, key=lambda s: s.session_id)
        checklist: dict[str, dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
        for s in group:
            for item, answer in s.checklist.items():
                if answer is not None:
                    checklist[item]["yes" if answer else "no"] += 1
        out.append(
            {
                "station_key": key,
                "name": first.name,
                "lat": first.lat,
                "lon": first.lon,
                "session_count": len(group),
                "checklist": dict(sorted(checklist.items())),
                **measures(
                    [x for s in group for x in s.samples],
                    [x for s in group for x in s.tests],
                ),
            }
        )
    return out


def bin_line(b: Bin) -> list[list[float]]:
    """GeoJSON [lon, lat] coordinates of a bin."""
    return [[round(lon, 6), round(lat, 6)] for lat, lon in b.line]
