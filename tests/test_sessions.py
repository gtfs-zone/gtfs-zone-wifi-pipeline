"""sessions: reading raw/, validation report, exclusion and duplicates."""

import copy

import obstore
from conftest import BROKEN, GOOD, SESSIONS, STATION, put_raw, session

from gtfs_zone_wifi_pipeline.sessions import errors, load_excluded, read_raw, validators


def test_fixtures_are_valid():
    checks = validators()
    for path in sorted(SESSIONS.glob("*.json")):
        assert errors(checks, session(path.name)) == [], path.name


def test_pinned_schema_versions():
    assert sorted(validators()) == [1]


def test_reads_valid_sessions_sorted(buckets):
    store = buckets[0].store()
    put_raw(store, [session(n) for n in (GOOD, BROKEN, STATION)])
    out = read_raw(store, set())
    ids = [s["session_id"] for s in out.sessions]
    assert ids == sorted(ids)
    assert len(ids) == 3
    assert out.invalid == []


def test_invalid_objects_are_reported_not_raised(buckets):
    store = buckets[0].store()
    bad = copy.deepcopy(session(GOOD))
    bad["samples"][0]["imei"] = "123"
    put_raw(store, [bad, session(STATION)])
    obstore.put(store, "raw/v1/2026/11/08/broken.json", b"{not json")
    obstore.put(store, "ids/whatever", b"raw/v1/x.json")
    out = read_raw(store, set())
    assert [s["kind"] for s in out.sessions] == ["station"]
    keys = sorted(e["key"] for e in out.invalid)
    assert keys == [
        "raw/v1/2026/11/08/3f1c2a9e-6b7d-4e2f-9a1b-8c5d4e3f2a1b.json",
        "raw/v1/2026/11/08/broken.json",
    ]
    assert any("imei" in msg for e in out.invalid for msg in e["errors"])


def test_unknown_schema_version_is_invalid(buckets):
    store = buckets[0].store()
    s = session(STATION)
    s["schema_version"] = 9
    put_raw(store, [s])
    out = read_raw(store, set())
    assert out.sessions == []
    assert "no schema for 9" in out.invalid[0]["errors"][0]


def test_excluded_and_duplicate_sessions(buckets, tmp_path):
    store = buckets[0].store()
    good, station = session(GOOD), session(STATION)
    put_raw(store, [good, station])
    put_raw(store, [station], day="2026/11/09")
    excluded = tmp_path / "excluded.yaml"
    excluded.write_text(f"excluded:\n  - id: {good['session_id']}\n    reason: test\n")
    out = read_raw(store, load_excluded(excluded))
    assert [s["session_id"] for s in out.sessions] == [station["session_id"]]
    assert out.excluded == [good["session_id"]]
    assert out.duplicates == [f"raw/v1/2026/11/09/{station['session_id']}.json"]


def test_repo_exclusion_file_loads():
    from gtfs_zone_wifi_pipeline.config import DEFAULT_EXCLUDED

    assert load_excluded(DEFAULT_EXCLUDED) == set()
