"""Translate Dreame's completed passes into the scheduler's chronological run log."""

from collections.abc import Mapping
from datetime import date, datetime
import math
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
    return {str(int(v)) for v in values if str(v).isascii() and str(v).isdigit() and int(v) > 0}


def completed_passes(attributes, since, now):
    """Ignore sensor metadata, interrupted attempts, and records predating installation."""
    for raw in attributes.values():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("status"), str) or raw["status"] not in SCOPES:
            continue
        if raw.get("completed") is not True or raw.get("interrupt_reason"):
            continue
        stamp = raw.get("timestamp")
        duration = re.fullmatch(r"(\d+) min", str(raw.get("cleaning_time", "")))
        area = re.fullmatch(r"(\d+(?:\.\d+)?) m²", str(raw.get("cleaned_area", "")))
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or not (since <= stamp <= now):
            continue
        if not duration or not area or float(area[1]) <= 0:
            continue
        end = int(stamp) + int(duration[1]) * 60
        if end > now:
            continue
        # Keep each pass at its own time. Combining sequential passes under the
        # final pass time would move earlier room credits past intervening misses.
        yield f"dreame_pass_{int(stamp)}", int(stamp), end, raw


def credit(raw, configured, selected, historical_map):
    """Only whole-home and room passes with cleaned-room evidence earn credit."""
    if raw["status"] not in {"Cleaning", "Room cleaning"}:
        return []
    if historical_map is None:
        return []
    # A requested room or a large total area is not proof that a room was cleaned.
    selected_rooms = segments(historical_map.get("cleaned"))
    targeted = segments(historical_map.get("targeted"))
    if targeted:
        selected_rooms &= targeted
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


def apply_pass(state, source_id, item, room_names, timezone):
    """Import one evidenced pass once, preserving its original completion time."""
    history = state["history"]
    if any(row.get("source_id") == source_id for row in history):
        return False
    finish = item["end"]
    local_finish = datetime.fromtimestamp(finish, timezone)
    ts = local_finish.isoformat()
    swept = item["pad"] == "Not installed"
    per_room = {seg: {"status": "cleaned", "mode": "sweeping" if swept else None,
                      "will_mop": False if swept else None} for seg in item["rooms"]}
    if not per_room:
        return False
    row = {"source": SOURCE, "source_id": source_id, "kind": "manual", "map": "default",
           "ts": ts, "weekday": local_finish.weekday(), "per_room": per_room,
           "timestamp_basis": "pass_start_plus_reported_duration"}
    rows = sorted(history + [row], key=lambda r: epoch(r["ts"]))[-200:]
    if row not in rows:
        return False
    week = date.fromisoformat(state["week_start"])
    slot = state["maps"]["default"]
    cleaned = slot.setdefault("cleaned", {})
    confirmed = slot.setdefault("confirmed", dict(cleaned))
    state["history"] = rows
    if finish > epoch(state.get("last_clean")):
        state["last_clean"] = ts
    for seg in per_room:
        if epoch(confirmed.get(seg)) > finish:
            continue
        confirmed[seg] = ts
        if 0 <= (local_finish.date() - week).days < 7:
            cleaned[seg] = ts
        newer_miss = any(r.get("map", "default") == "default" and epoch(r["ts"]) > finish and
                         (r.get("per_room", {}).get(seg) or {}).get("status") in ("skipped", "failed")
                         for r in rows)
        if not newer_miss:
            slot.setdefault("unreachable", {}).pop(seg, None)
            slot.setdefault("manual_clean", {}).pop(seg, None)
    if finish >= epoch((state.get("last_run") or {}).get("finished")):
        state["last_run"] = {"kind": "manual", "source": SOURCE, "source_id": source_id,
                             "finished": ts, "cleaned": [room_names.get(s, f"Room {s}") for s in per_room],
                             "skipped": [], "cleaned_area": item["area"]}
    return True
