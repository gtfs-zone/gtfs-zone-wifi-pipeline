"""Bucket and pipeline settings, as Dagster resources."""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

from dagster import ConfigurableResource

if TYPE_CHECKING:
    from obstore.store import ObjectStore

VERSION = version("gtfs-zone-wifi-pipeline")
USER_AGENT = (
    f"gtfs-zone-wifi/{VERSION} (+https://github.com/gtfs-zone/gtfs-zone-wifi-app)"
)
# Repo checkout and image both keep config/ next to src/.
DEFAULT_EXCLUDED = (
    Path(__file__).resolve().parents[2] / "config" / "excluded_sessions.yaml"
)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def url_safe(s: str) -> str:
    """Percent-encodes every character outside [A-Za-z0-9._-] (UTF-8 bytes)."""
    return _UNSAFE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), s)


class Bucket(ConfigurableResource):
    """An S3 bucket, or a local directory when endpoint is file://<dir>."""

    endpoint: str
    bucket: str
    region: str = "garage"
    access_key: str = ""
    secret_key: str = ""

    def store(self) -> ObjectStore:
        if self.endpoint.startswith("file://"):
            from obstore.store import LocalStore

            root = Path(self.endpoint.removeprefix("file://")) / self.bucket
            root.mkdir(parents=True, exist_ok=True)
            return LocalStore(root)
        from obstore.store import S3Store

        return S3Store(
            self.bucket,
            endpoint=self.endpoint,
            region=self.region,
            access_key_id=self.access_key,
            secret_access_key=self.secret_key,
            virtual_hosted_style_request=False,
            client_options={
                "allow_http": self.endpoint.startswith("http://"),
                "timeout": "60s",
            },
        )


class Transitous(ConfigurableResource):
    base_url: str = "https://api.transitous.org"
    timeout_s: float = 30.0


class Params(ConfigurableResource):
    """Snapping, skeleton and aggregation settings."""

    excluded_sessions: str = str(DEFAULT_EXCLUDED)
    # Skeleton, as in gtfs-zone-timetable-sites sandbox/maps/skeleton.py.
    res_m: float = 2.0
    brush_m: float = 24.0
    spur_m: float = 30.0
    bridge_ratio: float = 1.5
    bin_m: float = 500.0
    max_offline_m: float = 200.0
    # Slack before boarding and past alighting, for GPS noise at the platform.
    stop_slack_m: float = 50.0
    station_radius_m: float = 150.0
    min_sessions: int = 1
    tippecanoe: str = "tippecanoe"
