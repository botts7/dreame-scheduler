"""Pure geometry for edge clean: turn a room's real wall line-segments into the
thin axis-aligned rectangles a ``dreame_vacuum`` zone clean sweeps along the walls.

No Home Assistant or numpy imports -- plain math on map-mm coordinates (the same
space as ``vacuum_clean_zone`` and the camera's room boxes), so it unit-tests
standalone. The *source* of the wall segments (the firmware's per-room ``walls``
list, or the ``pixel_type`` border band) lives in ``dreame_mapdata.py``; this
module only shapes them into clean zones.

Why this beats the old bounding-box strips: a box strip hugs a room's bounding
rectangle, so it crosses an open doorway into the neighbouring room. A real wall
segment only exists where there is an actual wall, so the strip stays inside the
room and never cleans a neighbour.
"""

from __future__ import annotations

MIN_WALL_MM = 150   # ignore wall stubs shorter than this (door posts, scan noise)
MIN_STRIP_MM = 60   # never emit a strip thinner than this


def strip_from_segment(x0, y0, x1, y1, width):
    """One wall line-segment ``(x0,y0)->(x1,y1)`` -> an axis-aligned rectangle
    ``[X0,Y0,X1,Y1]`` of thickness ``width`` hugging it.

    A near-horizontal wall becomes a band that runs along x and is thin in y; a
    near-vertical wall the reverse. A diagonal wall is approximated by the band of
    its dominant axis -- fine in practice because Dreame room walls are
    overwhelmingly rectilinear. Returns ``None`` for a degenerate (zero-length)
    wall. Coordinates stay in whatever units are passed in (map mm)."""
    half = max(MIN_STRIP_MM, float(width)) / 2.0
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    if dx < 1 and dy < 1:
        return None
    lo_x, hi_x = (x0, x1) if x0 <= x1 else (x1, x0)
    lo_y, hi_y = (y0, y1) if y0 <= y1 else (y1, y0)
    if dx >= dy:                      # horizontal-ish: thin band in y
        cy = (y0 + y1) / 2.0
        return [lo_x, cy - half, hi_x, cy + half]
    cx = (x0 + x1) / 2.0              # vertical-ish: thin band in x
    return [cx - half, lo_y, cx + half, hi_y]


def merge_colinear_walls(walls, *, axis_tol=60, gap_tol=300):
    """Join the many short colinear pieces ``walls_v3`` splits each real wall into,
    back into whole walls -- fewer, longer segments mean far fewer zone strips.

    Only axis-aligned (horizontal / vertical) segments are merged; a diagonal is
    passed through untouched. Horizontal pieces are grouped by shared y (within
    ``axis_tol`` mm) and their x-ranges merged when they overlap or sit within
    ``gap_tol`` mm of each other; vertical pieces by shared x. ``gap_tol`` is kept
    small on purpose: a real doorway opening (~700-900mm) exceeds it, so a wall
    broken by a door stays two segments and we never strip across the opening.

    ``walls`` is an iterable of ``(x0,y0,x1,y1)``; returns a list of the same shape.
    Pure."""
    horiz, vert, other = [], [], []
    for w in walls or []:
        if not (isinstance(w, (list, tuple)) and len(w) >= 4):
            continue
        try:
            x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
        except (TypeError, ValueError):
            continue
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if dy <= axis_tol and dx > dy:
            horiz.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2.0))
        elif dx <= axis_tol and dy > dx:
            vert.append((min(y0, y1), max(y0, y1), (x0 + x1) / 2.0))
        else:
            other.append([x0, y0, x1, y1])

    def _merge(runs, horizontal):
        # runs: (lo, hi, fixed). Bucket by fixed coord (axis_tol), merge 1-D ranges.
        out = []
        buckets = {}
        for lo, hi, fixed in runs:
            buckets.setdefault(round(fixed / axis_tol), []).append((lo, hi, fixed))
        for group in buckets.values():
            group.sort()
            cl, ch, cf = group[0]
            fixes = [cf]
            for lo, hi, fixed in group[1:]:
                if lo <= ch + gap_tol:
                    ch = max(ch, hi)
                    fixes.append(fixed)
                else:
                    f = sum(fixes) / len(fixes)
                    out.append([cl, f, ch, f] if horizontal else [f, cl, f, ch])
                    cl, ch, fixes = lo, hi, [fixed]
            f = sum(fixes) / len(fixes)
            out.append([cl, f, ch, f] if horizontal else [f, cl, f, ch])
        return out

    return _merge(horiz, True) + _merge(vert, False) + other


def strips_from_walls(walls, width, *, min_len=MIN_WALL_MM):
    """All wall strips for one room. ``walls`` is an iterable of ``(x0,y0,x1,y1)``
    in map mm. Walls shorter than ``min_len`` are skipped. Returns a list of
    integer ``[x0,y0,x1,y1]`` rectangles (empty if no wall qualifies)."""
    out = []
    for w in walls or []:
        if not (isinstance(w, (list, tuple)) and len(w) >= 4):
            continue
        try:
            x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
        except (TypeError, ValueError):
            continue
        if ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 < min_len:
            continue
        r = strip_from_segment(x0, y0, x1, y1, width)
        if r is not None:
            out.append([int(round(v)) for v in r])
    return out


def batch_rects(rects, cap=32):
    """Chunk a flat list of zone rectangles into batches of at most ``cap`` (the
    firmware's per-task zone limit). A later ``vacuum_clean_zone`` REPLACES the
    prior task, so each batch must be sent and finished before the next; a whole
    small home (<= cap strips) is a single batch. Returns ``list[list[rect]]``."""
    cap = max(1, int(cap))
    rects = list(rects or [])
    return [rects[i:i + cap] for i in range(0, len(rects), cap)]
