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
from datetime import date, datetime, timedelta


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


SWEEP_ONLY_MODE = "sweeping"


def is_mopping_mode(mode) -> bool:
    """True if a cleaning-mode option involves mopping (mopping / sweeping and
    mopping / mopping after sweeping / ...). Sweep-only modes return False."""
    return "mop" in str(mode or "").lower()


def advance_mop_cadence(
    base_mode, mop_every, prev_count, prev_date, today_iso: str
) -> tuple[int, str, bool]:
    """Resolve a room's cleaning mode for THIS run under the "mop every N
    sweep-days" cadence, and advance its per-room sweep-day counter.

    The user's model (from the community request): a room sweeps on each of its
    scheduled days, and mops only every Nth of those days — e.g. N=2 → sweep
    daily, mop-after-sweep every second day. N<=1 means "mop on every clean"
    (feature off), so the base mode is used unchanged.

    Counting is per CALENDAR DAY, not per dispatch: a run resumed after an
    interruption, or a manual clean-now, lands on the same date and must not
    advance the cadence twice. ``prev_count``/``prev_date`` are the room's stored
    counter and the date it last advanced; ``today_iso`` is today's local date.

    Returns ``(new_count, mode_for_this_run, will_mop)``:
      * new_count — the room's counter after this run (unchanged if already
        counted today).
      * mode_for_this_run — the base mode on a mop day, else ``"sweeping"``.
      * will_mop — whether this run mops.

    A non-mopping base mode (or N<=1) is returned verbatim with the counter
    still ticked once per day, so toggling N later stays phase-stable.
    """
    base = str(base_mode or "")
    try:
        n = int(mop_every)
    except (TypeError, ValueError):
        n = 1
    # Advance once per calendar day; a same-day re-dispatch keeps the count.
    count = int(prev_count or 0)
    if prev_date != today_iso:
        count += 1
    if not is_mopping_mode(base) or n <= 1:
        return count, base, is_mopping_mode(base)
    will_mop = (count % n == 0)
    return count, (base if will_mop else SWEEP_ONLY_MODE), will_mop


def door_open_long_enough(
    state, last_changed, now, mins, *, open_states=("on", "open")
) -> bool:
    """True if a door/contact sensor reads OPEN and has held that state for at
    least ``mins`` minutes.

    This is the safety signal behind the opt-in "retry a door-skipped room once
    its door reopens" feature: a door that has been open a good while means the
    room is very likely empty (nobody showering / using it), so it's safe to
    send the robot in — even while someone's home, if the user opted into that.

    ``last_changed``/``now`` are tz-aware datetimes. A closed/unknown state, a
    missing timestamp, or unparseable arithmetic all return False — we never
    retry on a signal we can't trust.
    """
    if str(state or "").lower() not in open_states:
        return False
    if last_changed is None or now is None:
        return False
    try:
        elapsed = (now - last_changed).total_seconds()
    except (TypeError, ValueError):
        return False
    return elapsed >= float(mins) * 60


def _date_of(ts) -> date | None:
    """Local date an ISO timestamp falls on, or None if unparseable. Stored
    clean timestamps carry the local offset, so ``.date()`` is already the
    local calendar day."""
    try:
        return datetime.fromisoformat(ts).date()
    except (TypeError, ValueError):
        return None


def cleaned_today(cleaned: dict | set | list, today: date) -> set:
    """Segment-ids confirmed cleaned on ``today``.

    The DAILY schedule de-dups on this, NOT on 'cleaned at all this week'. A room
    the user ticked for several weekdays (Mon+Thu, or every day) must clean on
    each of those days; keying the skip on the whole week silently collapses it
    to a single weekly clean (every day after the first never fires). This only
    ever stops a *same-day* double-run — a different scheduled day is untouched.

    The weekly whole-house catch-up still uses the coarser 'done this week'
    (``pending_rooms``) — that guarantee is per-week by design.

    Only a dict carries the per-room timestamps needed to tell which day a clean
    happened; a bare set/list can't, so every member counts as done (preserving
    the prior behaviour for those callers). An unparseable timestamp counts as
    'not today', so a room still gets its scheduled clean rather than being
    skipped forever on one bad value.
    """
    if not isinstance(cleaned, dict):
        return set(cleaned)
    return {seg for seg, ts in cleaned.items() if _date_of(ts) == today}


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
        # Drop rooms already cleaned TODAY (an earlier manual/auto run covered
        # them) — but NOT rooms merely cleaned earlier this week, or a room the
        # user ticked for several days would only ever clean on the first of
        # them. The schedule must follow the ticked days (up to every day).
        done_today = cleaned_today(cleaned, now_date)
        due = [s for s in due if s not in done_today]
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


def evaluate_progress(ref, now_iso, prog, area, dist, min_escape_mm):
    """Decide whether a robot that's away from the dock is still getting somewhere.

    Pure helper for the "can't get home" watchdog. Returns ``(ref, productive)``:
    ``ref`` is the running tracker dict (created here on the first call), and
    ``productive`` is True when the robot made real headway since the last
    productive moment — so the caller re-arms its stall timer.

    Productive = ANY of:
      * task-progress % climbed above the best seen (``prog`` — fine-grained, so
        slow-but-real cleaning still counts and never false-trips), or
      * cleaned m² climbed above the best seen (``area``), or
      * it netted ``min_escape_mm`` closer to the dock than the anchor distance.

    The distance anchor is reset to the current distance on every productive
    moment (never left pinned at the dock, where the run begins), so a genuine
    return home reads as productive while a stuck/circling return does not.
    ``None`` readings are simply ignored (a missing sensor never counts as
    progress and never counts as a stall on its own)."""
    if ref is None:
        return ({
            "since": now_iso,
            "anchor_dist": dist,
            "best_area": area if area is not None else -1.0,
            "best_prog": prog if prog is not None else -1.0,
            "notified": False,
        }, True)
    productive = False
    if prog is not None and prog > ref["best_prog"]:
        ref["best_prog"] = prog
        productive = True
    if area is not None and area > ref["best_area"]:
        ref["best_area"] = area
        productive = True
    if (dist is not None and ref.get("anchor_dist") is not None
            and dist < ref["anchor_dist"] - min_escape_mm):
        productive = True
    if productive:
        ref["since"] = now_iso
        ref["anchor_dist"] = dist
        ref["notified"] = False
    return (ref, productive)
