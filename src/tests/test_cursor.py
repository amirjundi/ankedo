"""Pointer path generation.

Pure geometry, so it is testable without a browser. What matters is that paths do not
look machine-generated: curved rather than straight, jittered, and landing exactly on
target despite the noise.
"""
from __future__ import annotations

import math
import random

from src.browsers.cursor import bezier_points, click_point_within


def _straight_line_deviation(points, start, end) -> float:
    """Largest perpendicular distance from the direct line."""
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0)
    worst = 0.0
    for x, y in points:
        # |cross product| / |line|
        distance = abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / length
        worst = max(worst, distance)
    return worst


def test_path_starts_and_ends_on_target():
    points = bezier_points((0, 0), (400, 300), rng=random.Random(1))
    assert points[-1] == (400, 300), "must land exactly on target despite jitter"
    assert math.dist(points[0], (0, 0)) < 3


def test_path_is_curved_not_straight():
    """A straight line is the most obvious tell of synthetic movement."""
    start, end = (0, 0), (600, 0)
    points = bezier_points(start, end, rng=random.Random(2))
    assert _straight_line_deviation(points, start, end) > 10


def test_paths_differ_between_runs():
    """Identical trajectories across clicks would fingerprint as easily as a line."""
    a = bezier_points((0, 0), (300, 300), rng=random.Random(1))
    b = bezier_points((0, 0), (300, 300), rng=random.Random(2))
    assert a != b


def test_paths_bow_both_ways():
    """All paths curving the same direction is itself a pattern."""
    signs = set()
    for seed in range(30):
        points = bezier_points((0, 0), (500, 0), rng=random.Random(seed))
        mid_y = points[len(points) // 2][1]
        signs.add(mid_y > 0)
    assert signs == {True, False}


def test_longer_moves_get_more_points():
    short = bezier_points((0, 0), (30, 30), rng=random.Random(3))
    long = bezier_points((0, 0), (900, 700), rng=random.Random(3))
    assert len(long) > len(short)


def test_zero_distance_is_handled():
    assert bezier_points((10, 10), (10, 10)) == [(10, 10)]


def test_click_points_land_inside_the_box_and_vary():
    box = {"x": 100, "y": 200, "width": 80, "height": 40}
    points = [click_point_within(box, rng=random.Random(i)) for i in range(40)]
    assert len({p for p in points}) > 30, "repeated identical click points are a signal"
    # Gaussian around the centre, so allow tails but require most inside.
    inside = [
        p for p in points
        if box["x"] <= p[0] <= box["x"] + box["width"]
        and box["y"] <= p[1] <= box["y"] + box["height"]
    ]
    assert len(inside) > 30


def test_click_points_are_not_always_dead_centre():
    box = {"x": 0, "y": 0, "width": 100, "height": 100}
    centres = sum(
        1 for i in range(50) if click_point_within(box, rng=random.Random(i)) == (50.0, 50.0)
    )
    assert centres == 0
