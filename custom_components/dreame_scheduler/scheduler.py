"""Pure scheduling logic for the Dreame Scheduler — no Home Assistant imports.

This module answers "which rooms should be cleaned, and when" from plain data:
the per-room config, the current weekday, and the set of rooms already
confirmed cleaned this week. The engine (engine.py) does the HA I/O — reading
states, dispatching services, notifying — and leans on these functions for
every decision so the decisions stay unit-testable.

Room config shape (per segment-id string), as stored in entry.options[rooms]:
    {
      "enabled":  bool,          # include in the schedule at all
      "days":     [0..6],        # weekdays this room is scheduled (0=Mon)
      "mode":     str,           # per-room cleaning-mode option (or "")
      "suction":  str,           # per-room suction option (or "")
      "wetness":  int | None,    # per-room mop wetness (or None)
      "repeats":  int,           # cleaning passes
      "door_sensor": str,        # optional contact-sensor entity_id (or "")
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


def _enabled_rooms(rooms: dict) -> dict:
    """Just the rooms flagged enabled, keyed by segment-id string."""
    return {
        seg: cfg
        for seg, cfg in (rooms or {}).items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    }


def rooms_due_today(rooms: dict, weekday: int) -> list[str]:
    """Enabled segment-ids scheduled for ``weekday`` (0=Mon..6=Sun), in the
    natural segment order. Empty ``days`` means 'never on the daily schedule'
    (that room relies on the weekly catch-up only)."""
    due = [
        seg
        for seg, cfg in _enabled_rooms(rooms).items()
        if weekday in (cfg.get("days") or [])
    ]
    return sorted(due, key=_seg_sort_key)


def pending_rooms(rooms: dict, cleaned: dict | set | list) -> list[str]:
    """Enabled segment-ids NOT yet confirmed cleaned this week — the set the
    weekly whole-house catch-up must finish. ``cleaned`` may be a set/list of
    segment-ids or a dict keyed by them."""
    done = set(cleaned.keys()) if isinstance(cleaned, dict) else set(cleaned)
    pend = [seg for seg in _enabled_rooms(rooms) if seg not in done]
    return sorted(pend, key=_seg_sort_key)


def all_enabled_segments(rooms: dict) -> list[str]:
    """Every enabled segment-id, sorted — the target of the weekly guarantee."""
    return sorted(_enabled_rooms(rooms).keys(), key=_seg_sort_key)


def week_start_for(day: date, week_start_day: int) -> date:
    """The date of the most recent ``week_start_day`` (0=Mon..6=Sun) on or
    before ``day`` — i.e. the Monday (or configured day) that anchors the
    tracking week ``day`` falls in."""
    delta = (day.weekday() - week_start_day) % 7
    return day - timedelta(days=delta)


def needs_week_rollover(today: date, stored_week_start: str | None, week_start_day: int) -> bool:
    """True when ``today`` belongs to a newer tracking week than the one we last
    recorded — the engine should finalise the previous week and reset counters.
    A missing/invalid stored value always rolls (first run)."""
    current = week_start_for(today, week_start_day)
    if not stored_week_start:
        return True
    try:
        stored = date.fromisoformat(stored_week_start)
    except (TypeError, ValueError):
        return True
    return current > stored


@dataclass
class Decision:
    """What the engine should do on this tick."""

    action: str  # "idle" | "dispatch"
    kind: str = ""  # "daily" | "catchup" | "manual"
    segments: list[str] = field(default_factory=list)
    reason: str = ""


def choose_dispatch(
    *,
    now_date: date,
    weekday: int,
    now_min: int,
    rooms: dict,
    cleaned: dict | set | list,
    daily_time_min: int | None,
    day_dispatched_on: str | None,
    catchup_enabled: bool,
    catchup_day: int,
    catchup_time_min: int | None,
    catchup_dispatched_on: str | None,
) -> Decision:
    """Decide whether a daily or catch-up dispatch is due right now.

    This is purely about *timing + selection* — it does NOT apply presence,
    window, guard or reachability checks (the engine layers those on the
    returned segment set, since they depend on live entity states). Returns a
    Decision with action 'dispatch' and the candidate segments, or 'idle'.

    Daily wins over catch-up when both are due on the same tick (the daily set
    is usually a subset; catch-up then mops up whatever's left later).
    ``*_dispatched_on`` are ISO date strings of the last dispatch of that kind,
    so each fires at most once per day.
    """
    today_iso = now_date.isoformat()

    # Daily: today's scheduled rooms, once per day, at/after the daily time.
    if (
        daily_time_min is not None
        and now_min >= daily_time_min
        and day_dispatched_on != today_iso
    ):
        due = rooms_due_today(rooms, weekday)
        # Drop rooms already confirmed cleaned this week (a catch-up or a manual
        # run may have covered them already) — no point redoing them today.
        done = set(cleaned.keys()) if isinstance(cleaned, dict) else set(cleaned)
        due = [s for s in due if s not in done]
        if due:
            return Decision(action="dispatch", kind="daily", segments=due,
                            reason=f"daily schedule for {today_iso}")
        # Nothing due today (or all already done) — still mark the day handled
        # so we don't re-check every tick; the engine records the empty dispatch.
        return Decision(action="idle", kind="daily", reason="nothing_due_today")

    # Weekly catch-up: on the catch-up weekday, once, at/after the catch-up time.
    if (
        catchup_enabled
        and weekday == catchup_day
        and catchup_time_min is not None
        and now_min >= catchup_time_min
        and catchup_dispatched_on != today_iso
    ):
        pend = pending_rooms(rooms, cleaned)
        if pend:
            return Decision(action="dispatch", kind="catchup", segments=pend,
                            reason="weekly whole-house catch-up")
        return Decision(action="idle", kind="catchup", reason="week_already_complete")

    return Decision(action="idle", reason="not_time")


def _seg_sort_key(seg: str):
    """Sort segment-id strings numerically when possible ('2' < '10')."""
    try:
        return (0, int(seg))
    except (TypeError, ValueError):
        return (1, str(seg))
