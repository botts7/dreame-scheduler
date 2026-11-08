"""Translate Dreame's completed passes into the scheduler's chronological run log."""

from collections.abc import Mapping
from datetime import date, datetime
import re

SOURCE = "dreame_scheduler_external"
SCOPES = {"Cleaning", "Room cleaning", "Zone cleaning", "Spot cleaning", "Partial cleaning"}


def epoch(value):
    if not value:
        return 0
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def segments(values):
    if not isinstance(values, (list, tuple, set, dict)):
        return set()
    return {str(v) for v in values if str(v).isdigit() and int(v) > 0}


def completed_passes(attributes, since, now):
    """Ignore sensor metadata, interrupted attempts, and records predating installation."""
    for raw in attributes.values():
        if not isinstance(raw, Mapping) or raw.get("status") not in SCOPES:
            continue
        if raw.get("completed") is not True or raw.get("interrupt_reason"):
            continue
        stamp = raw.get("timestamp")
        duration = re.fullmatch(r"(\d+) min", str(raw.get("cleaning_time", "")))
        area = re.fullmatch(r"(\d+(?:\.\d+)?) m²", str(raw.get("cleaned_area", "")))
        if not isinstance(stamp, (int, float)) or not (since <= stamp <= now):
            continue
        if not duration or not area or float(area[1]) <= 0:
            continue
        end = int(stamp) + int(duration[1]) * 60
        if end > now:
            continue
        group = str(raw.get("multiple_cleaning_time") or "")
        key = f"dreame_session_{group}" if group.isdigit() and int(group) else f"dreame_pass_{int(stamp)}"
        yield key, int(stamp), end, raw


def credit(raw, configured, selected, historical_map, min_area_per_room):
    """Zone work is history-only. Completed targeted passes require room evidence."""
    if raw["status"] not in {"Cleaning", "Room cleaning"}:
        return []
    if historical_map is not None:
        cleaned = segments(historical_map.get("cleaned"))
        targeted = segments(historical_map.get("targeted"))
        if cleaned and targeted:
            selected_rooms = cleaned & targeted
            if not selected_rooms:
                return []
        else:
            selected_rooms = cleaned or targeted
    else:
        selected_rooms = set()
    if not selected_rooms and raw["status"] == "Cleaning" and raw.get("mop_pad") == "Not installed":
        if float(raw["cleaned_area"].split()[0]) < min_area_per_room * len(selected):
            return []
        selected_rooms = selected
    blocked = set()
    for key in ("blocked_rooms", "skipped_rooms"):
        value = raw.get(key)
        if value:
            parsed = segments(value)
            if not parsed or len(parsed) != len(value):
                return []
            blocked.update(parsed)
    return sorted((selected_rooms & configured & selected) - blocked, key=int)


def owned_by_scheduler(start, windows):
    """Match the robot's pass start against recorded scheduler dispatch intervals."""
    return any(int(a) <= start <= b for a, b in windows)


def apply_session(state, source_id, passes, room_names, timezone):
    """Merge once per session, including passes which arrive on separate refreshes."""
    history = state["history"]
    prev = next((r for r in history if r.get("source_id") == source_id), None)
    if prev and prev.get("source") != SOURCE:
        return False
    by_start = {p["start"]: p for p in (prev or {}).get("passes", [])}
    for p in passes:
        prior = by_start.get(p["start"])
        if prior and prior["rooms"] and not p["rooms"] and not p["map_verified"]:
            continue  # A transiently unavailable map does not erase prior evidence.
        by_start[p["start"]] = p
    merged = sorted(by_start.values(), key=lambda p: p["start"])
    finish = max(p["end"] for p in merged)
    local_finish = datetime.fromtimestamp(finish, timezone)
    room_passes = {}
    for p in merged:
        for seg in p["rooms"]:
            room_passes.setdefault(seg, []).append(p)
    per_room = {}
    for seg, items in room_passes.items():
        swept = any(p["pad"] == "Not installed" for p in items)
        per_room[seg] = {"status": "cleaned", "mode": "sweeping" if swept else None,
                         "will_mop": False if all(p["pad"] == "Not installed" for p in items) else None}
    row = {"source": SOURCE, "source_id": source_id, "kind": "manual", "map": "default",
           "ts": local_finish.isoformat(), "weekday": local_finish.weekday(), "passes": merged,
           "timestamp_basis": "last_pass_start_plus_reported_duration", "per_room": per_room}
    if row == prev:
        return False
    rows = sorted([r for r in history if r is not prev] + [row], key=lambda r: epoch(r["ts"]))[-200:]
    if row not in rows:
        return False
    state["history"] = rows
    week = date.fromisoformat(state["week_start"])
    slot = state["maps"]["default"]
    cleaned = slot.setdefault("cleaned", {})
    for seg, items in room_passes.items():
        room_end = max(p["end"] for p in items)
        when = datetime.fromtimestamp(room_end, timezone)
        if room_end > epoch(state.get("last_clean")):
            state["last_clean"] = when.isoformat()
        if not 0 <= (when.date() - week).days < 7:
            continue
        if epoch(cleaned.get(seg)) > room_end:
            continue
        cleaned[seg] = when.isoformat()
        newer_miss = any(epoch(r["ts"]) > room_end and
                         (r.get("per_room", {}).get(seg) or {}).get("status") in ("skipped", "failed")
                         for r in rows)
        if not newer_miss:
            slot.setdefault("unreachable", {}).pop(seg, None)
            slot.setdefault("manual_clean", {}).pop(seg, None)
    if finish >= epoch((state.get("last_run") or {}).get("finished")):
        state["last_run"] = {"kind": "manual", "source": SOURCE, "source_id": source_id,
                             "finished": row["ts"], "cleaned": [room_names.get(s, f"Room {s}") for s in per_room],
                             "skipped": [], "cleaned_area": merged[-1]["area"]}
    return True
