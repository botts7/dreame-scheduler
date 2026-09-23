"""Home Assistant Core API bridge for the Dreame Scheduler add-on backend.

The add-on *frontend* (browser, behind ingress) cannot reach
``http://supervisor/core/api`` — only the backend can, using the
``SUPERVISOR_TOKEN`` the Supervisor injects when ``homeassistant_api: true`` is
granted. This module isolates that Core-API I/O so the Flask routes stay thin.

Exposes exactly what the config GUI needs:
  * list_states()          — entities for the presence / door-sensor pickers
  * list_notify_services() — notify.* targets
  * get_config()           — the integration's current options + discovered
                             rooms / modes / suctions (pre-fill the GUI)
  * set_config()           — write a partial options object back (+ reload)
  * call_action()          — fire run_scheduled_now / run_catchup_now / reset_week

The SUPERVISOR_TOKEN is held here only and is NEVER returned to the browser.
Adapted from the wallbox_gateway add-on's ha_bridge.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

_INTEGRATION_DOMAIN = "dreame_scheduler"

_PREFIX_RE = re.compile(r"^[a-z0-9_]+$")
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _safe_prefix(prefix: str) -> str:
    """Reject anything that isn't a bare object_id prefix so it can't inject
    path/query segments into the Core API URL it's interpolated into."""
    if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
        raise CoreError(f"invalid prefix: {prefix!r}")
    return prefix


def _safe_entity(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not _ENTITY_RE.match(entity_id):
        raise CoreError(f"invalid entity_id: {entity_id!r}")
    return entity_id

# Domains the pickers care about: presence (person/device_tracker/group) and
# door contact sensors (binary_sensor). Keeps the /states payload small.
_PICKER_DOMAINS = ("person", "device_tracker", "group", "binary_sensor")

_ACTIONS = ("run_scheduled_now", "run_catchup_now", "reset_week",
            "apply_learned_nogo", "show_unreachable", "edge_clean")


@dataclass(frozen=True)
class CoreConfig:
    base_url: str
    token: str

    @property
    def available(self) -> bool:
        return bool(self.token)


class CoreUnavailable(Exception):
    """Raised when the Supervisor token / Core API isn't available."""


class CoreError(Exception):
    """Raised when the Core API returns an error response."""


def config_from_env() -> CoreConfig:
    # DS_HA_BASE_URL / DS_HA_TOKEN let local dev point at a real HA with a
    # long-lived token; otherwise use the Supervisor-injected token.
    base = os.environ.get("DS_HA_BASE_URL", "").strip() or "http://supervisor/core/api"
    token = os.environ.get("DS_HA_TOKEN", "").strip() or os.environ.get("SUPERVISOR_TOKEN", "").strip()
    return CoreConfig(base_url=base.rstrip("/"), token=token)


def _headers(cfg: CoreConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.token}", "Content-Type": "application/json"}


def _ensure(cfg: CoreConfig) -> None:
    if not cfg.available:
        raise CoreUnavailable("SUPERVISOR_TOKEN not set — is homeassistant_api granted?")


def list_states(cfg: CoreConfig, timeout: float = 8.0) -> list[dict[str, Any]]:
    """Picker-relevant entities, trimmed to what the GUI needs."""
    _ensure(cfg)
    try:
        r = requests.get(f"{cfg.base_url}/states", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    r.raise_for_status()
    out: list[dict[str, Any]] = []
    for s in r.json():
        eid = s.get("entity_id", "")
        domain = eid.split(".", 1)[0] if "." in eid else ""
        if domain not in _PICKER_DOMAINS:
            continue
        attrs = s.get("attributes") or {}
        out.append({
            "entity_id": eid,
            "state": s.get("state"),
            "name": attrs.get("friendly_name", eid),
            "device_class": attrs.get("device_class"),
            "domain": domain,
        })
    out.sort(key=lambda e: (e["domain"], str(e["name"]).lower()))
    return out


def list_notify_services(cfg: CoreConfig, timeout: float = 8.0) -> list[str]:
    """The user's notify.* services (bare names) for the notify-target picker."""
    _ensure(cfg)
    try:
        r = requests.get(f"{cfg.base_url}/services", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    r.raise_for_status()
    out = ["persistent_notification"]
    for d in r.json():
        if d.get("domain") == "notify":
            out.extend((d.get("services") or {}).keys())
    return sorted(set(out))


def _ws_url(cfg: CoreConfig) -> str:
    """Derive the Core WebSocket URL from the REST base_url."""
    u = cfg.base_url
    scheme = "wss" if u.startswith("https") else "ws"
    host_path = u.split("://", 1)[-1]
    if host_path.endswith("/core/api"):
        host_path = host_path[: -len("/core/api")] + "/core/websocket"
    elif host_path.endswith("/api"):
        host_path = host_path[: -len("/api")] + "/api/websocket"
    else:
        host_path = host_path.rstrip("/") + "/api/websocket"
    return f"{scheme}://{host_path}"


# Entity domains worth overlaying on a floor plan (controls + sensors).
_OVERLAY_DOMAINS = (
    "light", "switch", "fan", "cover", "climate", "media_player", "lock",
    "vacuum", "scene", "script", "button", "input_boolean",
    "sensor", "binary_sensor",
)


def list_areas(cfg: CoreConfig, timeout: float = 8.0) -> list[dict[str, Any]]:
    """HA Areas with the entities assigned to each (entity's own area, else its
    device's area) — via the WS registries. Returns
    [{area_id, name, entities:[{entity_id, name, domain, state, device_class}]}].
    Only areas that have overlay-worthy entities are returned."""
    _ensure(cfg)
    try:
        import websocket  # websocket-client; installed in the add-on image
    except Exception as e:  # noqa: BLE001
        raise CoreError("websocket-client not available: " + str(e))

    # Current states (REST) for value/name/domain.
    states: dict[str, Any] = {}
    try:
        r = requests.get(f"{cfg.base_url}/states", headers=_headers(cfg), timeout=timeout)
        r.raise_for_status()
        for s in r.json():
            states[s.get("entity_id")] = s
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e

    def _recv(ws):
        return json.loads(ws.recv())

    try:
        ws = websocket.create_connection(_ws_url(cfg), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise CoreUnavailable("ws connect failed: " + str(e))
    try:
        if _recv(ws).get("type") != "auth_required":
            raise CoreError("ws: no auth_required")
        ws.send(json.dumps({"type": "auth", "access_token": cfg.token}))
        if _recv(ws).get("type") != "auth_ok":
            raise CoreError("ws: auth failed")

        def _q(i, t):
            ws.send(json.dumps({"id": i, "type": t}))
            while True:
                m = _recv(ws)
                if m.get("id") == i and m.get("type") == "result":
                    return (m.get("result") or []) if m.get("success") else []

        areas = _q(1, "config/area_registry/list")
        ents = _q(2, "config/entity_registry/list")
        devs = _q(3, "config/device_registry/list")
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass

    dev_area = {d.get("id"): d.get("area_id") for d in devs}
    out = {a.get("area_id"): {"area_id": a.get("area_id"), "name": a.get("name"), "entities": []}
           for a in areas}
    for e in ents:
        eid = e.get("entity_id", "")
        dom = eid.split(".", 1)[0] if "." in eid else ""
        if dom not in _OVERLAY_DOMAINS or e.get("hidden_by") or e.get("disabled_by"):
            continue
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        if not aid or aid not in out:
            continue
        st = states.get(eid) or {}
        attrs = st.get("attributes") or {}
        out[aid]["entities"].append({
            "entity_id": eid, "domain": dom,
            "name": attrs.get("friendly_name") or e.get("name") or eid,
            "state": st.get("state"), "device_class": attrs.get("device_class"),
        })
    res = [a for a in out.values() if a["entities"]]
    res.sort(key=lambda a: str(a["name"] or "").lower())
    for a in res:
        a["entities"].sort(key=lambda x: (x["domain"], str(x["name"]).lower()))
    return res


def get_config(cfg: CoreConfig, vacuum: str | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Call dreame_scheduler.get_config and return its response data."""
    _ensure(cfg)
    body: dict[str, Any] = {}
    if vacuum:
        body["vacuum"] = vacuum
    try:
        r = requests.post(
            f"{cfg.base_url}/services/{_INTEGRATION_DOMAIN}/get_config?return_response",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"get_config HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    # HA wraps service responses as {"service_response": {...}}.
    if isinstance(data, dict) and "service_response" in data:
        return data["service_response"] or {}
    return data if isinstance(data, dict) else {}


def get_report(cfg: CoreConfig, vacuum: str | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Call dreame_scheduler.get_report — today/week per-room status + coverage."""
    _ensure(cfg)
    body: dict[str, Any] = {}
    if vacuum:
        body["vacuum"] = vacuum
    try:
        r = requests.post(
            f"{cfg.base_url}/services/{_INTEGRATION_DOMAIN}/get_report?return_response",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"get_report HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if isinstance(data, dict) and "service_response" in data:
        return data["service_response"] or {}
    return data if isinstance(data, dict) else {}


def set_config(cfg: CoreConfig, options: dict[str, Any], vacuum: str | None = None, timeout: float = 10.0) -> None:
    """Call dreame_scheduler.set_config to merge options + reload."""
    _ensure(cfg)
    body: dict[str, Any] = {"options": options}
    if vacuum:
        body["vacuum"] = vacuum
    try:
        r = requests.post(
            f"{cfg.base_url}/services/{_INTEGRATION_DOMAIN}/set_config",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"set_config HTTP {r.status_code}: {r.text[:200]}")


def get_map(cfg: CoreConfig, prefix: str, timeout: float = 8.0) -> dict[str, Any]:
    """Room geometry + live robot/dock positions from the vacuum's map camera —
    the data needed to draw a floor plan. Only the geometry/label fields are
    passed through (the camera's attributes also carry big history-image blobs
    we don't want to ship to the browser)."""
    _ensure(cfg)
    prefix = _safe_prefix(prefix)
    try:
        r = requests.get(f"{cfg.base_url}/states/camera.{prefix}_map", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"map HTTP {r.status_code}: {r.text[:200]}")
    a = (r.json() or {}).get("attributes") or {}
    keep = ("x0", "y0", "x1", "y1", "x", "y", "room_id", "name", "custom_name",
            "icon", "color_index", "floor_material")
    rooms = {}
    for rid, rm in (a.get("rooms") or {}).items():
        if isinstance(rm, dict):
            rooms[str(rid)] = {k: rm.get(k) for k in keep if k in rm}
    def _lst(key):
        return a.get(key) if isinstance(a.get(key), list) else []

    return {
        "rooms": rooms,
        "vacuum_position": a.get("vacuum_position"),
        "charger_position": a.get("charger_position"),
        "rotation": a.get("rotation", 0),
        "updated_at": a.get("updated_at"),
        "furnitures": _lst("furnitures"),
        "virtual_walls": _lst("virtual_walls"),
        "no_go_areas": _lst("no_go_areas"),
        "no_mopping_areas": _lst("no_mopping_areas"),
        "virtual_thresholds": _lst("virtual_thresholds"),
        "vacuum_entity": f"vacuum.{prefix}",
        "map_id": a.get("map_id"),                 # needed for split/merge segment writes
        # for the map-image underlay + (future) exact auto-align:
        "has_image": bool(a.get("entity_picture")),
        "calibration_points": a.get("calibration_points"),
    }


def get_map_image(cfg: CoreConfig, prefix: str, timeout: float = 10.0) -> tuple[bytes, str]:
    """Fetch the rendered map PNG from the camera. Proxied through the add-on
    (with the supervisor token) so the browser can show it under the room boxes
    without juggling the camera's signed access token."""
    _ensure(cfg)
    prefix = _safe_prefix(prefix)
    try:
        r = requests.get(f"{cfg.base_url}/camera_proxy/camera.{prefix}_map", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"map_image HTTP {r.status_code}")
    return r.content, r.headers.get("Content-Type", "image/png")


def backup_map(cfg: CoreConfig, vacuum_entity: str, timeout: float = 15.0) -> None:
    """Back up the current map BEFORE any write, so it can be restored."""
    _ensure(cfg)
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_backup_map",
            headers=_headers(cfg), json={"entity_id": vacuum_entity}, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"backup_map HTTP {r.status_code}: {r.text[:200]}")


def set_restricted_zone(cfg: CoreConfig, vacuum_entity: str, walls, zones, no_mops,
                        timeout: float = 15.0) -> None:
    """Write virtual walls + no-go + no-mop zones to the robot's cloud map.
    Each item is [x0,y0,x1,y1] in the map's mm coordinate frame. Replaces the
    whole set, so the caller passes the full lists."""
    _ensure(cfg)
    body = {"entity_id": vacuum_entity, "walls": walls or [], "zones": zones or [], "no_mops": no_mops or []}
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_set_restricted_zone",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"set_restricted_zone HTTP {r.status_code}: {r.text[:200]}")


def rename_segment(cfg: CoreConfig, vacuum_entity: str, segment_id, name: str,
                   timeout: float = 15.0) -> None:
    """Rename a room SEGMENT on the robot's own map (syncs to the Dreame app).
    First converter in the diff→robot-writes pipeline: a changed room name maps
    directly to dreame_vacuum.vacuum_rename_segment."""
    _ensure(cfg)
    body = {"entity_id": vacuum_entity, "segment_id": int(segment_id), "segment_name": str(name)}
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_rename_segment",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"rename_segment HTTP {r.status_code}: {r.text[:200]}")


def split_segment(cfg: CoreConfig, vacuum_entity: str, map_id, segment, line,
                  timeout: float = 20.0) -> None:
    """Split a room segment along a straight line on the robot's own map.
    line = [x1,y1,x2,y2] in the map's mm frame (same as room coords), and must
    cross the selected room. Maps a user-drawn divider → the split's `line`."""
    _ensure(cfg)
    body = {"entity_id": vacuum_entity, "segment": int(segment),
            "line": [int(round(v)) for v in line]}
    if map_id is not None:
        body["map_id"] = int(map_id)
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_split_segments",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"split_segments HTTP {r.status_code}: {r.text[:200]}")


def merge_segments(cfg: CoreConfig, vacuum_entity: str, segments,
                   timeout: float = 20.0) -> None:
    """Merge two ADJACENT room segments on the robot's own map. segments =
    [keep_id, absorb_id] — the first is kept, the second is deleted. map_id is
    omitted (the device uses the current map; passing the live id 500s)."""
    _ensure(cfg)
    body = {"entity_id": vacuum_entity, "segments": [int(s) for s in segments][:2]}
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_merge_segments",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"merge_segments HTTP {r.status_code}: {r.text[:200]}")


def clean_segments(cfg: CoreConfig, vacuum_entity: str, segments, timeout: float = 15.0) -> None:
    """Send the robot to clean specific room segments (tap-a-room on the plan).
    Routed through the SCHEDULER's dreame_scheduler.clean_rooms (a manual override
    that drops any presence-paused run and is exempt from return-on-arrival), so
    'someone home' doesn't dock it — rather than calling the raw vacuum service."""
    _ensure(cfg)
    segs = [int(s) for s in segments]
    if not segs:
        raise CoreError("no segments")
    # Target this scheduler's own vacuum so a multi-robot home cleans the room
    # on the RIGHT machine (the service otherwise fans out to every entry).
    body = {"segments": segs}
    if vacuum_entity:
        body["vacuum"] = vacuum_entity
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_scheduler/clean_rooms",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"clean_rooms HTTP {r.status_code}: {r.text[:200]}")


# Upper bound on zones we'll hand the robot in one clean_zone task. A generous
# safety cap only — the real per-task limit is the robot firmware's (unknown
# until tested), and the Edge-clean UI batches/limits well under this.
MAX_CLEAN_ZONES = 32


def clean_zones(cfg: CoreConfig, vacuum_entity: str, zones, repeats: int = 1,
                timeout: float = 15.0) -> None:
    """Zone-clean a list of axis-aligned rectangles [x0,y0,x1,y1] in ONE task —
    used by Edge clean (thin strips hugging the walls). Calls the robot's own
    vacuum_clean_zone directly; note a later clean_zone REPLACES this task, so all
    the strips for one pass must come in a single call (hence the firmware zone
    cap matters)."""
    _ensure(cfg)
    ve = _safe_entity(vacuum_entity)
    if ve.split(".", 1)[0] != "vacuum":
        raise CoreError(f"{ve} is not a vacuum entity")
    rects: list[list[int]] = []
    for z in (zones or []):
        if not (isinstance(z, (list, tuple)) and len(z) == 4):
            raise CoreError("each zone must be [x0,y0,x1,y1]")
        x0, y0, x1, y1 = (int(round(float(v))) for v in z)
        rects.append([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])
    if not rects:
        raise CoreError("no zones")
    if len(rects) > MAX_CLEAN_ZONES:
        raise CoreError(f"too many zones ({len(rects)} > {MAX_CLEAN_ZONES})")
    rp = max(1, min(3, int(repeats)))
    try:
        r = requests.post(
            f"{cfg.base_url}/services/dreame_vacuum/vacuum_clean_zone",
            headers=_headers(cfg),
            json={"entity_id": ve, "zone": rects, "repeats": rp},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"clean_zone HTTP {r.status_code}: {r.text[:200]}")


def _rooms_snapshot(cfg: CoreConfig, prefix: str) -> dict:
    return get_map(cfg, prefix).get("rooms") or {}


def _wait_new_id(cfg: CoreConfig, prefix: str, before: set, timeout: float = 45.0) -> str:
    """After a split, poll the map until the robot assigns the new piece an id."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.5)
        cur = _rooms_snapshot(cfg, prefix)
        new_ids = [k for k in cur if k not in before]
        if new_ids:
            return new_ids[0]
    raise CoreError("split sent but no new room appeared — check the Dreame app; "
                    "the map was backed up before this batch")


def _wait_gone(cfg: CoreConfig, prefix: str, seg, timeout: float = 30.0) -> bool:
    """Best-effort: poll until a merged-away segment disappears from the map."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.5)
        if str(seg) not in _rooms_snapshot(cfg, prefix):
            return True
    return False


def _resolve_at(rooms: dict, ids, x, y) -> int:
    """Which of `ids` is at map point (x,y)? Smallest containing bbox wins (the
    same rule the GUI uses), restricted to the candidate set so overlapping
    bboxes of unrelated rooms can't steal the match."""
    cands = []
    for i in ids:
        r = rooms.get(str(i))
        if r and r.get("x0") is not None and r["x0"] <= x <= r["x1"] and r["y0"] <= y <= r["y1"]:
            cands.append((int(i), (r["x1"] - r["x0"]) * (r["y1"] - r["y0"])))
    if not cands:
        raise CoreError(f"no candidate room at ({int(x)},{int(y)})")
    cands.sort(key=lambda t: t[1])
    return cands[0][0]


def _resolve_at_live(cfg: CoreConfig, prefix: str, ids, x, y, timeout: float = 15.0) -> int:
    """_resolve_at with retries on fresh snapshots — right after a split the
    camera's bboxes can lag behind (old piece already shrunk, new piece not yet
    settled), so a single read can transiently match nothing."""
    deadline = time.time() + timeout
    while True:
        try:
            return _resolve_at(_rooms_snapshot(cfg, prefix), ids, x, y)
        except CoreError:
            if time.time() >= deadline:
                raise
            time.sleep(2.5)


def _carve(cfg: CoreConfig, vacuum_entity: str, prefix: str, donor: int, receiver: int,
           rect, log: list) -> None:
    """Carve a drawn rectangle out of `donor` and give it to `receiver`,
    compiled at runtime into full-crossing splits + rejoin merges + a hand-over
    merge. Runtime resolution is essential: which piece keeps which id is only
    knowable from the map after each split."""
    TOL = 250                                       # rect edge ~at the wall → no cut needed
    rooms = _rooms_snapshot(cfg, prefix)
    d = rooms.get(str(donor))
    if not d:
        raise CoreError(f"segment {donor} not on the map")
    x0, y0 = max(min(rect[0], rect[2]), d["x0"]), max(min(rect[1], rect[3]), d["y0"])
    x1, y1 = min(max(rect[0], rect[2]), d["x1"]), min(max(rect[1], rect[3]), d["y1"])
    if x1 - x0 < 400 or y1 - y0 < 400:
        raise CoreError("carve chunk too small (or outside the room)")
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cuts = []
    if y0 > d["y0"] + TOL: cuts.append(("h", y0))
    if y1 < d["y1"] - TOL: cuts.append(("h", y1))
    if x0 > d["x0"] + TOL: cuts.append(("v", x0))
    if x1 < d["x1"] - TOL: cuts.append(("v", x1))
    if not cuts:                                    # the "chunk" is the whole room
        merge_segments(cfg, vacuum_entity, [receiver, donor])
        _wait_gone(cfg, prefix, donor)
        log.append({"op": "carve", "note": "rect covered the whole room — plain merge",
                    "keep": receiver, "absorb": donor})
        return

    descendants = {int(donor)}                      # donor + every piece the cuts create
    try:
        for axis, v in cuts:
            target = _resolve_at_live(cfg, prefix, descendants, cx, cy)  # cut the piece holding the chunk
            rooms = _rooms_snapshot(cfg, prefix)
            tb = rooms[str(target)]
            line = ([tb["x0"] - 500, v, tb["x1"] + 500, v] if axis == "h"
                    else [v, tb["y0"] - 500, v, tb["y1"] + 500])
            before = set(rooms)
            split_segment(cfg, vacuum_entity, None, target, [int(round(n)) for n in line])
            new_id = _wait_new_id(cfg, prefix, before)
            descendants.add(int(new_id))
            log.append({"op": "carve.split", "target": target, "axis": axis, "at": int(v), "new_id": new_id})
        rooms = _rooms_snapshot(cfg, prefix)
        chunk = _resolve_at_live(cfg, prefix, descendants, cx, cy)
    except CoreError as e:
        # Something went wrong mid-cuts (e.g. the rect wasn't on real floor, so
        # no piece contains its centre). FOLD the pieces back into the donor so
        # the room isn't left cut up, then report.
        for pid in sorted(descendants - {int(donor)}):
            try:
                merge_segments(cfg, vacuum_entity, [int(donor), int(pid)])
                _wait_gone(cfg, prefix, pid)
                log.append({"op": "carve.foldback", "keep": int(donor), "absorb": int(pid)})
            except Exception:  # noqa: BLE001
                pass
        raise CoreError("carve failed (is the box on the room's actual floor?) — "
                        "pieces folded back into the room; the map was backed up. "
                        f"Detail: {e}") from e
    others = [i for i in descendants if i != chunk and str(i) in rooms]
    warn_id_moved = (chunk == int(donor))           # rare: the original id landed on the chunk
    area = lambda i: (rooms[str(i)]["x1"] - rooms[str(i)]["x0"]) * (rooms[str(i)]["y1"] - rooms[str(i)]["y0"])  # noqa: E731
    base = int(donor) if int(donor) in others else (max(others, key=area) if others else None)

    if base is not None:                            # fold the non-chunk pieces back together
        ctr = lambda i: ((rooms[str(i)]["x0"] + rooms[str(i)]["x1"]) / 2, (rooms[str(i)]["y0"] + rooms[str(i)]["y1"]) / 2)  # noqa: E731
        bx, by = ctr(base)
        rest = sorted((i for i in others if i != base),
                      key=lambda i: (ctr(i)[0] - bx) ** 2 + (ctr(i)[1] - by) ** 2)
        pending, retried = list(rest), set()
        while pending:
            i = pending.pop(0)
            try:
                merge_segments(cfg, vacuum_entity, [base, i])
                # the robot IGNORES a non-neighbour merge without erroring, so a
                # 200 isn't enough — confirm the piece actually disappeared
                if not _wait_gone(cfg, prefix, i):
                    raise CoreError(f"rejoin of piece {i} didn't take")
                log.append({"op": "carve.rejoin", "keep": base, "absorb": i})
            except CoreError:
                if i in retried:                    # adjacency never came good
                    raise
                retried.add(i)
                pending.append(i)                   # try again after the others merged

    gave = False
    try:
        merge_segments(cfg, vacuum_entity, [int(receiver), int(chunk)])
        gave = _wait_gone(cfg, prefix, chunk)       # silent-refusal check (non-neighbours)
    except CoreError:
        gave = False
    if not gave:
        if base is not None:                        # don't leave the room cut up
            try:
                merge_segments(cfg, vacuum_entity, [base, int(chunk)])
                _wait_gone(cfg, prefix, chunk)
                log.append({"op": "carve.foldback", "keep": base, "absorb": int(chunk)})
            except Exception:  # noqa: BLE001
                pass
        raise CoreError("carve hand-over failed — the chunk must actually touch the "
                        "receiving room (no wall between). Chunk folded back into the "
                        "donor; the map was backed up.")
    log.append({"op": "carve.give", "keep": int(receiver), "absorb": int(chunk),
                "warn_id_moved": warn_id_moved})


def apply_segment_ops(cfg: CoreConfig, vacuum_entity: str, prefix: str, ops: list) -> dict:
    """Run a STAGED batch of segment edits as one bulk write to the robot —
    the user edits locally (instant), then this applies the compiled native ops
    in order: {op:"split", segment, line[4]} · {op:"merge", keep, absorb} ·
    {op:"carve", donor, receiver, rect[4]} · {op:"rename", segment, name}.
    `absorb`/`segment` may be "$new" = the id the
    robot assigned to the most recent split's new piece (discovered by polling
    the map, since split returns nothing).

    The map is backed up ONCE, up front — per-op backups would overwrite the
    pre-batch backup with an intermediate state and ruin rollback."""
    log: list[dict] = []
    last_new: str | None = None

    def _resolve(v):
        if v == "$new":
            if last_new is None:
                raise CoreError("op references $new but no split has run yet")
            return int(last_new)
        return int(v)

    backup_map(cfg, vacuum_entity)
    baseline = _rooms_snapshot(cfg, prefix)

    for i, op in enumerate(ops):
        kind = (op.get("op") or "").strip()
        if kind == "split":
            seg = _resolve(op.get("segment"))
            before = set(_rooms_snapshot(cfg, prefix))
            split_segment(cfg, vacuum_entity, None, seg, op.get("line")[:4])
            last_new = _wait_new_id(cfg, prefix, before)
            log.append({"op": "split", "segment": seg, "new_id": last_new,
                        "kept_original": str(seg) in _rooms_snapshot(cfg, prefix)})
        elif kind == "merge":
            keep, absorb = _resolve(op.get("keep")), _resolve(op.get("absorb"))
            merge_segments(cfg, vacuum_entity, [keep, absorb])
            # the robot silently ignores non-neighbour merges — confirm it took
            if not _wait_gone(cfg, prefix, absorb):
                raise CoreError(f"op {i + 1}: merge {absorb}→{keep} didn't take — the rooms "
                                "must be neighbours (no wall between). Map was backed up.")
            log.append({"op": "merge", "keep": keep, "absorb": absorb, "confirmed": True})
        elif kind == "carve":
            _carve(cfg, vacuum_entity, prefix,
                   _resolve(op.get("donor")), _resolve(op.get("receiver")),
                   op.get("rect")[:4], log)
        elif kind == "rename":
            seg = _resolve(op.get("segment"))
            rename_segment(cfg, vacuum_entity, seg, str(op.get("name") or ""))
            log.append({"op": "rename", "segment": seg, "name": op.get("name")})
        else:
            raise CoreError(f"op {i + 1}: unknown op '{kind}'")

    return {"log": log, "rooms_before": len(baseline),
            "rooms_after": len(_rooms_snapshot(cfg, prefix))}


def call_action(cfg: CoreConfig, action: str, data: dict | None = None, timeout: float = 10.0) -> None:
    """Fire a whitelisted scheduler service (run now / catch-up / reset)."""
    _ensure(cfg)
    if action not in _ACTIONS:
        raise CoreError(f"action '{action}' not allowed")
    try:
        r = requests.post(
            f"{cfg.base_url}/services/{_INTEGRATION_DOMAIN}/{action}",
            headers=_headers(cfg), json=data or {}, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"{action} HTTP {r.status_code}: {r.text[:200]}")


def _find_manual_todo(cfg: CoreConfig, prefix: str, timeout: float = 8.0) -> str | None:
    """The integration's own 'clean by hand' to-do entity for this vacuum. Its
    entity_id embeds the prefix + 'clean_by_hand'; match on both so a multi-robot
    home ticks the right list."""
    prefix = _safe_prefix(prefix)
    try:
        r = requests.get(f"{cfg.base_url}/states", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    r.raise_for_status()
    for s in r.json():
        eid = s.get("entity_id", "")
        if eid.startswith("todo.") and "clean_by_hand" in eid and prefix in eid:
            return eid
    return None


def manual_done(cfg: CoreConfig, prefix: str, seg, timeout: float = 10.0) -> None:
    """Mark a 'clean by hand' room done from the GUI — the same as ticking it in
    HA's to-do panel. Credits the room as cleaned and drops it from the list (the
    integration's async_manual_room_done, reached via todo.update_item). Matched
    by the item's stable uid (seg_<n>), not its wording, so it can't drift."""
    _ensure(cfg)
    eid = _find_manual_todo(cfg, prefix)
    if not eid:
        raise CoreError("clean-by-hand to-do entity not found for this vacuum")
    body = {"entity_id": eid, "item": f"seg_{int(seg)}", "status": "completed"}
    try:
        r = requests.post(
            f"{cfg.base_url}/services/todo/update_item",
            headers=_headers(cfg), json=body, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"manual_done HTTP {r.status_code}: {r.text[:200]}")


# Allow-list of control services the floor-plan overlays may call, per domain.
_SERVICE_ALLOW = {
    "light": {"toggle", "turn_on", "turn_off"},
    "switch": {"toggle", "turn_on", "turn_off"},
    "fan": {"toggle", "turn_on", "turn_off"},
    "input_boolean": {"toggle", "turn_on", "turn_off"},
    "cover": {"toggle", "open_cover", "close_cover", "stop_cover"},
    "lock": {"lock", "unlock"},
    "climate": {"turn_on", "turn_off"},
    "media_player": {"media_play_pause", "volume_up", "volume_down", "turn_on", "turn_off"},
    "scene": {"turn_on"},
    "script": {"turn_on"},
    "button": {"press"},
    "vacuum": {"start", "pause", "return_to_base"},
}


def call_service(cfg: CoreConfig, domain: str, service: str, entity_id: str, timeout: float = 10.0) -> None:
    """Call an allow-listed control service on an entity (from a floor-plan tap)."""
    _ensure(cfg)
    if domain not in _SERVICE_ALLOW or service not in _SERVICE_ALLOW[domain]:
        raise CoreError(f"service {domain}.{service} not allowed")
    entity_id = _safe_entity(entity_id)          # single entity only — no "all"/comma lists
    if entity_id.split(".", 1)[0] != domain:     # and its domain must match the service
        raise CoreError(f"{entity_id} is not a {domain} entity")
    try:
        r = requests.post(
            f"{cfg.base_url}/services/{domain}/{service}",
            headers=_headers(cfg), json={"entity_id": entity_id}, timeout=timeout,
        )
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"{domain}.{service} HTTP {r.status_code}: {r.text[:200]}")


def notify_test(cfg: CoreConfig, targets: list[str], timeout: float = 10.0) -> int:
    """Send a test notification to the given notify targets so the user can
    verify their channel renders correctly (icons/dashes). Returns count sent."""
    _ensure(cfg)
    valid = set(list_notify_services(cfg)) | {"persistent_notification"}
    title = "🧹 Dreame Scheduler test"
    message = "Notifications are working. If the ✅ 🟡 🔴 — icons look right, you're all set."
    sent = 0
    for raw in (targets or ["persistent_notification"]):
        t = re.sub(r"[^a-z0-9_]", "", str(raw).lower())   # safe service name
        if not t or t not in valid:
            continue
        try:
            r = requests.post(
                f"{cfg.base_url}/services/notify/{t}",
                headers=_headers(cfg), json={"title": title, "message": message}, timeout=timeout,
            )
        except requests.RequestException as e:
            raise CoreUnavailable(str(e)) from e
        if r.status_code < 400:
            sent += 1
    return sent


def get_state(cfg: CoreConfig, entity_id: str, timeout: float = 6.0) -> dict[str, Any]:
    """Current state of one entity (for live overlay refresh)."""
    _ensure(cfg)
    entity_id = _safe_entity(entity_id)
    try:
        r = requests.get(f"{cfg.base_url}/states/{entity_id}", headers=_headers(cfg), timeout=timeout)
    except requests.RequestException as e:
        raise CoreUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise CoreError(f"state HTTP {r.status_code}")
    s = r.json()
    a = s.get("attributes") or {}
    return {"entity_id": s.get("entity_id"), "state": s.get("state"),
            "name": a.get("friendly_name"), "device_class": a.get("device_class")}
