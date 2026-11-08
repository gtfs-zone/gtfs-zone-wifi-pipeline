import json
from pathlib import Path

import httpx
import obstore
import pytest

from gtfs_zone_wifi_pipeline.config import Bucket, Params, url_safe
from gtfs_zone_wifi_pipeline.network import build_network
from gtfs_zone_wifi_pipeline.transitous import TripShape, parse, trim

FIXTURES = Path(__file__).parent / "fixtures"
SESSIONS = FIXTURES / "sessions"
TRANSITOUS = FIXTURES / "transitous"
GOOD = "trip-good-wifi.json"
BROKEN = "trip-broken-wifi-portal.json"
STATION = "station-checklist.json"


def session(name: str) -> dict:
    return json.loads((SESSIONS / name).read_text())


def recorded(trip_id: str) -> dict:
    return json.loads((TRANSITOUS / f"{url_safe(trip_id)}.json").read_text())


class Recorder:
    """Serves the recorded /api/v6/trip responses; counts requests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        trip_id = request.url.params["tripId"]
        self.calls.append(trip_id)
        path = TRANSITOUS / f"{url_safe(trip_id)}.json"
        if request.url.path != "/api/v6/trip" or not path.exists():
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, content=path.read_bytes())

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self), base_url="https://transitous.test"
        )


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def buckets(tmp_path):
    root = f"file://{tmp_path / 'buckets'}"
    return Bucket(endpoint=root, bucket="wifi-raw"), Bucket(
        endpoint=root, bucket="data.wifi.gtfs.zone"
    )


def put_raw(store, sessions: list[dict], day: str = "2026/11/08") -> None:
    for s in sessions:
        key = f"raw/v{s['schema_version']}/{day}/{s['session_id']}.json"
        obstore.put(store, key, json.dumps(s).encode())


@pytest.fixture(scope="session")
def params():
    return Params(excluded_sessions=str(FIXTURES / "none-excluded.yaml"))


@pytest.fixture(scope="session")
def shapes() -> dict[str, TripShape]:
    out = {}
    for name in (GOOD, BROKEN):
        trip_id = session(name)["trip"]["transitous_trip_id"]
        out[trip_id] = parse(trim(recorded(trip_id)["legs"][0]))
    return out


@pytest.fixture(scope="session")
def network(shapes, params):
    return build_network(shapes, params)
