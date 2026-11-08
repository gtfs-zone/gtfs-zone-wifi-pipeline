"""skeleton: tiled thinning matches one raster; shapes on one track merge."""

import math

import pytest

from gtfs_zone_wifi_pipeline import skeleton as sk
from gtfs_zone_wifi_pipeline.geo import length


def arc(cx, cy, r, a0, a1, n=200):
    return [
        (
            cx + r * math.cos(a0 + (a1 - a0) * k / n),
            cy + r * math.sin(a0 + (a1 - a0) * k / n),
        )
        for k in range(n + 1)
    ]


# A curve, its other direction 8 m off, a line crossing it, a branch and a
# line passing 20 m from the curve without meeting it.
LINES = [
    arc(0, 0, 1200, 0.1, 1.4),
    [(x + 8, y) for x, y in arc(0, 0, 1200, 0.1, 1.4)][::-1],
    [(-200, 600), (1800, 700)],
    [(700, 650), (1100, 1300), (1150, 2000)],
    [(400 + 1220 * math.cos(1.25), 0 + 1220 * math.sin(1.25)), (300, 2200)],
]


@pytest.mark.parametrize("tile", [64, 160, 384])
def test_tiles_match_one_raster(tile):
    g = sk.grid(LINES, 2.0, 24 + sk.PAD * 2.0)
    whole = sk.skeleton_px(LINES, g, 24.0, tile=4096)
    assert whole
    assert sk.skeleton_px(LINES, g, 24.0, tile=tile) == whole


def test_two_directions_merge_into_one_edge():
    line = [(0.0, 0.0), (3000.0, 0.0)]
    back = [(3000.0, 9.0), (0.0, 9.0)]
    graph, stats = sk.build([line, back], 2.0, 24.0, 30.0, 1.5)
    assert len(graph.edges) == 1
    (edge,) = graph.edges.values()
    assert abs(length(edge.line) - 3000) < 30
    assert stats.skeleton_px > 0


def test_parallel_tracks_apart_stay_apart():
    graph, _ = sk.build(
        [[(0.0, 0.0), (3000.0, 0.0)], [(0.0, 80.0), (3000.0, 80.0)]],
        2.0,
        24.0,
        30.0,
        1.5,
    )
    assert len(graph.edges) == 2


def test_branch_makes_a_junction_without_spurs():
    main = [(0.0, 0.0), (4000.0, 0.0)]
    branch = [(2000.0, 0.0), (3000.0, 1500.0)]
    graph, _ = sk.build([main, branch], 2.0, 24.0, 30.0, 1.5)
    assert len(graph.edges) == 3
    assert min(length(e.line) for e in graph.edges.values()) > 1000


def test_edges_are_simplified_to_shape_length():
    diagonal = [(0.0, 0.0), (2000.0, 1500.0)]
    graph, _ = sk.build([diagonal], 2.0, 24.0, 30.0, 1.5)
    (edge,) = graph.edges.values()
    assert abs(length(edge.line) - 2500) < 25
    assert len(edge.line) < 10
