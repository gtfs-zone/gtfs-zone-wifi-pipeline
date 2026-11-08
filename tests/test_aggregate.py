"""aggregate: values per bin, route order, minimum session count, stations."""

import copy

import pytest
from conftest import BROKEN, GOOD, STATION, session

from gtfs_zone_wifi_pipeline import aggregate
from gtfs_zone_wifi_pipeline.snap import NetTest, Sample, snap_all


def sample(**kw):
    base = dict(
        t="2026-11-08T00:00:00Z",
        bin="0.1.0",
        wifi_connected=True,
        wifi_working=True,
        wifi_rssi=-60,
        cell_tech="lte",
        cell_dbm=-100,
        cell_level=3,
    )
    return Sample(**(base | kw))


def test_measures():
    samples = [
        sample(),
        sample(wifi_rssi=-70, wifi_working=False),
        sample(
            wifi_rssi=-80,
            wifi_working=None,
            cell_tech="nr",
            cell_dbm=-90,
            cell_level=1,
        ),
        sample(
            wifi_connected=False, wifi_working=False, wifi_rssi=None, cell_tech=None
        ),
    ]
    tests = [
        NetTest("t", "0.1.0", True, False, 40.0),
        NetTest("t", "0.1.0", True, False, 60.0),
        NetTest("t", "0.1.0", False, True, None),
    ]
    m = aggregate.measures(samples, tests)
    assert m["samples"] == 4
    assert m["wifi_connected_share"] == 0.75
    assert m["wifi_working_share"] == 0.333
    assert m["wifi_rssi_median"] == -70.0
    assert m["net_test_success_rate"] == 0.667
    assert m["latency_ms_median"] == 50.0
    assert m["captive_portal_tests"] == 1
    assert m["no_cell_samples"] == 1
    assert m["cell_level_0_4_median"] == 3.0
    assert m["cell"] == {
        "lte": {"samples": 2, "level_dbm_median": -100.0, "level_0_4_median": 3.0},
        "nr": {"samples": 1, "level_dbm_median": -90.0, "level_0_4_median": 1.0},
    }


def test_empty_measures_are_null():
    m = aggregate.measures([], [])
    assert m["wifi_working_share"] is None
    assert m["latency_ms_median"] is None


@pytest.fixture
def snapped(network, shapes, params):
    return snap_all(
        [session(n) for n in (GOOD, BROKEN, STATION)], shapes, network, params
    )


def test_routes_in_order_with_stops(snapped, network, shapes):
    routes, bins = aggregate.route_aggregates(snapped, network, shapes, 1)
    assert [r.name for r in routes] == ["FEX", "RE2"]
    fex = routes[0]
    assert fex.session_count == 1
    assert fex.stops[0]["name"] == "S+U Berlin Hauptbahnhof"
    assert fex.stops[0]["m"] == 0.0
    froms = [b["from_m"] for b in fex.bins]
    assert froms == sorted(froms)
    # Hbf to Suedkreuz is ~5.6 km of the 27 km shape.
    assert 5000 < fex.bins[-1]["to_m"] < 6200
    assert fex.reports["outlets"] == {"working": 1}
    assert fex.reports["data_cap"] == {"false": 1}
    for b in fex.bins:
        assert bins[b["bin_id"]]["routes"] == [fex.route_key]
        assert b["reports"]["portal"] == {"click": 1}


def test_broken_route_values(snapped, network, shapes):
    routes, _ = aggregate.route_aggregates(snapped, network, shapes, 1)
    re2 = routes[1]
    assert re2.reports["wifi"] == {"broken": 1}
    first = re2.bins[0]
    assert first["wifi_working_share"] == 0.0
    tests = sum(b["net_tests"] for b in re2.bins)
    assert tests == len(session(BROKEN)["net_tests"])


def test_min_sessions_hides_thin_bins(snapped, network, shapes):
    routes, bins = aggregate.route_aggregates(snapped, network, shapes, 2)
    assert routes == []
    assert bins == {}


def test_shared_bins_count_both_sessions(network, shapes, params):
    good = session(GOOD)
    again = copy.deepcopy(good)
    again["session_id"] = "11111111-2222-4333-8444-555555555555"
    again["reports"]["outlets"] = "none"
    snapped = snap_all([good, again], shapes, network, params)
    routes, bins = aggregate.route_aggregates(snapped, network, shapes, 2)
    (fex,) = routes
    assert fex.session_count == 2
    assert all(b["session_count"] == 2 for b in fex.bins)
    assert fex.reports["outlets"] == {"none": 1, "working": 1}
    assert set(bins) == {b["bin_id"] for b in fex.bins}


def test_station_aggregates(snapped):
    (st,) = aggregate.station_aggregates(snapped.stations, 1)
    assert st["station_key"] == "de-DELFI_de:11000:900003201"
    assert st["checklist"]["needed_checkbox"] == {"yes": 1, "no": 0}
    assert st["session_count"] == 1
    assert st["net_test_success_rate"] == 0.5
    assert aggregate.station_aggregates(snapped.stations, 2) == []
