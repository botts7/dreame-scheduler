"""Credit for cleans started outside the scheduler: pure, no Home Assistant imports.

A clean started from the Dreame app, the robot's own schedule or a voice
assistant used to be invisible to the scheduler: it sent the robot again that
week, and Clean by hand could nag about rooms that had just been done (#3, #4).

The engine opens a session when the robot is genuinely out cleaning while the
scheduler has no run of its own, notes the rooms it was asked for and the rooms
it passes through, and closes it once the robot is parked again. Credit is
deliberately conservative:

* only when the robot's own history record for that session says completed
  (an interrupted or stopped clean earns nothing),
* never for a room the record lists as blocked,
* a room clean credits the rooms it was asked to clean AND was seen in (the
  robot can be asked for a room it never reaches: live 2026-11-19 a 5-room run
  never entered Lounge Room2); a whole-house clean credits the rooms it visited,
* a vacuum-then-mop job is one task on the robot, so it counts once.
"""

from __future__ import annotations

from datetime import datetime

# A session whose robot is parked with no new history record after this long
# is dropped (a cancelled start, or a record that never synced).
GIVE_UP_SECONDS = 1800
# A completed=False record is a mid-task dock (mop wash, recharge) unless the
# robot stays parked this long without heading back out.
INCOMPLETE_DWELL_SECONDS = 1800
# Allowed skew between HA's clock and the record's timestamp (session start).
RECORD_TS_TOLERANCE = 120


def open_session(now_iso: str, count: int | None, record_ts: int | None,
                 segments, how: str | None) -> dict:
    """A new session. ``segments`` is what the robot says it was asked to clean
    (empty for a whole-house clean); ``how`` is its startup_method, if known."""
    return {
        "started": now_iso,
        "count_at_start": count,
        "record_ts_at_start": record_ts,
        "segments": sorted({str(s) for s in (segments or [])}),
        "visited": [],
        "how": how,
        "parked_since": None,
    }


def note_progress(session: dict, segments, current_segment) -> dict:
    """Fold in what the robot reports this tick: its task's segment list (it can
    arrive a beat after the start) and the room it's in now."""
    segs = set(session.get("segments") or [])
    segs.update(str(s) for s in (segments or []))
    session["segments"] = sorted(segs)
    if current_segment not in (None, "", 0, "0"):
        cur = str(current_segment)
        if cur not in session["visited"]:
            session["visited"].append(cur)
    return session


def record_for_session(session: dict, record: dict | None) -> dict | None:
    """The newest history record if it belongs to this session: strictly newer
    than the one that existed when it opened, and not older than its start."""
    if not isinstance(record, dict):
        return None
    try:
        ts = int(record.get("timestamp"))
    except (TypeError, ValueError):
        return None
    base = session.get("record_ts_at_start")
    if base is not None and ts <= int(base):
        return None
    try:
        started = datetime.fromisoformat(str(session.get("started"))).timestamp()
    except (TypeError, ValueError):
        return record
    return record if ts >= started - RECORD_TS_TOLERANCE else None


def _interrupted(record: dict) -> bool:
    """A record carrying an interrupt reason other than a normal finish."""
    reason = str(record.get("interrupt_reason") or "").strip().lower()
    # "Task completed" is a normal finish; anything else (including
    # "incomplete") means it stopped early.
    return bool(reason) and reason not in ("task completed", "completed", "complete")


def rooms_to_credit(session: dict, record: dict | None) -> list[str]:
    """Segment ids to credit, or [] when the evidence doesn't support credit."""
    if not isinstance(record, dict) or record.get("completed") is not True or _interrupted(record):
        return []
    blocked = set()
    for key in ("blocked_rooms", "skipped_rooms"):
        value = record.get(key) or {}
        blocked.update(str(k) for k in (value if isinstance(value, (dict, list, tuple, set)) else []))
    asked = [str(s) for s in (session.get("segments") or [])]
    visited = [str(s) for s in (session.get("visited") or [])]
    base = [s for s in asked if s in visited] if asked else visited
    return [s for s in base if s not in blocked]


def history_entry(now_iso: str, weekday: int, rooms: list[str], how: str | None) -> dict:
    """The run-history row for a credited outside clean, so the weekly report,
    the trend stats and Clean by hand all see it. kind "external" is left out of
    the best-day stats (it wasn't a scheduled attempt)."""
    return {
        "ts": now_iso,
        "weekday": weekday,
        "kind": "external",
        "how": how,
        "per_room": {str(s): {"status": "cleaned", "mode": "native", "will_mop": None} for s in rooms},
    }


def close_action(parked_seconds: float, record: dict | None) -> str:
    """What to do with a session while the robot is parked.

    "credit"  the record says completed: credit and close
    "drop"    no usable evidence after the wait, or an incomplete record that
              never resumed: close without credit
    "wait"    give the counter/record time to land, or the robot time to head
              back out after a mid-task dock
    """
    if record is not None and record.get("completed") is True:
        return "credit"
    if record is not None and record.get("completed") is False:
        return "drop" if parked_seconds >= INCOMPLETE_DWELL_SECONDS else "wait"
    return "drop" if parked_seconds >= GIVE_UP_SECONDS else "wait"
