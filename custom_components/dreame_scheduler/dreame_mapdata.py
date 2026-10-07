"""Read the ``dreame_vacuum`` coordinator's in-memory ``MapData`` for the real
per-room wall geometry edge clean needs -- the firmware's own walls, not room
bounding boxes (which bleed across open doorways).

Two geometry sources live in the decoded ``MapData`` and are NOT exposed as
camera attributes, so we reach the coordinator object directly:

  * ``MapData.walls`` -- a dict ``{segment_id: [Line(x0,y0,x1,y1), ...]}`` of the
    room's real wall segments (present when the firmware reports walls_v3).
  * ``MapData.pixel_type`` -- a numpy raster where a cell ``== 100 + seg`` or
    ``== 200 + seg`` is that room's wall/border band (``== seg`` is its floor).
    Confirmed from the installed library: frame_type I v3,
    ``segment_id = pixel & 0x1F``, ``wall = (pixel >> 5) & 0x03`` (map.py).

Access path, all public: entity registry -> the vacuum entity's config entry ->
``hass.data["dreame_vacuum"][entry_id]`` (the coordinator) ->
``coordinator.device.status.current_map`` (the active MapData). The mm transform
for a raster cell ``(gx, gy)`` is ``mm = (left + gx*grid_size, top + gy*grid_size)``
-- the same transform the base integration uses to place room boxes, so the
result is in the ``vacuum_clean_zone`` coordinate space.

Everything is defensive getattr/try: a shape change in the base integration must
degrade to "no geometry", never raise into the scheduler.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

try:  # numpy ships with dreame_vacuum; still optional here (pixel_type path only)
    import numpy as _np
except Exception:  # noqa: BLE001  pragma: no cover
    _np = None

try:  # package context (production); bare fallback for standalone file-load tests
    from .edge_geometry import strips_from_walls, batch_rects, merge_colinear_walls
except ImportError:  # pragma: no cover
    from edge_geometry import strips_from_walls, batch_rects, merge_colinear_walls


def resolve_coordinator(hass, vacuum_entity_id):
    """The ``dreame_vacuum`` DataUpdateCoordinator backing ``vacuum_entity_id``, or
    None. Resolved via the entity registry (entity -> config_entry_id ->
    ``hass.data[dreame_vacuum][entry_id]``), falling back to the sole coordinator
    if exactly one is loaded."""
    from .const import DREAME_DOMAIN  # lazy: keeps this module file-loadable in tests
    try:
        from homeassistant.helpers import entity_registry as er
        bucket = hass.data.get(DREAME_DOMAIN) or {}
        if not bucket:
            return None
        ent = er.async_get(hass).async_get(vacuum_entity_id)
        if ent is not None and ent.config_entry_id in bucket:
            return bucket[ent.config_entry_id]
        coords = [c for c in bucket.values() if getattr(c, "device", None) is not None]
        return coords[0] if len(coords) == 1 else None
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("dreame coordinator resolve failed: %s", exc)
        return None


def active_map(coordinator):
    """The active (current) ``MapData`` on the coordinator's device, or None."""
    dev = getattr(coordinator, "device", None)
    if dev is None:
        return None
    status = getattr(dev, "status", None)
    md = getattr(status, "current_map", None) if status is not None else None
    if md is None:
        get_map = getattr(dev, "get_map", None)
        if callable(get_map):
            try:
                md = get_map(0)
            except Exception:  # noqa: BLE001
                md = None
    return md


def _dims(md):
    d = getattr(md, "dimensions", None)
    if d is None:
        return None
    try:
        return {
            "grid_size": float(getattr(d, "grid_size")),
            "left": float(getattr(d, "left")),
            "top": float(getattr(d, "top")),
            "width": int(getattr(d, "width")),
            "height": int(getattr(d, "height")),
        }
    except Exception:  # noqa: BLE001
        return None


def _segment_boxes(md):
    """``{seg_id: (name, (x0,y0,x1,y1))}`` from ``MapData.segments`` (mm). The box
    is the room's bounding rectangle -- used only to sanity-check wall units, not
    for the strips themselves."""
    segments = getattr(md, "segments", None)
    out = {}
    if not isinstance(segments, dict):
        return out
    for sid, seg in segments.items():
        try:
            name = getattr(seg, "custom_name", None) or getattr(seg, "name", None) or str(sid)
            box = (float(seg.x0), float(seg.y0), float(seg.x1), float(seg.y1))
        except Exception:  # noqa: BLE001
            continue
        out[int(sid)] = (str(name), box)
    return out


def _walls_mm(md):
    """``{seg_id: [(x0,y0,x1,y1), ...]}`` from ``MapData.walls`` (Line objects), as
    stored. Empty if the firmware did not report walls (older protocol)."""
    walls = getattr(md, "walls", None)
    out = {}
    if not isinstance(walls, dict):
        return out
    for sid, lines in walls.items():
        segs = []
        for ln in (lines or []):
            try:
                segs.append((float(ln.x0), float(ln.y0), float(ln.x1), float(ln.y1)))
            except Exception:  # noqa: BLE001
                continue
        if segs:
            out[int(sid)] = segs
    return out


def _border_bbox_mm(md, dims):
    """Per-segment bounding box (mm) + cell count of the wall/border band from
    ``pixel_type``: cells whose value is ``100+seg`` or ``200+seg``.
    ``{seg_id: (x0,y0,x1,y1,cells)}``. Needs numpy and a raster; empty otherwise.

    This is a diagnostic/fallback summary -- it confirms the raster is present and
    lets us compare its mm extent to the wall coords. Turning the band into tight
    strips is a later step; the primary path uses ``walls``."""
    if _np is None or dims is None:
        return {}
    arr = getattr(md, "pixel_type", None)
    if arr is None:
        return {}
    try:
        a = _np.asarray(arr)
    except Exception:  # noqa: BLE001
        return {}
    if a.ndim != 2:
        return {}
    gs, left, top = dims["grid_size"], dims["left"], dims["top"]
    out = {}
    for v in _np.unique(a):
        iv = int(v)
        if 100 < iv < 164:
            seg = iv - 100
        elif 200 < iv < 264:
            seg = iv - 200
        else:
            continue
        xs, ys = _np.where(a == iv)     # pixel_type is indexed [x, y]
        if xs.size == 0:
            continue
        x0 = left + float(xs.min()) * gs
        x1 = left + float(xs.max()) * gs
        y0 = top + float(ys.min()) * gs
        y1 = top + float(ys.max()) * gs
        cnt = int(xs.size)
        prev = out.get(seg)
        if prev:
            x0, y0 = min(x0, prev[0]), min(y0, prev[1])
            x1, y1 = max(x1, prev[2]), max(y1, prev[3])
            cnt += prev[4]
        out[seg] = (x0, y0, x1, y1, cnt)
    return out


def read_geometry(hass, vacuum_entity_id):
    """Inspect the active MapData and return the real geometry edge clean can use.

    ``{"present": False, "reason": ...}`` when no coordinator/map is available,
    else a dict with ``dims``, ``segments`` ``{seg:(name,box)}``, ``walls``
    ``{seg:[(x0,y0,x1,y1)...]}``, ``wall_count``, ``has_pixel_type`` and
    ``border_bbox`` ``{seg:(x0,y0,x1,y1,cells)}``. Read-only."""
    coord = resolve_coordinator(hass, vacuum_entity_id)
    if coord is None:
        return {"present": False, "reason": "no_coordinator"}
    md = active_map(coord)
    if md is None:
        return {"present": False, "reason": "no_map"}
    dims = _dims(md)
    walls = _walls_mm(md)
    return {
        "present": True,
        "dims": dims,
        "segments": _segment_boxes(md),
        "walls": walls,
        "wall_count": sum(len(v) for v in walls.values()),
        "has_pixel_type": _np is not None and getattr(md, "pixel_type", None) is not None,
        "border_bbox": _border_bbox_mm(md, dims),
    }


def edge_strips_for_segments(geom, seg_ids, width, cap=32):
    """Zone-clean batches of wall strips for ``seg_ids``, built from the real
    ``walls`` geometry. Returns ``(batches, source)`` where ``source`` is
    ``"walls"`` when real walls produced strips, else ``"none"``. (A ``pixel_type``
    border-band path is a later addition, hence the explicit source.)"""
    walls = (geom or {}).get("walls") or {}
    rects = []
    for s in (seg_ids or []):
        try:
            key = int(s)
        except (TypeError, ValueError):
            continue
        seg_walls = walls.get(key)
        if seg_walls:
            rects.extend(strips_from_walls(merge_colinear_walls(seg_walls), width))
    if rects:
        return batch_rects(rects, cap), "walls"
    return [], "none"
