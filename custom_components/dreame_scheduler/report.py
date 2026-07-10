"""Report builder + get_report service for the Dreame Scheduler GUI.

Produces a today/week overview the add-on can render: per-room status
(cleaned / skipped-with-reason / pending / not-scheduled), the mode & suction
each room used or will use, plus the robot's own coverage renders
(``cleaning_history_picture``) and structured ``obstacles`` from the map camera.

Everything here is DERIVED from data that already exists — the week_tracker
counters, the per-room options, and the Tasshack map-camera attributes — so it
needs no new persistent store. (A cross-week history log for fail-trend
analytics is a later milestone.)

Registered once per HA start from __init__.async_setup_entry.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from . import history_analytics as hist_a
from .const import (
    CONF_PREFIX,
    CONF_VACUUM_ENTITY,
    DEFAULT_DAILY_TIME,
    DOMAIN,
    MAX_SEGMENTS,
    OPT_CATCHUP_DAY,
    OPT_CATCHUP_ENABLED,
    OPT_CATCHUP_TIME,
    OPT_DAILY_TIME,
    OPT_DEFAULT_MODE,
    OPT_DEFAULT_SUCTION,
    OPT_REQUIRE_AWAY,
    OPT_ROOMS,
    OPT_WINDOW_ENABLED,
    OPT_WINDOW_END,
    OPT_WINDOW_START,
    ROOM_DAYS,
    ROOM_ENABLED,
    ROOM_MODE,
    ROOM_SUCTION,
    SUF_CLEAN_WATER,
    SUF_CURRENT_ROOM,
    SUF_DIRTY_WATER,
    SUF_DUST_BAG,
    SUF_ERROR,
    SUF_STATUS,
    SUF_TASK_STATUS,
    WEEKDAYS,
    e,
    room_entity,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = ("unknown", "unavailable", "none", "")


def _sval(hass: HomeAssistant, entity_id: str) -> str | None:
    st = hass.states.get(entity_id)
    if st is None or str(st.state).lower() in _UNAVAILABLE:
        return None
    return st.state


def _room_names(hass: HomeAssistant, prefix: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for n in range(1, MAX_SEGMENTS + 1):
        name = _sval(hass, room_entity("select", prefix, n, "name"))
        if name:
            names[str(n)] = name
    return names


def _plain(v, _depth: int = 0):
    """Recursively coerce a value to plain JSON types. The dreame map camera
    stores some attribute values (obstacles) as objects that aren't dict/Mapping
    in-process — they expose their fields via as_dict() (that's how they reach
    REST/the frontend). Without coercion, isinstance-based parsing drops them."""
    if _depth > 6:
        return None
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Mapping):
        return {str(k): _plain(val, _depth + 1) for k, val in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x, _depth + 1) for x in v]
    as_dict = getattr(v, "as_dict", None)
    if callable(as_dict):
        try:
            return _plain(as_dict(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass
    inner = getattr(v, "__dict__", None)
    if isinstance(inner, dict):
        return {str(k): _plain(val, _depth + 1) for k, val in inner.items()
                if not str(k).startswith("_")}
    return str(v)


def _map_attrs(hass: HomeAssistant, prefix: str) -> dict:
    st = hass.states.get(e("camera", prefix, "map"))
    if not st:
        return {}
    plain = _plain(dict(st.attributes))
    return plain if isinstance(plain, dict) else {}


def _coverage_runs(map_attrs: dict) -> list[dict]:
    """Turn the cleaning_history_picture dict into an ordered list of per-run
    coverage renders: [{label, url, status}]. Keys look like
    '1: 07/07 21:54 - Room Cleaning (Completed)'."""
    chp = map_attrs.get("cleaning_history_picture")
    if not isinstance(chp, Mapping):
        return []
    runs = []
    for label, url in chp.items():
        status = "unknown"
        low = str(label).lower()
        if "(completed)" in low:
            status = "completed"
        elif "(interrupted)" in low:
            status = "interrupted"
        # strip the leading 'N: ' index for a cleaner display label
        disp = str(label)
        if ": " in disp:
            disp = disp.split(": ", 1)[1]
        runs.append({"label": disp, "url": str(url), "status": status})
    return runs


def _obstacles(map_attrs: dict) -> list[dict]:
    obs = map_attrs.get("obstacles")
    if not isinstance(obs, Mapping):
        return []
    # The robot photographs obstacles it detects; obstacle_picture maps
    # "<idx>: <label>" -> image URL. Match by the leading index so each row can
    # show the actual picture instead of meaningless coordinates.
    pics = map_attrs.get("obstacle_picture")
    pic_by_idx: dict[str, str] = {}
    if isinstance(pics, Mapping):
        for label, url in pics.items():
            idx = str(label).split(":", 1)[0].strip()
            if idx and url:
                pic_by_idx[idx] = str(url)
    out = []
    for key, v in obs.items():
        if not isinstance(v, Mapping):
            continue
        pic = pic_by_idx.get(str(key))
        if str(v.get("picture_status", "")).lower() != "uploaded":
            pic = None  # only offer a photo the robot actually uploaded
        out.append({
            "room": v.get("room"),
            "type": v.get("type"),
            "reason": v.get("reason"),
            "x": v.get("x"),
            "y": v.get("y"),
            "possibility": v.get("possibility"),
            "picture_url": pic,
        })
    return out


def _robot(hass: HomeAssistant, engine, prefix: str) -> dict:
    """Live robot snapshot for the Robot-status card — battery, what it's doing,
    error, current room, and the consumable/tank statuses. Sourced from the
    vacuum entity + the Tasshack helper sensors (reusing the engine's tested
    battery reader), so it's portable to any user's vacuum."""
    vac = getattr(engine, "_vacuum_entity", None)
    st = hass.states.get(vac) if vac else None
    return {
        "vacuum_entity": vac,
        "state": st.state if st else None,
        "status": _sval(hass, e("sensor", prefix, SUF_STATUS)),
        "task_status": _sval(hass, e("sensor", prefix, SUF_TASK_STATUS)),
        "battery": engine._battery(),
        "error": _sval(hass, e("sensor", prefix, SUF_ERROR)),
        "current_room": _sval(hass, e("sensor", prefix, SUF_CURRENT_ROOM)),
        "dust_bag": _sval(hass, e("sensor", prefix, SUF_DUST_BAG)),
        "clean_water": _sval(hass, e("sensor", prefix, SUF_CLEAN_WATER)),
        "dirty_water": _sval(hass, e("sensor", prefix, SUF_DIRTY_WATER)),
        "cleaned_area": _sval(hass, e("sensor", prefix, "cleaned_area")),
    }


def _weekday_name(d) -> str | None:
    """WEEKDAYS[d] tolerant of int or numeric-string storage (native flow stores
    the day as a string, the add-on as an int)."""
    try:
        i = int(d)
    except (TypeError, ValueError):
        return None
    return WEEKDAYS[i] if 0 <= i < 7 else None


def _schedule(hass: HomeAssistant, engine, opts: dict, rooms_cfg: dict, now: datetime) -> dict:
    """Presence + next-run summary for the Presence card."""
    presence_home = engine._presence_home()
    configured = bool(engine._presence_entities())
    daily_time = opts.get(OPT_DAILY_TIME) or DEFAULT_DAILY_TIME
    nxt = engine.next_run(now)   # shared with status_snapshot so GUI + card agree
    return {
        "presence_home": presence_home,          # True / False / None (unknown)
        "presence_configured": configured,
        "require_away": bool(opts.get(OPT_REQUIRE_AWAY)),
        "daily_time": daily_time,
        "next_run": nxt.isoformat() if nxt else None,
        "next_run_day": WEEKDAYS[nxt.weekday()] if nxt else None,
        "next_run_time": nxt.strftime("%H:%M") if nxt else None,
        "window": {
            "enabled": bool(opts.get(OPT_WINDOW_ENABLED)),
            "start": opts.get(OPT_WINDOW_START),
            "end": opts.get(OPT_WINDOW_END),
        },
        "catchup": {
            "enabled": bool(opts.get(OPT_CATCHUP_ENABLED)),
            # the native options flow stores the day as a string ("5"), the
            # add-on as an int — accept both so the day always renders
            "day": _weekday_name(opts.get(OPT_CATCHUP_DAY)),
            "time": opts.get(OPT_CATCHUP_TIME),
        },
    }


def build_report(hass: HomeAssistant, entry: ConfigEntry, engine) -> dict:
    tracker = engine.tracker
    prefix = entry.data[CONF_PREFIX]
    opts = dict(entry.options)
    rooms_cfg: dict = opts.get(OPT_ROOMS, {}) or {}
    names = _room_names(hass, prefix)
    map_attrs = _map_attrs(hass, prefix)

    now: datetime = dt_util.now()
    weekday = now.weekday()

    cleaned: dict = dict(tracker.cleaned)
    unreachable: dict = dict(tracker.unreachable)
    default_mode = opts.get(OPT_DEFAULT_MODE, "") or "(default)"
    default_suction = opts.get(OPT_DEFAULT_SUCTION, "") or "(default)"

    # Per-room blocked reason from the map's structured obstacles (matched by name).
    obstacles = _obstacles(map_attrs)
    blocked_by_name: dict[str, str] = {}
    for o in obstacles:
        if o.get("type") == "Blocked Room" and o.get("room"):
            blocked_by_name[str(o["room"])] = o.get("reason") or "blocked"

    history = list(getattr(tracker, "history", []) or [])

    rooms_out: list[dict] = []
    totals = {"cleaned": 0, "pending": 0, "skipped": 0, "not_scheduled": 0}
    for n in range(1, MAX_SEGMENTS + 1):
        seg = str(n)
        name = names.get(seg)
        if not name:
            continue
        cfg = rooms_cfg.get(seg, {}) or {}
        enabled = bool(cfg.get(ROOM_ENABLED, False))
        days = list(cfg.get(ROOM_DAYS, []) or [])
        mode = cfg.get(ROOM_MODE) or default_mode
        suction = cfg.get(ROOM_SUCTION) or default_suction
        scheduled_week = enabled and bool(days)
        scheduled_today = enabled and (weekday in days)

        if seg in cleaned:
            status = "cleaned"
            totals["cleaned"] += 1
        elif seg in unreachable:
            status = "skipped"
            totals["skipped"] += 1
        elif scheduled_week:
            status = "pending"
            totals["pending"] += 1
        else:
            status = "not_scheduled"
            totals["not_scheduled"] += 1

        streak = hist_a.consecutive_fails(history, seg)
        sug = hist_a.suggest_better_day(history, seg, days)
        rooms_out.append({
            "seg": seg,
            "name": name,
            "enabled": enabled,
            "days": days,
            "day_names": [WEEKDAYS[d] for d in days if 0 <= d < 7],
            "scheduled_today": scheduled_today,
            "mode": mode,
            "suction": suction,
            "status": status,
            "cleaned_at": cleaned.get(seg),
            "fail_count": int(unreachable.get(seg, 0)),
            "fail_streak": streak,
            "suggested_day": WEEKDAYS[sug] if sug is not None else None,
            "blocked_reason": blocked_by_name.get(name),
        })

    # Actionable suggestions derived from the history log.
    suggestions: list[dict] = []
    for r in rooms_out:
        if r.get("suggested_day") and r["status"] in ("skipped", "pending"):
            cur = " ".join(r["day_names"]) or "its scheduled days"
            suggestions.append({
                "seg": r["seg"], "name": r["name"], "type": "move_day",
                "message": f"{r['name']} keeps getting missed on {cur} — it cleans reliably on "
                           f"{r['suggested_day']}. Consider scheduling it then.",
            })
        elif r.get("fail_streak", 0) >= 3:
            reason = hist_a.last_fail_reason(history, r["seg"])
            suggestions.append({
                "seg": r["seg"], "name": r["name"], "type": "recurring_fail",
                "message": f"{r['name']} has been missed {r['fail_streak']}× in a row"
                           + (f" ({reason})" if reason else "")
                           + " — add a no-go/door sensor or check access.",
            })

    return {
        "found": True,
        "entry_id": entry.entry_id,
        "prefix": prefix,
        "generated": now.isoformat(),
        "weekday": WEEKDAYS[weekday],
        "week_start": tracker.week_start,
        "rooms": rooms_out,
        "totals": totals,
        "suggestions": suggestions,
        "runs_logged": len(history),
        # Raw run log (capped in the tracker) — powers the GUI's run-history
        # timeline + weekday trend. _plain() keeps it JSON-safe.
        "history": [_plain(h) for h in history[-100:]],
        "last_run": tracker.last_run,
        "coverage": {
            "live_map_url": map_attrs.get("entity_picture"),
            "runs": _coverage_runs(map_attrs),
        },
        "obstacles": obstacles,
        "cleaned_area_today": _sval(hass, e("sensor", prefix, "cleaned_area")),
        "robot": _robot(hass, engine, prefix),
        "schedule": _schedule(hass, engine, opts, rooms_cfg, now),
        "weekdays": WEEKDAYS,
    }


def async_register_report_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "get_report"):
        return

    def _find_entry(vacuum: str | None) -> ConfigEntry | None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if vacuum:
            for ent in entries:
                if ent.data.get(CONF_VACUUM_ENTITY) == vacuum:
                    return ent
            return None
        return entries[0] if entries else None

    async def _async_get_report(call: ServiceCall) -> dict:
        entry = _find_entry(call.data.get("vacuum"))
        if entry is None:
            return {"found": False, "rooms": []}
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is None:
            return {"found": False, "rooms": []}
        return build_report(hass, entry, coordinator.engine)

    hass.services.async_register(
        DOMAIN, "get_report", _async_get_report,
        schema=vol.Schema({vol.Optional("vacuum"): str}),
        supports_response=SupportsResponse.ONLY,
    )
