"""Tests for the real-wall edge geometry: edge_geometry (pure) + the MapData
reader's pure extraction/strip helpers driven by a fake MapData.

Run: python tests/test_edge_geometry.py   (no Home Assistant install needed;
numpy is optional -- the pixel_type checks skip if it isn't importable)
"""
import importlib.util
import os
import sys
import types

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")
sys.path.insert(0, BASE)  # lets dreame_mapdata's bare-import fallback resolve edge_geometry


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


eg = load("edge_geometry")
md = load("dreame_mapdata")       # needs edge_geometry already in sys.modules

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


# ---- edge_geometry: strip_from_segment ----
# A horizontal wall (long in x) -> a band thin in y, centred on the wall's y.
r = eg.strip_from_segment(0, 1000, 4000, 1000, 250)
check("horizontal wall -> full x span", r[0] == 0 and r[2] == 4000)
check("horizontal wall -> thin y band = width", round(r[3] - r[1]) == 250)
check("horizontal wall -> band centred on wall y", r[1] == 1000 - 125 and r[3] == 1000 + 125)

# A vertical wall (long in y) -> a band thin in x.
r = eg.strip_from_segment(500, 0, 500, 3000, 250)
check("vertical wall -> full y span", r[1] == 0 and r[3] == 3000)
check("vertical wall -> thin x band = width", round(r[2] - r[0]) == 250)

check("degenerate wall -> None", eg.strip_from_segment(10, 10, 10, 10, 250) is None)
check("min strip width enforced", round(eg.strip_from_segment(0, 0, 1000, 0, 10)[3]
                                        - eg.strip_from_segment(0, 0, 1000, 0, 10)[1]) == eg.MIN_STRIP_MM)

# ---- edge_geometry: strips_from_walls ----
walls = [
    (0, 0, 4000, 0),       # long wall -> kept
    (0, 0, 0, 3000),       # long wall -> kept
    (0, 0, 50, 0),         # 50mm stub -> dropped (< MIN_WALL_MM)
    "garbage", [1, 2],     # malformed -> skipped
]
strips = eg.strips_from_walls(walls, 250)
check("strips_from_walls keeps 2 real walls, drops stub+garbage", len(strips) == 2)
check("strips_from_walls returns int rects", all(isinstance(v, int) for s in strips for v in s))

# ---- edge_geometry: batch_rects ----
rects = [[0, 0, 1, 1]] * 70
batches = eg.batch_rects(rects, cap=32)
check("batch_rects chunks at cap", len(batches) == 3 and len(batches[0]) == 32 and len(batches[-1]) == 6)
check("batch_rects empty -> []", eg.batch_rects([], 32) == [])

# ---- edge_geometry: merge_colinear_walls ----
# Four colinear touching pieces of one long wall at y=0 -> one segment.
pieces = [(0, 0, 1000, 0), (1000, 0, 2000, 0), (2000, 0, 3000, 0), (3000, 0, 4000, 0)]
merged = eg.merge_colinear_walls(pieces)
check("merge: 4 colinear pieces -> 1 wall", len(merged) == 1)
check("merge: merged wall spans full length", merged[0][0] == 0 and merged[0][2] == 4000)

# A real doorway gap (800mm > gap_tol) must NOT be bridged.
gapped = [(0, 0, 1000, 0), (1800, 0, 3000, 0)]   # 800mm gap between 1000 and 1800
check("merge: doorway gap kept as 2 walls", len(eg.merge_colinear_walls(gapped)) == 2)

# Vertical pieces merge on shared x; horizontal+vertical stay separate.
mixed = [(500, 0, 500, 1000), (500, 1000, 500, 2000), (0, 0, 1000, 0)]
mm2 = eg.merge_colinear_walls(mixed)
check("merge: 2 vertical + 1 horizontal -> 2 walls", len(mm2) == 2)

# A diagonal is passed through untouched.
check("merge: diagonal passed through", eg.merge_colinear_walls([(0, 0, 1000, 1000)]) == [[0.0, 0.0, 1000.0, 1000.0]])

# End-to-end: merged perimeter yields far fewer strips than raw pieces.
raw_room = [(0, 0, 1000, 0), (1000, 0, 2000, 0), (2000, 0, 2000, 1000), (2000, 1000, 2000, 2000)]
check("merge: room perimeter 4 pieces -> 2 strips", len(eg.strips_from_walls(eg.merge_colinear_walls(raw_room), 250)) == 2)


# ---- dreame_mapdata: fake MapData ----
def seg(sid, x0, y0, x1, y1, name):
    return types.SimpleNamespace(id=sid, x0=x0, y0=y0, x1=x1, y1=y1, name=name, custom_name=None)


def line(x0, y0, x1, y1):
    return types.SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1)


fake = types.SimpleNamespace(
    dimensions=types.SimpleNamespace(grid_size=50, left=-1000, top=-2000, width=4, height=3),
    segments={
        1: seg(1, 0, 0, 4000, 3000, "Kitchen"),
        2: seg(2, 4000, 0, 8000, 3000, "Lounge"),
    },
    walls={
        1: [line(0, 0, 4000, 0), line(0, 0, 0, 3000)],   # Kitchen: 2 walls
        2: [line(4000, 0, 8000, 0)],                      # Lounge: 1 wall
    },
    pixel_type=None,
)

boxes = md._segment_boxes(fake)
check("reader: segment boxes read names+box", boxes[1][0] == "Kitchen" and boxes[1][1] == (0.0, 0.0, 4000.0, 3000.0))

wmm = md._walls_mm(fake)
check("reader: walls read per segment", len(wmm[1]) == 2 and len(wmm[2]) == 1)
check("reader: wall tuple is (x0,y0,x1,y1)", wmm[1][0] == (0.0, 0.0, 4000.0, 0.0))

geom = {"walls": wmm}
batches, source = md.edge_strips_for_segments(geom, [1, 2], 250)
check("reader: strips come from walls", source == "walls")
check("reader: 3 wall strips total (2+1) in one batch", sum(len(b) for b in batches) == 3 and len(batches) == 1)

batches2, source2 = md.edge_strips_for_segments({"walls": {}}, [1, 2], 250)
check("reader: no walls -> source none, no batches", source2 == "none" and batches2 == [])

# ---- active_map chain (fake coordinator) ----
coord = types.SimpleNamespace(device=types.SimpleNamespace(
    status=types.SimpleNamespace(current_map=fake)))
check("reader: active_map reads device.status.current_map", md.active_map(coord) is fake)
coord_gm = types.SimpleNamespace(device=types.SimpleNamespace(
    status=types.SimpleNamespace(current_map=None), get_map=lambda i: fake if i == 0 else None))
check("reader: active_map falls back to get_map(0)", md.active_map(coord_gm) is fake)
check("reader: active_map(None device) -> None", md.active_map(types.SimpleNamespace(device=None)) is None)

# ---- pixel_type border band (numpy only) ----
if np is not None:
    # 4x3 raster indexed [x, y]. Seg 1: floor value 1, border value 101. Seg 2: 102 border.
    pt = np.array(
        [[1,   101, 1],    # x=0
         [1,   1,   101],  # x=1
         [102, 102, 2],    # x=2
         [2,   2,   2]],   # x=3
        dtype=np.uint8,
    )
    fake.pixel_type = pt
    dims = md._dims(fake)
    check("reader: dims parsed", dims["grid_size"] == 50 and dims["left"] == -1000 and dims["top"] == -2000)
    border = md._border_bbox_mm(fake, dims)
    check("reader: border band found for seg 1 and 2", 1 in border and 2 in border)
    # seg 1 border cells at (x,y) in {(0,1),(1,2)} -> count 2, mm via left+gx*gs / top+gy*gs
    check("reader: seg1 border cell count", border[1][4] == 2)
    check("reader: seg1 border x range mm", border[1][0] == -1000 + 0 * 50 and border[1][2] == -1000 + 1 * 50)
    check("reader: seg1 border y range mm", border[1][1] == -2000 + 1 * 50 and border[1][3] == -2000 + 2 * 50)
    rg = md.read_geometry.__doc__  # smoke: symbol exists
    check("reader: read_geometry present", isinstance(rg, str))
else:
    print("SKIP numpy not installed -- pixel_type border checks skipped")


print()
if fails:
    print(f"{len(fails)} FAILED:", *fails, sep="\n  ")
    sys.exit(1)
print("all edge-geometry checks passed")
