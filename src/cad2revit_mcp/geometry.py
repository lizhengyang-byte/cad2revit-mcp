"""Small geometry helpers used by the recognition rules.

Units are millimetres throughout.  The functions are deliberately simple
and deterministic: the same input always produces the same output so the
whole pipeline stays reproducible (see the architecture document).
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

Point = Tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def bbox(points: Iterable[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_size(points: Iterable[Point]) -> Tuple[float, float]:
    x0, y0, x1, y1 = bbox(points)
    return x1 - x0, y1 - y0


def angle_of(start: Point, end: Point) -> float:
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180.0


def is_parallel(a: Tuple[Point, Point], b: Tuple[Point, Point], tol_deg: float = 1.5) -> bool:
    diff = abs(angle_of(*a) - angle_of(*b))
    diff = min(diff, 180.0 - diff)
    return diff <= tol_deg


def is_axis_aligned(a: Tuple[Point, Point], b: Tuple[Point, Point], tol_deg: float = 1.0) -> bool:
    ang = angle_of(*a)
    return min(ang, abs(90.0 - ang), abs(180.0 - ang)) <= tol_deg


def point_line_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return distance(p, a)
    return abs(dy * p[0] - dx * p[1] + bx * ay - by * ax) / length


def project_parameter(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denom


def overlap_length(a1: Point, a2: Point, b1: Point, b2: Point, tol_deg: float = 1.5) -> float:
    """Length of the projection overlap of two parallel segments."""
    if not is_parallel((a1, a2), (b1, b2), tol_deg):
        return 0.0
    t_b1 = project_parameter(b1, a1, a2)
    t_b2 = project_parameter(b2, a1, a2)
    lo, hi = sorted((t_b1, t_b2))
    lo = max(lo, 0.0)
    hi = min(hi, 1.0)
    if hi <= lo:
        return 0.0
    return (hi - lo) * distance(a1, a2)


def is_closed_polyline(points: Sequence[Point], tol: float = 1.0) -> bool:
    if len(points) < 3:
        return False
    return distance(points[0], points[-1]) <= tol


def polygon_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    acc = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


def rectangle_from_points(points: Sequence[Point], tol: float = 5.0) -> float | None:
    """Return the short side of a rectangle-ish polygon, else ``None``."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= tol or h <= tol:
        return None
    area = polygon_area(points)
    if abs(area - w * h) / max(w * h, 1.0) > 0.12:
        return None
    return min(w, h)


def offsets_are_parallel_projection(p1: Point, p2: Point, p3: Point, p4: Point, tol: float = 3.0) -> bool:
    """Check that the two segments are the two sides of a rectangular strip."""
    return (
        point_line_distance(p1, p3, p4) <= tol
        and point_line_distance(p2, p3, p4) <= tol
        and abs(distance(p1, p2) - distance(p3, p4)) <= max(tol * 2, 10.0)
    )


def snap(value: float, grid: float = 1.0) -> float:
    return round(value / grid) * grid


def dedupe_segments(segments, tol: float = 2.0):
    """Drop duplicate or nearly identical segments, keeping the first one."""
    kept = []
    for seg in segments:
        for other in kept:
            if (
                distance(seg.start, other.start) <= tol
                and distance(seg.end, other.end) <= tol
            ) or (
                distance(seg.start, other.end) <= tol
                and distance(seg.end, other.start) <= tol
            ):
                break
        else:
            kept.append(seg)
    return kept
