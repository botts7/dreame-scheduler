"""Flask entrypoint for the Dreame Scheduler HA add-on.

A thin config GUI behind HA ingress. All persistent config lives in the
dreame_scheduler integration; this backend just reads/writes it through the
Core API (using SUPERVISOR_TOKEN, never exposed to the browser) and serves the
single-page UI.

Run via the s6 service in rootfs/etc/services.d/dreame_scheduler/run.
Local dev: DS_HA_BASE_URL=http://homeassistant.local:8123/api DS_HA_TOKEN=... python3 server.py
"""

from __future__ import annotations

import base64
import logging
import os
import re
import threading
from typing import Tuple

from flask import Flask, jsonify, render_template, request, Response

import ha_bridge

# Dev live-reload: when DS_UI_DIR is set (by the add-on run script), serve the
# templates/static from there (an SMB-editable /share folder) so UI tweaks need
# only a browser refresh. Unset -> serve the baked-in copies (shipping mode).
_UI_DIR = os.environ.get("DS_UI_DIR", "").strip()
_TPL = os.path.join(_UI_DIR, "templates") if _UI_DIR else "templates"
_STC = os.path.join(_UI_DIR, "static") if _UI_DIR else "static"
app = Flask(__name__, template_folder=_TPL, static_folder=_STC)
if _UI_DIR:                          # dev live-reload only; shipping serves cached assets
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dreame-scheduler-addon")

_STATIC_DIR = _STC if os.path.isabs(_STC) else os.path.join(os.path.dirname(os.path.abspath(__file__)), _STC)

# Reject bodies larger than 16 MB before Flask buffers them (the plan upload is
# capped at 8 MB after decode; this bounds every route against a memory-DoS).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Under the Supervisor, only the ingress gateway may reach the app — other
# add-ons share the internal docker network and could otherwise POST to
# :8099 with this add-on's token, bypassing HA auth. When running outside the
# Supervisor (local dev via DS_HA_* env), the guard is inert.
_INGRESS_GATEWAY = "172.30.32.2"
_UNDER_SUPERVISOR = bool(os.environ.get("SUPERVISOR_TOKEN"))

# Serialises the long-running robot map-edit batch (apply_segment_ops).
_segops_lock = threading.Lock()


@app.before_request
def _guard_ingress():
    if not _UNDER_SUPERVISOR:
        return None
    remote = request.remote_addr or ""
    if remote not in (_INGRESS_GATEWAY, "127.0.0.1", "::1"):
        log.warning("blocked non-ingress request from %s to %s", remote, request.path)
        return Response("Forbidden", status=403)
    return None


@app.context_processor
def _inject_asset_v():
    """Cache-bust JS/CSS by build mtime so a rebuild is fetched fresh."""
    try:
        v = max(
            int(os.path.getmtime(os.path.join(_STATIC_DIR, f)))
            for f in os.listdir(_STATIC_DIR)
            if f.endswith((".js", ".css"))
        )
    except (OSError, ValueError):
        v = 0
    return {"ASSET_V": v, "ADDON_V": _addon_version()}


def _addon_version() -> str:
    """The add-on's version from its config.yaml (sits one level above app/
    in both the mapped-share dev layout and the repo)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    if _UI_DIR:
        path = os.path.join(os.path.dirname(_UI_DIR), "config.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*version:\s*"?([^"\s]+)"?', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "dev"


def _ha_error(exc: Exception) -> Tuple[dict, int]:
    if isinstance(exc, ha_bridge.CoreUnavailable):
        return {"error": "ha_unavailable", "detail": str(exc)}, 503
    if isinstance(exc, ha_bridge.CoreError):
        return {"error": "ha_error", "detail": str(exc)}, 502
    return {"error": "unknown", "detail": repr(exc)}, 500


@app.route("/")
def index():
    return render_template("index.html")


# Uploaded reference floor plan (the user's real architect drawing) — kept in
# the add-on's persistent /data so it survives rebuilds; the scheduler config
# stores only its on-map transform (OPTS.user_plan).
_PLAN_DIR = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) \
    else os.path.dirname(os.path.abspath(__file__))
_PLAN_PATH = os.path.join(_PLAN_DIR, "user_plan.png")


@app.route("/api/user_plan", methods=["GET", "POST", "DELETE"])
def api_user_plan():
    if request.method == "GET":
        if not os.path.isfile(_PLAN_PATH):
            return jsonify({"error": "not_found"}), 404
        with open(_PLAN_PATH, "rb") as fh:
            return Response(fh.read(), mimetype="image/png")
    if request.method == "DELETE":
        try:
            os.remove(_PLAN_PATH)
        except OSError:
            pass
        return jsonify({"ok": True})
    body = request.get_json(silent=True) or {}
    m = re.match(r"data:image/(?:png|jpe?g);base64,(.+)$", str(body.get("data_url", "")), re.S)
    if not m:
        return jsonify({"error": "bad_request", "detail": "expected data_url with base64 png/jpeg"}), 400
    # cap on the ENCODED length so we never decode a huge payload into RAM
    if len(m.group(1)) > 11 * 1024 * 1024:   # ~8 MB decoded
        return jsonify({"error": "too_large", "detail": "max 8 MB"}), 413
    try:
        raw = base64.b64decode(m.group(1))
    except Exception:
        return jsonify({"error": "bad_request", "detail": "invalid base64"}), 400
    if len(raw) > 8 * 1024 * 1024:
        return jsonify({"error": "too_large", "detail": "max 8 MB"}), 413
    with open(_PLAN_PATH, "wb") as fh:
        fh.write(raw)
    return jsonify({"ok": True, "bytes": len(raw)})


@app.route("/api/ha/states")
def api_states():
    cfg = ha_bridge.config_from_env()
    try:
        return jsonify({"entities": ha_bridge.list_states(cfg)})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/notify_services")
def api_notify_services():
    cfg = ha_bridge.config_from_env()
    try:
        return jsonify({"services": ha_bridge.list_notify_services(cfg)})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/areas")
def api_areas():
    cfg = ha_bridge.config_from_env()
    try:
        return jsonify({"areas": ha_bridge.list_areas(cfg)})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/service", methods=["POST"])
def api_call_service():
    """Call an allow-listed HA service on an entity (floor-plan controls)."""
    cfg = ha_bridge.config_from_env()
    p = request.get_json(silent=True) or {}
    try:
        ha_bridge.call_service(cfg, p.get("domain", ""), p.get("service", ""), p.get("entity_id", ""))
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/test_notify", methods=["POST"])
def api_test_notify():
    """Send a test notification to the configured targets so the user can
    verify their notify channel renders correctly."""
    cfg = ha_bridge.config_from_env()
    p = request.get_json(silent=True) or {}
    targets = p.get("targets") or ["persistent_notification"]
    try:
        sent = ha_bridge.notify_test(cfg, targets)
        return jsonify({"ok": True, "sent": sent})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/state")
def api_state():
    """Current state of a single entity (for live overlay refresh)."""
    cfg = ha_bridge.config_from_env()
    eid = (request.args.get("entity_id") or "").strip()
    try:
        return jsonify(ha_bridge.get_state(cfg, eid))
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/config")
def api_get_config():
    cfg = ha_bridge.config_from_env()
    vacuum = (request.args.get("vacuum") or "").strip() or None
    try:
        return jsonify(ha_bridge.get_config(cfg, vacuum=vacuum))
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/report")
def api_get_report():
    cfg = ha_bridge.config_from_env()
    vacuum = (request.args.get("vacuum") or "").strip() or None
    try:
        return jsonify(ha_bridge.get_report(cfg, vacuum=vacuum))
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/config", methods=["POST"])
def api_set_config():
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    options = payload.get("options")
    if not isinstance(options, dict):
        return jsonify({"error": "bad_request", "detail": 'body must be {"options": {...}}'}), 400
    vacuum = (payload.get("vacuum") or "").strip() or None
    try:
        ha_bridge.set_config(cfg, options, vacuum=vacuum)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/map")
def api_map():
    cfg = ha_bridge.config_from_env()
    prefix = (request.args.get("prefix") or "").strip()
    if not prefix:
        return jsonify({"error": "bad_request", "detail": "prefix required"}), 400
    try:
        return jsonify(ha_bridge.get_map(cfg, prefix))
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/map_image")
def api_map_image():
    """Rendered map PNG, proxied so the browser can show it under the boxes."""
    cfg = ha_bridge.config_from_env()
    prefix = (request.args.get("prefix") or "").strip()
    if not prefix:
        return jsonify({"error": "bad_request", "detail": "prefix required"}), 400
    try:
        content, ctype = ha_bridge.get_map_image(cfg, prefix)
        return Response(content, mimetype=ctype, headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/zones", methods=["POST"])
def api_set_zones():
    """Write virtual walls + no-go + no-mop zones to the robot's cloud map.
    Backs up the map first (unless backup=false) so it can be restored."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    if not ve:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity required"}), 400
    try:
        backed_up = False
        if payload.get("backup", True):
            ha_bridge.backup_map(cfg, ve)
            backed_up = True
        ha_bridge.set_restricted_zone(cfg, ve, payload.get("walls"), payload.get("zones"), payload.get("no_mops"))
        return jsonify({"ok": True, "backed_up": backed_up})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/rename_segment", methods=["POST"])
def api_rename_segment():
    """Rename a room segment on the robot's own map (syncs to the Dreame app)."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    seg = payload.get("segment_id")
    name = (payload.get("name") or "").strip()
    if not ve or seg is None or not name:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, segment_id, name required"}), 400
    try:
        ha_bridge.rename_segment(cfg, ve, seg, name)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/split_segment", methods=["POST"])
def api_split_segment():
    """Split a room segment along a drawn line on the robot's map (Dreame app
    reflects it). Backs up the map first unless backup=false."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    seg = payload.get("segment_id")
    line = payload.get("line")
    if not ve or seg is None or not isinstance(line, list) or len(line) < 4:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, segment_id, line[4] required"}), 400
    try:
        backed_up = False
        if payload.get("backup", True):
            ha_bridge.backup_map(cfg, ve)
            backed_up = True
        ha_bridge.split_segment(cfg, ve, payload.get("map_id"), seg, line[:4])
        return jsonify({"ok": True, "backed_up": backed_up})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/merge_segments", methods=["POST"])
def api_merge_segments():
    """Merge two adjacent room segments on the robot's map (Dreame app reflects
    it). segments = [keep_id, absorb_id]. Backs up the map first."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    segs = payload.get("segments")
    if not ve or not isinstance(segs, list) or len(segs) < 2:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, segments[2] required"}), 400
    try:
        backed_up = False
        if payload.get("backup", True):
            ha_bridge.backup_map(cfg, ve)
            backed_up = True
        ha_bridge.merge_segments(cfg, ve, segs)
        return jsonify({"ok": True, "backed_up": backed_up})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/clean_segments", methods=["POST"])
def api_clean_segments():
    """Send the robot to clean the tapped room segment(s)."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    segs = payload.get("segments")
    if not ve or not isinstance(segs, list) or not segs:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, segments[] required"}), 400
    try:
        ha_bridge.clean_segments(cfg, ve, segs)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/clean_zone", methods=["POST"])
def api_clean_zone():
    """Edge clean: zone-clean a list of rectangles [x0,y0,x1,y1] in one task."""
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    ve = (payload.get("vacuum_entity") or "").strip()
    zones = payload.get("zones")
    if not ve or not isinstance(zones, list) or not zones:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, zones[] required"}), 400
    try:
        ha_bridge.clean_zones(cfg, ve, zones, repeats=int(payload.get("repeats", 1)))
        return jsonify({"ok": True, "count": len(zones)})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/apply_segment_ops", methods=["POST"])
def api_apply_segment_ops():
    """Apply a staged batch of segment edits (split/merge/rename) to the robot
    as one bulk change — single backup, sequential native writes with polling.
    Long request by design (each op is a cloud map write, ~5-15s)."""
    cfg = ha_bridge.config_from_env()
    p = request.get_json(silent=True) or {}
    ve = (p.get("vacuum_entity") or "").strip()
    prefix = (p.get("prefix") or "").strip()
    ops = p.get("ops")
    if not ve or not prefix or not isinstance(ops, list) or not ops:
        return jsonify({"error": "bad_request", "detail": "vacuum_entity, prefix, ops[] required"}), 400
    if len(ops) > 12:
        return jsonify({"error": "bad_request", "detail": "max 12 ops per batch"}), 400
    # One bulk map-edit at a time per robot — a second concurrent batch would
    # interleave native writes on the same map and corrupt it.
    if not _segops_lock.acquire(blocking=False):
        return jsonify({"error": "busy", "detail": "a map edit is already applying — wait for it to finish"}), 409
    try:
        res = ha_bridge.apply_segment_ops(cfg, ve, prefix, ops)
        return jsonify({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code
    finally:
        _segops_lock.release()


@app.route("/api/ha/manual_done", methods=["POST"])
def api_manual_done():
    """Tick a 'clean by hand' room off from the Insights view — credits the room
    and drops it from the list (same as ticking it in HA's to-do panel)."""
    cfg = ha_bridge.config_from_env()
    p = request.get_json(silent=True) or {}
    prefix = (p.get("prefix") or "").strip()
    seg = p.get("segment")
    if not prefix or seg is None:
        return jsonify({"error": "bad_request", "detail": "prefix, segment required"}), 400
    try:
        ha_bridge.manual_done(cfg, prefix, seg)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


@app.route("/api/ha/action", methods=["POST"])
def api_action():
    cfg = ha_bridge.config_from_env()
    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    try:
        ha_bridge.call_action(cfg, action, data)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        body, code = _ha_error(e)
        return jsonify(body), code


if __name__ == "__main__":
    # HA's ingress proxy hits us on 0.0.0.0:8099.
    app.run(host="0.0.0.0", port=8099, debug=False)
