"""Centerlines of trip shapes as a graph, so shapes along one track share edges.

Ported from gtfs-zone-timetable-sites sandbox/maps/skeleton.py, without the
drawing code. Every shape is drawn into a raster at `res` m per px with a
round brush `brush` m wide, so the two directions of one line and
overlapping routes become one blob. Thinning leaves 1 px wide centerlines,
traced into nodes and edges:

- node: a group of touching skeleton px with other than 2 neighbors (ends
  have 1, junctions 3 or more), at the group's centroid
- edge: the chain of 2-neighbor px between two nodes, as a polyline from node
  to node through the px centers

Dead-end edges under `spur` m are pruned in rounds. Edges between junctions
that no shape runs along and no shape links within `bridge` x their length
(necks where two blobs touch) are removed, then spurs pruned again. A node
with exactly two edges is dissolved and its edges joined.

The raster is never built whole: only tiles that a shape passes are drawn
and thinned, each with a `pad` px border, and only each tile's core is kept.
Thinning and the brush only look a bounded distance away, so the kept px
match one global raster while memory follows the length of the shapes.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely import LineString
from skimage.draw import line as draw_line
from skimage.morphology import skeletonize

from gtfs_zone_wifi_pipeline.geo import length

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gtfs_zone_wifi_pipeline.geo import Xy

# px offsets of the 8 neighbors.
AROUND = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
# Bridge test: heading taken over +-HEADING_WIN m, a shape point within
# PARALLEL deg covers an edge point, an edge under BARE covered is bare.
HEADING_WIN = 8.0
PARALLEL = 30.0
BARE = 0.05
TILE = 384
# Thinning removes one px layer per pass and stops at the blob's middle, so
# a tile's core is exact when pad exceeds the brush plus the widest blob.
PAD = 96

type Px = tuple[int, int]


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    res: float

    def xy(self, r: float, c: float) -> Xy:
        return self.x0 + (c + 0.5) * self.res, self.y0 + (r + 0.5) * self.res

    def px(self, p: Xy) -> tuple[float, float]:
        """(row, col) of point p, fractional."""
        return (p[1] - self.y0) / self.res - 0.5, (p[0] - self.x0) / self.res - 0.5


@dataclass
class Node:
    id: int
    xy: Xy
    ring: bool = False  # placed on a ring that had no node


@dataclass
class Edge:
    id: int
    a: int
    b: int
    line: list[Xy]  # m, from a to b
    px: int  # chain px


@dataclass
class Graph:
    nodes: dict[int, Node] = field(default_factory=dict)
    edges: dict[int, Edge] = field(default_factory=dict)
    inc: dict[int, list[int]] = field(default_factory=dict)  # node -> edge ids
    next_id: int = 0

    def add_node(self, xy: Xy, ring: bool = False) -> int:
        n = self.next_id
        self.next_id += 1
        self.nodes[n] = Node(n, xy, ring)
        self.inc[n] = []
        return n

    def add_edge(self, a: int, b: int, line: list[Xy], px: int) -> int:
        e = self.next_id
        self.next_id += 1
        self.edges[e] = Edge(e, a, b, line, px)
        self.inc[a].append(e)
        self.inc[b].append(e)
        return e

    def remove_edge(self, e: int) -> Edge:
        edge = self.edges.pop(e)
        self.inc[edge.a].remove(e)
        self.inc[edge.b].remove(e)
        return edge

    def remove_node(self, n: int) -> None:
        del self.nodes[n]
        del self.inc[n]

    def degree(self, n: int) -> int:
        return len(self.inc[n])

    def dissolve(self) -> None:
        """Remove degree-0 nodes; join the two edges of each degree-2 node."""
        for n in list(self.nodes):
            if self.degree(n) == 0:
                self.remove_node(n)
            elif self.degree(n) == 2 and len(set(self.inc[n])) == 2:
                e1, e2 = (self.remove_edge(e) for e in list(self.inc[n]))
                l1 = e1.line if e1.b == n else e1.line[::-1]
                l2 = e2.line if e2.a == n else e2.line[::-1]
                a = e1.a if e1.b == n else e1.b
                b = e2.b if e2.a == n else e2.a
                self.remove_node(n)
                self.add_edge(a, b, l1 + l2[1:], e1.px + e2.px + 1)


def grid(lines: Sequence[Sequence[Xy]], res: float, margin: float) -> Grid:
    xs = [p[0] for line in lines for p in line]
    ys = [p[1] for line in lines for p in line]
    return Grid(min(xs) - margin, min(ys) - margin, res)


def _segments(lines: Sequence[Sequence[Xy]], g: Grid) -> list[np.ndarray]:
    """Each line's drawn px (rows, cols), 1 px wide between its rounded points."""
    out = []
    for line in lines:
        pts = [g.px(q) for q in line]
        for (r0, c0), (r1, c1) in pairwise(pts):
            rr, cc = draw_line(round(r0), round(c0), round(r1), round(c1))
            out.append(np.stack([rr, cc]))
    return out


def skeleton_px(
    lines: Sequence[Sequence[Xy]],
    g: Grid,
    brush: float,
    tile: int = TILE,
    pad: int = PAD,
) -> set[Px]:
    """Skeleton px (row, col) of the brushed lines, thinned tile by tile."""
    radius = brush / 2 / g.res
    reach = pad + math.ceil(radius) + 1
    by_tile: dict[Px, list[np.ndarray]] = {}
    for seg in _segments(lines, g):
        rows = range((seg[0].min() - reach) // tile, (seg[0].max() + reach) // tile + 1)
        cols = range((seg[1].min() - reach) // tile, (seg[1].max() + reach) // tile + 1)
        for tr in rows:
            for tc in cols:
                by_tile.setdefault((tr, tc), []).append(seg)
    out: set[Px] = set()
    size = tile + 2 * pad
    # The brush cannot reach past the drawn px bbox plus its radius, and
    # thinning keeps px inside the mask, so only that crop of a tile is built.
    grow = math.ceil(radius) + 2
    for (tr, tc), segs in sorted(by_tile.items()):
        r0, c0 = tr * tile - pad, tc * tile - pad
        rr = np.concatenate([seg[0] for seg in segs]) - r0
        cc = np.concatenate([seg[1] for seg in segs]) - c0
        inside = (rr >= 0) & (rr < size) & (cc >= 0) & (cc < size)
        rr, cc = rr[inside], cc[inside]
        if not len(rr):
            continue
        a0, a1 = max(rr.min() - grow, 0), min(rr.max() + grow + 1, size)
        b0, b1 = max(cc.min() - grow, 0), min(cc.max() + grow + 1, size)
        drawn = np.zeros((a1 - a0, b1 - b0), dtype=bool)
        drawn[rr - a0, cc - b0] = True
        mask = ndimage.distance_transform_edt(~drawn) <= radius
        skel = skeletonize(np.pad(mask, 1))[1:-1, 1:-1]
        rs, cs = np.nonzero(skel)
        rs, cs = rs + a0, cs + b0
        core = (rs >= pad) & (rs < pad + tile) & (cs >= pad) & (cs < pad + tile)
        out.update(zip((rs[core] + r0).tolist(), (cs[core] + c0).tolist(), strict=True))
    return out


def _around(on: set[Px], p: Px) -> list[Px]:
    return [q for dr, dc in AROUND if (q := (p[0] + dr, p[1] + dc)) in on]


def trace(on: set[Px], g: Grid) -> Graph:
    """Nodes at groups of non-2-neighbor px, edges along the chains between."""
    node_px = {p for p in on if len(_around(on, p)) != 2}
    graph = Graph()
    group: dict[Px, int] = {}
    for start in sorted(node_px):
        if start in group:
            continue
        members, todo = [start], deque([start])
        group[start] = -1
        while todo:
            for q in _around(node_px, todo.popleft()):
                if q not in group:
                    group[q] = -1
                    members.append(q)
                    todo.append(q)
        r = sum(p[0] for p in members) / len(members)
        c = sum(p[1] for p in members) / len(members)
        n = graph.add_node(g.xy(r, c))
        for p in members:
            group[p] = n

    seen: set[Px] = set()

    def walk(start: Px, first: Px) -> tuple[list[Px], Px]:
        """Chain px from first away from start, and the px it stops at."""
        chain, prev, cur = [first], start, first
        seen.add(first)
        while True:
            nxt = next(q for q in _around(on, cur) if q != prev)
            if nxt in group or nxt in seen:
                return chain, nxt
            chain.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt

    for p in sorted(group):
        a = group[p]
        for q in _around(on, p):
            if q in group or q in seen:
                continue
            chain, end = walk(p, q)
            if end not in group:
                raise ValueError(f"chain from px {p} runs into a traced chain at {end}")
            b = group[end]
            if a == b and len(chain) <= 2:
                continue
            line = [graph.nodes[a].xy, *(g.xy(*c) for c in chain), graph.nodes[b].xy]
            graph.add_edge(a, b, line, len(chain))
    for p in sorted(on - seen - group.keys()):
        if p in seen:
            continue
        seen.add(p)
        a = graph.add_node(g.xy(*p), ring=True)
        chain, _ = walk(p, _around(on, p)[0])
        line = [g.xy(*p), *(g.xy(*c) for c in chain), g.xy(*p)]
        graph.add_edge(a, a, line, len(chain) + 1)
    graph.dissolve()
    return graph


def prune(graph: Graph, spur: float) -> int:
    """Remove dead-end edges under spur m in rounds; how many were removed."""
    removed = 0
    while True:
        cut = [
            e.id
            for e in graph.edges.values()
            if e.a != e.b
            and min(graph.degree(e.a), graph.degree(e.b)) == 1
            and length(e.line) < spur
        ]
        if not cut:
            return removed
        for e in cut:
            graph.remove_edge(e)
        removed += len(cut)
        graph.dissolve()


def headings(
    line: Sequence[Xy], step: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points every step m along line, their m along it and their heading
    (deg mod 180) between the points HEADING_WIN before and after."""
    pts = np.asarray(line, dtype=float)
    along = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    s = np.arange(0.0, along[-1] + 1e-9, step)

    def at(t: np.ndarray) -> np.ndarray:
        t = np.clip(t, 0.0, along[-1])
        return np.c_[np.interp(t, along, pts[:, 0]), np.interp(t, along, pts[:, 1])]

    dx, dy = (at(s + HEADING_WIN) - at(s - HEADING_WIN)).T
    return at(s), s, np.degrees(np.arctan2(dy, dx)) % 180


@dataclass(frozen=True)
class ShapePoints:
    """Every shape resampled every res m, with each point's m along its shape,
    heading and shape index, in a KD-tree."""

    s: np.ndarray
    heading: np.ndarray
    owner: np.ndarray
    tree: cKDTree


def shape_points(lines: Sequence[Sequence[Xy]], res: float) -> ShapePoints:
    xy, s, heading, owner = [], [], [], []
    for k, line in enumerate(lines):
        q, sq, hq = headings(line, res)
        xy.append(q)
        s.append(sq)
        heading.append(hq)
        owner.append(np.full(len(sq), k))
    xy_, s_, heading_, owner_ = (np.concatenate(a) for a in (xy, s, heading, owner))
    return ShapePoints(s_, heading_, owner_, cKDTree(xy_))


def bridges(
    graph: Graph, pts: ShapePoints, res: float, brush: float, ratio: float
) -> int:
    """Remove edges between junctions that no shape runs along and no shape
    links within ratio x their length; how many were removed."""
    s_, heading_, owner_, tree = pts.s, pts.heading, pts.owner, pts.tree
    r = brush / 2

    def bare(line: list[Xy]) -> bool:
        q, _, hq = headings(line, res)
        covered = sum(
            bool(near) and np.abs((heading_[near] - h + 90) % 180 - 90).min() < PARALLEL
            for near, h in zip(tree.query_ball_point(q, r), hq, strict=True)
        )
        return covered < BARE * len(q)

    def linked(e: Edge) -> bool:
        """Whether one shape passes near both ends within ratio x length."""
        near_a, near_b = (
            tree.query_ball_point(graph.nodes[n].xy, r) for n in (e.a, e.b)
        )
        at_b: dict[int, list[float]] = {}
        for i in near_b:
            at_b.setdefault(owner_[i], []).append(s_[i])
        limit = ratio * length(e.line)
        return any(
            abs(s_[i] - sb) < limit for i in near_a for sb in at_b.get(owner_[i], [])
        )

    cut = [
        e.id
        for e in graph.edges.values()
        if e.a != e.b
        and min(graph.degree(e.a), graph.degree(e.b)) >= 3
        and bare(e.line)
        and not linked(e)
    ]
    for e in cut:
        graph.remove_edge(e)
    graph.dissolve()
    return len(cut)


@dataclass(frozen=True)
class Stats:
    skeleton_px: int
    spurs: int
    bridges: int


def build(
    lines: Sequence[Sequence[Xy]],
    res: float,
    brush: float,
    spur: float,
    bridge: float,
    tile: int = TILE,
) -> tuple[Graph, Stats]:
    """The skeleton graph of lines (m), pruned and with bridges removed."""
    g = grid(lines, res, brush + PAD * res)
    on = skeleton_px(lines, g, brush, tile)
    graph = trace(on, g)
    spurs = prune(graph, spur)
    cut = bridges(graph, shape_points(lines, res), res, brush, bridge)
    spurs += prune(graph, spur)
    # px-center chains step in 45 deg stairs, which adds ~6% to their length.
    for e in graph.edges.values():
        e.line = list(LineString(e.line).simplify(res).coords)
    return graph, Stats(len(on), spurs, cut)
