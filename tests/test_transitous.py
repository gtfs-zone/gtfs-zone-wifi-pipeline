"""transitous: parsing a recorded /api/v6/trip and the cache in wifi-raw."""

import json

import obstore
from conftest import GOOD, recorded, session

from gtfs_zone_wifi_pipeline.config import url_safe
from gtfs_zone_wifi_pipeline.transitous import cache_key, load_shapes, parse, trim

FEX = session(GOOD)["trip"]["transitous_trip_id"]


def test_url_safe():
    assert url_safe("de-DELFI_de:VBB|FEX:_100") == "de-DELFI_de%3AVBB%7CFEX%3A_100"
    assert url_safe("a%b ü") == "a%25b%20%C3%BC"
    assert url_safe("A-z_0.9") == "A-z_0.9"


def test_cache_key():
    assert cache_key(FEX) == "cache/trips/20261108_14%3A19_de-DELFI_3468911239.json"


def test_parse_recorded_trip():
    shape = parse(trim(recorded(FEX)["legs"][0]))
    assert shape.trip_id == FEX
    assert shape.route_name == "FEX"
    assert shape.mode == "REGIONAL_RAIL"
    assert [s.name for s in shape.stops] == [
        "S+U Berlin Hauptbahnhof",
        "S+U Potsdamer Platz Bhf (Berlin)",
        "S Südkreuz Bhf (Berlin)",
        "Flughafen BER",
    ]
    assert len(shape.line) == 490
    lat, lon = shape.line[0]
    assert abs(lat - 52.525) < 0.01 and abs(lon - 13.369) < 0.01


def test_fetches_once_then_reads_cache(buckets, recorder):
    store = buckets[0].store()
    with recorder.client() as http:
        shapes, failed, fetched = load_shapes(store, http, [FEX, FEX])
        assert (list(shapes), failed, fetched) == ([FEX], {}, 1)
        doc = json.loads(bytes(obstore.get(store, cache_key(FEX)).bytes()))
        assert doc["leg"]["tripId"] == FEX
        assert "alternatives" not in doc["leg"]
        again, _, fetched = load_shapes(store, http, [FEX])
    assert fetched == 0
    assert again[FEX] == shapes[FEX]
    assert recorder.calls == [FEX]


def test_failed_fetch_is_reported_and_not_cached(buckets, recorder):
    store = buckets[0].store()
    with recorder.client() as http:
        shapes, failed, _ = load_shapes(store, http, ["20261108_00:00_x_1"])
    assert shapes == {}
    assert "404" in failed["20261108_00:00_x_1"]
    assert list(obstore.list(store, prefix="cache/")) == []
