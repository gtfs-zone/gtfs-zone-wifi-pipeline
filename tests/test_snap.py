"""network and snap: bins along the skeleton, dropped samples, wifi status."""

import copy
from itertools import pairwise

import pytest
from conftest import BROKEN, GOOD, STATION, session
from shapely import LineString

from gtfs_zone_wifi_pipeline.geo import Frame, haversine
from gtfs_zone_wifi_pipeline.network import cut_bins
from gtfs_zone_wifi_pipeline.snap import snap_all, snap_station, snap_trip, wifi_working


def test_bins_are_equal_and_cover_the_edge():
    frame = Frame(52.5, 13.4)
    bins = cut_bins("0.1", LineString([(0, 0), (1240, 0)]), frame, 500)
    assert [b.id for b in bins] == ["0.1.0", "0.1.1"]
    lengths = [sum(haversine(p, q) for p, q in pairwise(b.line)) for b in bins]
    # haversine is spherical, the frame WGS84: ~0.3% apart at this latitude.
    assert all(abs(x - 620) < 5 for x in lengths)
    assert len(cut_bins("0.2", LineString([(0, 0), (80, 0)]), frame, 500)) == 1


def test_one_cluster_for_nearby_trips(network, shapes):
    assert len(network.clusters) == 1
    assert sorted(network.cluster_of) == sorted(shapes)
    assert all(len(e.bins) >= 1 for e in network.clusters[0].edges.values())


def test_good_trip_keeps_every_sample(network, shapes, params):
    s = session(GOOD)
    out = snap_trip(s, shapes[s["trip"]["transitous_trip_id"]], network, params)
    assert out.route_key == "de-DELFI_de%3AVBB%3A11000000%7CR-Bahn%7CFEX%3A_100"
    assert out.feed == "de-DELFI"
    assert out.dropped == {}
    assert len(out.samples) == len(s["samples"])
    assert len(out.tests) == len(s["net_tests"])
    assert all(x.wifi_working for x in out.samples)
    assert len({x.bin for x in out.samples}) >= 10  # ~5.6 km in 500 m bins


def test_outlier_is_dropped_off_line(network, shapes, params):
    s = session(BROKEN)
    out = snap_trip(s, shapes[s["trip"]["transitous_trip_id"]], network, params)
    assert out.dropped == {"off_line": 1}
    assert len(out.samples) == len(s["samples"]) - 1


def test_samples_past_alighting_are_dropped(network, shapes, params):
    s = copy.deepcopy(session(GOOD))
    stop = s["trip"]["alighting"]
    # Alight one stop earlier: samples past Potsdamer Platz fall outside.
    stop.update(lat=52.50922, lon=13.375728)
    out = snap_trip(s, shapes[s["trip"]["transitous_trip_id"]], network, params)
    assert out.dropped["outside_trip"] > 10
    assert out.dropped["net_tests"] >= 1


def test_wifi_working_follows_nearby_wifi_test():
    s = session(BROKEN)
    working = wifi_working(s)
    connected = [x["wifi"] is not None for x in s["samples"]]
    # Connected only while the portal blocks every wifi test.
    assert [w for w, c in zip(working, connected, strict=True) if c] == [False] * sum(
        connected
    )
    assert all(w is False for w, c in zip(working, connected, strict=True) if not c)


def test_connected_without_wifi_test_is_unknown():
    s = copy.deepcopy(session(GOOD))
    s["net_tests"] = []
    assert set(wifi_working(s)) == {None}


def test_station_counts_samples_near_the_stop(params):
    s = copy.deepcopy(session(STATION))
    s["samples"][0].update(lat=s["station"]["lat"] + 0.01)  # ~1.1 km north
    out = snap_station(s, params)
    assert out.station_key == "de-DELFI_de:11000:900003201"
    assert len(out.samples) == len(s["samples"]) - 1
    assert out.dropped["far"] == 1


def test_trip_without_shape_is_skipped(network, params):
    out = snap_all([session(GOOD), session(STATION)], {}, network, params)
    assert out.trips == []
    assert len(out.stations) == 1
    assert out.skipped == {session(GOOD)["session_id"]: "no trip shape"}


@pytest.mark.parametrize("name", [GOOD, BROKEN])
def test_bins_follow_the_trip(network, shapes, params, name):
    """Consecutive samples stay on one edge or move to a neighbouring bin."""
    s = session(name)
    out = snap_trip(s, shapes[s["trip"]["transitous_trip_id"]], network, params)
    ks = [int(x.bin.rsplit(".", 1)[1]) for x in out.samples]
    edges = {x.bin.rsplit(".", 1)[0] for x in out.samples}
    assert len(edges) == 1
    assert all(abs(b - a) <= 1 for a, b in pairwise(ks))
