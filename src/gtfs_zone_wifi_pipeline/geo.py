"""Metric frames: azimuthal equidistant (WGS84) around a center, in m."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np
from pyproj import Transformer

if TYPE_CHECKING:
    from collections.abc import Sequence

type Xy = tuple[float, float]
type LatLon = tuple[float, float]


@cache
def _transformers(lat0: float, lon0: float) -> tuple[Transformer, Transformer]:
    crs = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m"
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return fwd, inv


@dataclass(frozen=True)
class Frame:
    lat0: float
    lon0: float

    def xy(self, pts: Sequence[LatLon]) -> list[Xy]:
        """(lat, lon) points to (x, y) m."""
        if not pts:
            return []
        lat, lon = np.asarray(pts, dtype=float).T
        x, y = _transformers(self.lat0, self.lon0)[0].transform(lon, lat)
        return list(
            zip(np.atleast_1d(x).tolist(), np.atleast_1d(y).tolist(), strict=True)
        )

    def lat_lon(self, pts: Sequence[Xy]) -> list[LatLon]:
        """(x, y) m points to (lat, lon)."""
        if not pts:
            return []
        x, y = np.asarray(pts, dtype=float).T
        lon, lat = _transformers(self.lat0, self.lon0)[1].transform(x, y)
        return list(
            zip(np.atleast_1d(lat).tolist(), np.atleast_1d(lon).tolist(), strict=True)
        )


def length(line: Sequence[Xy]) -> float:
    return sum(math.dist(p, q) for p, q in pairwise(line))


def haversine(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in m."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(math.sqrt(h))
