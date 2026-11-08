"""pipeline: the fixture sessions through every asset into a temp dir."""

import gzip
import json
import shutil

import obstore
import pytest
from conftest import BROKEN, FIXTURES, GOOD, STATION, put_raw, session
from dagster import materialize

from gtfs_zone_wifi_pipeline import publish as publish_mod
from gtfs_zone_wifi_pipeline.config import Params, Transitous
from gtfs_zone_wifi_pipeline.pipeline import definitions

HAVE_TIPPECANOE = shutil.which("tippecanoe") is not None


def run(buckets, recorder, monkeypatch, **params):
    raw, public = buckets
    monkeypatch.setattr(definitions, "client", lambda _t: recorder.client())
    if not HAVE_TIPPECANOE:
        monkeypatch.setattr(publish_mod, "build_pmtiles", lambda _f, _t: b"stub")
        params.setdefault("tippecanoe", "sh")
    result = materialize(
        definitions.ASSETS,
        resources={
            "raw": raw,
            "public": public,
            "transitous": Transitous(base_url="https://transitous.test"),
            "params": Params(
                **{"excluded_sessions": str(FIXTURES / "none-excluded.yaml")} | params
            ),
        },
    )
    assert result.success
    return result, public.store()


def read(store, key):
    return json.loads(bytes(obstore.get(store, key).bytes()))


@pytest.fixture
def published(buckets, recorder, monkeypatch):
    put_raw(buckets[0].store(), [session(n) for n in (GOOD, BROKEN, STATION)])
    return run(buckets, recorder, monkeypatch)


def test_definitions_load():
    definitions.defs.resolve_all_job_defs()
    job = definitions.defs.resolve_job_def("wifi")
    assert {n.name for n in job.nodes} == {a.key.path[-1] for a in definitions.ASSETS}


def test_layout(published):
    _, store = published
    keys = sorted(m["path"] for batch in obstore.list(store) for m in batch)
    assert keys == [
        "LICENSE",
        "bins.pmtiles",
        "index.json",
        "probe/100k.bin",
        "routes/de-DELFI_de%3AVBB%3A11000000%7CR-Bahn%7CFEX%3A_100.json",
        "routes/de-DELFI_de%3AVBB%3A11000000%7CR-Bahn%7CRE2%3ADB_100.json",
        "stations.json",
    ]
    index = read(store, "index.json")
    assert index["license"] == "ODbL-1.0"
    assert [r["name"] for r in index["routes"]] == ["FEX", "RE2"]
    assert index["station_count"] == 1
    fex = read(store, f"routes/{index['routes'][0]['route_key']}.json")
    assert fex["updated_at"] == index["updated_at"]
    assert fex["bins"][0]["wifi_working_share"] == 1.0
    assert len(fex["bins"]) == index["routes"][0]["bin_count"]
    stations = read(store, "stations.json")["stations"]
    assert stations[0]["name"] == "S+U Berlin Hauptbahnhof"
    assert "ODbL" in bytes(obstore.get(store, "LICENSE").bytes()).decode()
    probe = bytes(obstore.get(store, "probe/100k.bin").bytes())
    assert len(probe) == 100 * 1024
    assert len(gzip.compress(probe)) > len(probe)


def test_metadata(published):
    result, _ = published
    meta = {
        e.asset_key.path[-1]: e.materialization.metadata
        for e in result.get_asset_materialization_events()
    }
    assert meta["raw_sessions"]["sessions"].value == 3
    assert meta["trip_shapes"]["fetched"].value == 2
    assert meta["snapped_samples"]["dropped"].value == {
        session(BROKEN)["session_id"]: {"off_line": 1}
    }


def test_rerun_uses_the_cache_and_drops_stale_routes(
    buckets, recorder, monkeypatch, tmp_path
):
    raw = buckets[0].store()
    put_raw(raw, [session(n) for n in (GOOD, BROKEN, STATION)])
    run(buckets, recorder, monkeypatch)
    assert len(recorder.calls) == 2
    excluded = tmp_path / "excluded.yaml"
    excluded.write_text(f"excluded:\n  - id: {session(BROKEN)['session_id']}\n")
    _, store = run(buckets, recorder, monkeypatch, excluded_sessions=str(excluded))
    assert len(recorder.calls) == 2
    index = read(store, "index.json")
    assert [r["name"] for r in index["routes"]] == ["FEX"]
    routes = [
        m["path"] for batch in obstore.list(store, prefix="routes/") for m in batch
    ]
    assert len(routes) == 1


def test_no_sessions_publishes_empty_files(buckets, recorder, monkeypatch):
    _, store = run(buckets, recorder, monkeypatch)
    index = read(store, "index.json")
    assert index["routes"] == []
    assert index["bin_count"] == 0
    assert read(store, "stations.json")["stations"] == []
    keys = sorted(m["path"] for batch in obstore.list(store) for m in batch)
    assert keys == ["LICENSE", "index.json", "probe/100k.bin", "stations.json"]


@pytest.mark.skipif(not HAVE_TIPPECANOE, reason="tippecanoe is not installed")
def test_pmtiles_reads_back(published, tmp_path):
    from pmtiles.reader import MmapSource, Reader

    _, store = published
    path = tmp_path / "bins.pmtiles"
    path.write_bytes(bytes(obstore.get(store, "bins.pmtiles").bytes()))
    with path.open("rb") as f:
        reader = Reader(MmapSource(f))
        header = reader.header()
        meta = reader.metadata()
    assert header["max_zoom"] == 14
    assert [layer["id"] for layer in meta["vector_layers"]] == ["bins"]
    fields = meta["vector_layers"][0]["fields"]
    assert {"bin_id", "routes", "wifi_working_share", "session_count"} <= set(fields)
    assert 13.1 < header["min_lon_e7"] / 1e7 < header["max_lon_e7"] / 1e7 < 13.5
