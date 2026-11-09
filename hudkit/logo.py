"""The SimpleWinClean mark: a banana that is also a clean sweep.

A crescent banana in the scheme's cursor amber, tilted up to the right as
if mid-swipe, with three cyan speed lines trailing behind it and a small
glint above it - the "clean" in the name. It sits in a cut-corner badge that
matches every other panel in the app.

Geometry only, no drawing library: layout() returns plain coordinates in a
unit square (0..1, y pointing down). The header draws them on a Tk Canvas at
runtime; tools/make_icon.py rasterises the same shapes into the .ico used for
the executable, taskbar and installer. One source of truth, so the icon and
the in-app mark cannot drift apart.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from . import palette

Point = Tuple[float, float]

BANANA = palette.SIGNATURE
BANANA_SHADE = palette.WARN    # for the shading along the back
STEM = palette.BANANA_TIP
SWEEP = palette.ACCENT
SPARKLE = palette.ACCENT_BRIGHT
BADGE = palette.PANEL
BADGE_EDGE = palette.ACCENT_DIM

TILT = -20.0              # degrees; negative lifts the right end

# Crescent: the belly is the lower arc of the outer circle, the back is the
# lower arc of a larger circle set higher. Unequal radii make it taper
# toward the ends the way a banana does.
_OUTER = (0.50, 0.10, 0.48)   # cx, cy, r
_INNER = (0.50, -0.12, 0.54)
_TRIM_DEG = 3.0               # blunts the tips slightly

# Where the banana is fitted in the square: leaves room on the left for the
# speed lines and above for the glint.
_BOX = (0.25, 0.22, 0.97, 0.86)   # x0, y0, x1, y1

# For tiny icons (16-24px) the banana has to fill the badge or it becomes a
# sliver, so it gets a bigger box and less room is left for the extras.
COMPACT_BOX = (0.14, 0.14, 0.98, 0.94)


def _intersections() -> Tuple[Point, Point]:
    cx, y1, r1 = _OUTER
    _, y2, r2 = _INNER
    # (y-y1)^2 - (y-y2)^2 = r1^2 - r2^2  ->  linear in y
    y = ((r1 * r1 - r2 * r2) - (y1 * y1 - y2 * y2)) / (2 * (y2 - y1))
    dx = math.sqrt(max(0.0, r1 * r1 - (y - y1) ** 2))
    return (cx - dx, y), (cx + dx, y)


def _angle(circle, p: Point) -> float:
    cx, cy, _ = circle
    return math.degrees(math.atan2(p[1] - cy, p[0] - cx))


def _arc(circle, a0: float, a1: float, steps: int) -> List[Point]:
    cx, cy, r = circle
    out = []
    for i in range(steps + 1):
        t = math.radians(a0 + (a1 - a0) * i / steps)
        out.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return out


def _rotate(points: List[Point], degrees: float, about: Point) -> List[Point]:
    a = math.radians(degrees)
    ca, sa = math.cos(a), math.sin(a)
    ox, oy = about
    return [(ox + (x - ox) * ca - (y - oy) * sa,
             oy + (x - ox) * sa + (y - oy) * ca) for x, y in points]


def _fit(groups: List[List[Point]], box) -> List[List[Point]]:
    """Uniformly scale and centre several point lists into `box` together."""
    xs = [x for g in groups for x, _ in g]
    ys = [y for g in groups for _, y in g]
    x0, y0, x1, y1 = box
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    k = min((x1 - x0) / w, (y1 - y0) / h)
    ox = x0 + ((x1 - x0) - w * k) / 2 - min(xs) * k
    oy = y0 + ((y1 - y0) - h * k) / 2 - min(ys) * k
    return [[(ox + x * k, oy + y * k) for x, y in g] for g in groups]


def layout(steps: int = 48, box=None) -> Dict[str, object]:
    """Every part of the mark, positioned in the unit square."""
    left, right = _intersections()
    o_l, o_r = _angle(_OUTER, left), _angle(_OUTER, right)
    i_l, i_r = _angle(_INNER, left), _angle(_INNER, right)

    # Outer arc left->right through the bottom; inner arc back right->left.
    belly = _arc(_OUTER, o_l - _TRIM_DEG, o_r + _TRIM_DEG, steps)
    back = _arc(_INNER, i_r + _TRIM_DEG, i_l - _TRIM_DEG, steps)

    centre = (0.5, 0.35)
    belly = _rotate(belly, TILT, centre)
    back = _rotate(back, TILT, centre)
    belly, back = _fit([belly, back], box or _BOX)

    body = belly + back
    shade = back[len(back) // 6: -len(back) // 6]

    # Tips sit exactly where the two arcs end, so they cannot misalign.
    def end(a: Point, b: Point) -> Point:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    stem_base = end(belly[0], back[-1])
    flower = end(belly[-1], back[0])
    # Stem points away from the body, along the curve at the left end.
    tx, ty = belly[0][0] - belly[3][0], belly[0][1] - belly[3][1]
    tl = math.hypot(tx, ty) or 1.0
    stem_tip = (stem_base[0] + tx / tl * 0.06, stem_base[1] + ty / tl * 0.06)

    # Speed lines trail behind the direction of travel (left), parallel to
    # the banana's chord so they read as its motion, not as weather. Spacing
    # is absolute, not a fraction of the body: fractions put three thick
    # lines on top of each other.
    chord = math.atan2(flower[1] - stem_base[1], flower[0] - stem_base[0])
    dx, dy = math.cos(chord), math.sin(chord)
    low = max(p[1] for p in belly)
    mid = (stem_base[1] + low) / 2 + 0.02
    spacing = 0.085
    lines = []
    for offset, length, gap in ((-1, 0.13, 0.05), (0, 0.21, 0.035),
                                (1, 0.11, 0.06)):
        ay = mid + offset * spacing
        near = [p[0] for p in body if abs(p[1] - ay) < 0.05]
        edge = min(near) if near else stem_base[0]
        ax = edge - gap
        lines.append(((ax - dx * length, ay - dy * length), (ax, ay)))

    # Glint sits in the hollow above the back of the curve.
    xs = [p[0] for p in body]
    ys = [p[1] for p in body]
    bx0, by0 = min(xs), min(ys)
    bw = max(xs) - bx0
    gx, gy = bx0 + bw * 0.42, by0 + 0.02
    glint = []
    for i in range(8):
        a = math.radians(-90 + i * 45)
        rr = 0.078 if i % 2 == 0 else 0.020
        glint.append((gx + rr * math.cos(a), gy + rr * math.sin(a)))
    glint_dot = ((gx + 0.105, gy + 0.075), 0.018)

    return {
        "body": body,
        "shade": shade,
        "stem": (stem_base, stem_tip),
        "flower": flower,
        "lines": lines,
        "glint": glint,
        "glint_dot": glint_dot,
    }


def badge(cut: float = 0.18) -> List[Point]:
    """The cut-corner square the mark sits in, matching the app's panels."""
    return [(cut, 0.0), (1.0, 0.0), (1.0, 1.0 - cut), (1.0 - cut, 1.0),
            (0.0, 1.0), (0.0, cut)]


def to_pixels(points, size: float, x: float = 0.0, y: float = 0.0,
              inset: float = 0.0) -> List[Tuple[float, float]]:
    """Map unit-square points into a `size` box at (x, y), shrunk by `inset`
    (a fraction of the box) for breathing room inside the badge."""
    usable = size * (1.0 - 2 * inset)
    off = size * inset
    return [(x + off + px_ * usable, y + off + py_ * usable)
            for px_, py_ in points]
