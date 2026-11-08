"""The skeleton network of all trip shapes, cut into fixed-length bins.

Shapes whose bboxes come within CLUSTER_DEG of each other form a cluster,
which gets its own metric frame and skeleton; far apart regions never share
a projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import shapely
from shapely import LineString, STRtree
from shapely.ops import substring

from gtfs_zone_wifi_pipeline import skeleton
from gtfs_zone_wifi_pipeline.geo import Frame

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gtfs_zone_wifi_pipeline.config import Params
    from gtfs_zone_wifi_pipeline.geo import LatLon, Xy
    from gtfs_zone_wifi_pipeline.transitous import TripShape

CLUSTER_DEG = 0.01
# A trip is matched to the skeleton every PATH_STEP m along its shape; an
# edge within the brush and PARALLEL deg of the trip's heading is a match.
PATH_STEP = 10.0
PARALLEL = 30.0


@dataclass(frozen=True)
class Bin:
    id: str
    edge: str
    k: int
    line: tuple[LatLon, ...]


@dataclass
class NetEdge:
    id: str
    line: LineString  # cluster frame, m
    bins: list[Bin]

    def bin_at(self, s: float) -> Bin:
        n = len(self.bins)
        return self.bins[min(n - 1, max(0, int(s / self.line.length * n)))]


@dataclass
class Cluster:
    id: int
    frame: Frame
    trip_ids: list[str]
    edges: dict[str, NetEdge] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    def tree(self) -> tuple[STRtree, list[NetEdge]]:
        edges = list(self.edges.values())
        return STRtree([e.line for e in edges]), edges


@dataclass
class Network:
    clusters: list[Cluster]
    cluster_of: dict[str, int]  # trip id -> cluster id

    def bins(self) -> dict[str, Bin]:
        return {
            b.id: b for c in self.clusters for e in c.edges.values() for b in e.bins
        }


def _clusters(shapes: Sequence[TripShape]) -> list[list[int]]:
    """Indexes of shapes whose padded bboxes chain together, per cluster."""
    boxes = []
    for s in shapes:
        lats = [p[0] for p in s.line]
        lons = [p[1] for p in s.line]
        boxes.append((min(lats), min(lons), max(lats), max(lons)))
    root = list(range(len(shapes)))

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if (
                a[0] - CLUSTER_DEG <= b[2]
                and b[0] - CLUSTER_DEG <= a[2]
                and a[1] - CLUSTER_DEG <= b[3]
                and b[1] - CLUSTER_DEG <= a[3]
            ):
                root[find(i)] = find(j)
    out: dict[int, list[int]] = {}
    for i in range(len(shapes)):
        out.setdefault(find(i), []).append(i)
    return sorted(out.values())


def cut_bins(edge_id: str, line: LineString, frame: Frame, bin_m: float) -> list[Bin]:
    """The edge cut into round(length / bin_m) equal bins, at least one."""
    n = max(1, round(line.length / bin_m))
    step = line.length / n
    out = []
    for k in range(n):
        piece = substring(line, k * step, (k + 1) * step)
        out.append(
            Bin(
                id=f"{edge_id}.{k}",
                edge=edge_id,
                k=k,
                line=tuple(frame.lat_lon(list(piece.coords))),
            )
        )
    return out


def build_network(shapes: dict[str, TripShape], params: Params) -> Network:
    ordered = [shapes[t] for t in sorted(shapes)]
    clusters = []
    cluster_of = {}
    for cid, members in enumerate(_clusters(ordered)):
        group = [ordered[i] for i in members]
        lats = [p[0] for s in group for p in s.line]
        lons = [p[1] for s in group for p in s.line]
        frame = Frame(
            round((min(lats) + max(lats)) / 2, 6), round((min(lons) + max(lons)) / 2, 6)
        )
        lines = [frame.xy(s.line) for s in group]
        graph, stats = skeleton.build(
            lines, params.res_m, params.brush_m, params.spur_m, params.bridge_ratio
        )
        cluster = Cluster(cid, frame, [s.trip_id for s in group])
        cluster.stats = {
            "skeleton_px": stats.skeleton_px,
            "spurs": stats.spurs,
            "bridges": stats.bridges,
            "edges": len(graph.edges),
        }
        for e in sorted(graph.edges.values(), key=lambda e: e.id):
            eid = f"{cid}.{e.id}"
            line = LineString(e.line)
            cluster.edges[eid] = NetEdge(
                eid, line, cut_bins(eid, line, frame, params.bin_m)
            )
        clusters.append(cluster)
        for s in group:
            cluster_of[s.trip_id] = cid
    return Network(clusters, cluster_of)


def _heading(line: LineString | np.ndarray, s: np.ndarray) -> np.ndarray:
    """Heading (deg mod 180) of line(s) at each m in s, over +-HEADING_WIN m."""
    win = skeleton.HEADING_WIN
    end = shapely.length(line)
    a = shapely.line_interpolate_point(line, np.clip(s - win, 0, end))
    b = shapely.line_interpolate_point(line, np.clip(s + win, 0, end))
    d = shapely.get_coordinates(b) - shapely.get_coordinates(a)
    return np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180


@dataclass(frozen=True)
class TripPath:
    """A trip's shape in its cluster's frame, matched to skeleton edges."""

    line: LineString
    m: np.ndarray  # every PATH_STEP m along the shape
    edge: list[str | None]  # matched edge per point


def trip_path(cluster: Cluster, shape_xy: Sequence[Xy], brush: float) -> TripPath:
    line = LineString(shape_xy)
    m = np.arange(0.0, line.length + 1e-9, PATH_STEP)
    pts = shapely.line_interpolate_point(line, m)
    heading = _heading(line, m)
    tree, edges = cluster.tree()
    best: list[tuple[float, str] | None] = [None] * len(m)
    if edges:
        idx_pt, idx_edge = tree.query(pts, predicate="dwithin", distance=brush)
        lines = np.array([edges[j].line for j in idx_edge], dtype=object)
        s = shapely.line_locate_point(lines, pts[idx_pt])
        d = shapely.distance(lines, pts[idx_pt])
        h = _heading(lines, s)
        diff = np.abs((h - heading[idx_pt] + 90) % 180 - 90)
        for i, j, dist, ok in zip(idx_pt, idx_edge, d, diff < PARALLEL, strict=True):
            if ok and (best[i] is None or dist < best[i][0]):
                best[i] = (float(dist), edges[j].id)
    return TripPath(line, m, [b[1] if b else None for b in best])


def edge_at(path: TripPath, m: float) -> str | None:
    """The edge matched nearest to m along the trip shape."""
    i = min(len(path.m) - 1, max(0, round(m / PATH_STEP)))
    if path.edge[i] is not None:
        return path.edge[i]
    for k in range(1, 4):
        for j in (i - k, i + k):
            if 0 <= j < len(path.m) and path.edge[j] is not None:
                return path.edge[j]
    return None


def bin_of(cluster: Cluster, path: TripPath, m: float) -> Bin | None:
    """The bin at m along the trip shape, through its matched edge."""
    eid = edge_at(path, m)
    if eid is None:
        return None
    edge = cluster.edges[eid]
    return edge.bin_at(edge.line.project(path.line.interpolate(m)))
