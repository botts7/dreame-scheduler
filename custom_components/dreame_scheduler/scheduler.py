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
      "times":    [ {"at": "HH:MM", "mop": bool}, ... ],  # optional explicit clean
                                 # slots. Absent/empty -> the room follows the
                                 # global daily_time (today's behaviour). Set ->
                                 # the room cleans at EACH listed time on its
                                 # `days`, and a slot with "mop": false forces a
                                 # sweep-only pass (a mid-day vacuum). This lets a
                                 # room clean more than once a day.
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


def _hhmm_to_min(value) -> int | None:
    """Minutes-since-midnight for an "HH:MM" string, or None if unparseable."""
    try:
        hh, mm = str(value).split(":", 1)
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


def room_times(cfg: dict) -> list[tuple[int, str, bool]]:
    """A room's explicit clean slots as ``(minute, "HH:MM", mop)`` tuples, sorted
    by time and de-duplicated by time (last one wins on a clash).

    Empty when the room has no usable ``times`` — that room then follows the
    global daily schedule (today's behaviour). Each slot's ``mop`` defaults to
    True (a full clean); ``"mop": false`` forces a sweep-only pass. Any malformed
    entry is skipped rather than crashing the tick."""
    raw = cfg.get("times") if isinstance(cfg, dict) else None
    if not isinstance(raw, list):
        return []
    by_min: dict[int, tuple[int, str, bool]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        minute = _hhmm_to_min(item.get("at"))
        if minute is None:
            continue
        mop = bool(item.get("mop", True))
        by_min[minute] = (minute, f"{minute // 60:02d}:{minute % 60:02d}", mop)
    return sorted(by_min.values(), key=lambda t: t[0])


def has_explicit_times(cfg: dict) -> bool:
    """True if a room defines at least one usable explicit clean slot — such a
    room opts OUT of the global daily fire and runs on its own ``times`` instead."""
    return bool(room_times(cfg))


def slot_key(seg: str, at: str) -> str:
    """Stable per-day de-dup key for one room-slot, e.g. ``"13@08:00"``."""
    return f"{seg}@{at}"


def due_slots(
    rooms: dict, weekday: int, now_min: int, slots_done
) -> list[tuple[str, str, bool]]:
    """Explicit-time room slots that are due right now and not yet fired today.

    For every enabled room scheduled on ``weekday`` that defines ``times``, a slot
    is due when its time is at/after ``now_min`` and its ``slot_key`` isn't already
    in ``slots_done`` (the set of keys fired earlier today). Returns
    ``(seg, slot_key, mop)`` tuples in natural segment order.

    De-dup is per-SLOT, not per-day: a room cleaned at its 08:00 slot must still
    fire at 13:00, so ``cleaned_today`` is deliberately NOT consulted here — only
    the fired-slot set gates a re-run. The engine layers presence/window/guard
    checks on the returned set, exactly like a daily dispatch."""
    done = set(slots_done or [])
    out: list[tuple[str, str, bool]] = []
    for seg, cfg in _enabled_rooms(rooms).items():
        if weekday not in (cfg.get("days") or []):
            continue
        for minute, at, mop in room_times(cfg):
            if now_min < minute:
                continue
            key = slot_key(seg, at)
            if key in done:
                continue
            out.append((seg, key, mop))
    return sorted(out, key=lambda t: _seg_sort_key(t[0]))


def next_slot_minute(rooms: dict, weekday: int, after_min: int | None = None) -> int | None:
    """Earliest explicit-slot minute on ``weekday`` across enabled rooms, or None.
    With ``after_min`` set, only slots strictly after it count (for 'later today');
    pass None to get the day's first slot (for a future day)."""
    mins = [
        minute
        for seg, cfg in _enabled_rooms(rooms).items()
        if weekday in (cfg.get("days") or [])
        for minute, _at, _mop in room_times(cfg)
        if after_min is None or minute > after_min
    ]
    return min(mins) if mins else None


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
    daily, mop-after-sweep every second day. N==1 means "mop on every clean"
    (cadence off), so the base mode is used unchanged; N==0 means "never mop"
    (all-rug rooms) — always sweep-only, whatever the base mode.

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
    # N == 0 → "never mop": this room always sweeps, whatever its base mode
    # (all-rug rooms, per the community request — mopping there is wasted water).
    if n <= 0:
        return count, SWEEP_ONLY_MODE, False
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
    kind: str = ""  # "daily" | "catchup" | "manual" | "slot"
    segments: list[str] = field(default_factory=list)
    reason: str = ""
    # For a "slot" dispatch (per-room extra times):
    sweep_only: list[str] = field(default_factory=list)  # segs forced sweep-only this pass
    slot_keys: list[str] = field(default_factory=list)   # fired-slot keys to persist


def tracker_stale(age_seconds, stale_after_min) -> bool:
    """True when a presence entity hasn't reported for longer than
    ``stale_after_min`` minutes (so it should be ignored — a wedged phone tracker
    stuck at 'home' must not silently block cleaning). ``stale_after_min`` <= 0
    disables the check; an unknown age (None) is never treated as stale."""
    try:
        limit = int(stale_after_min)
    except (TypeError, ValueError):
        return False
    if limit <= 0 or age_seconds is None:
        return False
    return age_seconds > limit * 60


def holiday_hold(enabled, away_days, after_days, house_clean) -> bool:
    """True when an extended-away 'holiday' should pause cleaning: it's enabled,
    the house is already clean (nothing pending this week), and everyone has been
    away for at least ``after_days``. The engine gates the scheduled clean on this
    so a long absence stops re-cleaning an already-clean, empty house. The first
    clean(s) after leaving still run (house not yet clean / away < after_days), and
    the weekly reset naturally re-cleans once a week (pending refills → not clean →
    hold releases). ``away_days`` is None when presence is unknown/undetermined."""
    return bool(enabled) and bool(house_clean) and away_days is not None \
        and away_days >= int(after_days)


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
    slots_done=None,
    opportunistic_catchup: bool = False,
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

    Per-room explicit ``times`` (``slots_done`` = fired-slot keys today) take
    priority: such a room opts out of the global daily fire and runs at each of
    its slots instead, which is what lets a room clean more than once a day.
    """
    today_iso = now_date.isoformat()

    # Per-room extra times: any slot due now and not yet fired today. Checked
    # first (they carry a precise time); if two kinds are due on one tick the
    # others follow on the next. Slotted rooms are excluded from the daily fire
    # below, so a room never double-runs from both paths.
    slot_due = due_slots(rooms, weekday, now_min, slots_done)
    if slot_due:
        # A room can have >1 slot due on the same tick (e.g. after a restart, or
        # its 08:00 and 13:00 both pending). Clean it once, mark every fired slot,
        # and let a full-clean slot win over a sweep-only one (mop covers sweep).
        segs: list[str] = []
        mop_by_seg: dict[str, bool] = {}
        for s, _k, mop in slot_due:
            if s not in mop_by_seg:
                segs.append(s)
            mop_by_seg[s] = mop_by_seg.get(s, False) or mop
        return Decision(
            action="dispatch", kind="slot",
            segments=segs,
            sweep_only=[s for s in segs if not mop_by_seg[s]],
            slot_keys=[k for _s, k, _m in slot_due],
            reason=f"per-room scheduled times for {today_iso}",
        )

    # Daily: today's scheduled rooms, once per day, at/after the daily time.
    if (
        daily_time_min is not None
        and now_min >= daily_time_min
        and day_dispatched_on != today_iso
    ):
        # Rooms with their own explicit `times` run on those slots (above), not
        # the global daily time — drop them from the daily set.
        due = [s for s in rooms_due_today(rooms, weekday)
               if not has_explicit_times(rooms.get(s, {}))]
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

    # Whole-house catch-up of rooms still pending this week. Normally only on the
    # configured catch-up weekday; with opportunistic catch-up on it may fire on
    # ANY day (from the catch-up time), so a run repeatedly blocked by presence
    # gets mopped up the next time the house is empty rather than waiting for the
    # one weekly day. Either way at most once per day (catchup_dispatched_on), and
    # the engine's presence gate means it only runs when the house is actually
    # empty. Priority stays below daily/slots, so it only handles leftovers.
    catchup_today = (catchup_enabled and weekday == catchup_day) or opportunistic_catchup
    if (
        catchup_today
        and catchup_time_min is not None
        and now_min >= catchup_time_min
        and catchup_dispatched_on != today_iso
    ):
        pend = pending_rooms(rooms, cleaned)
        if pend:
            reason = ("catch-up: pending rooms, house free"
                      if opportunistic_catchup and weekday != catchup_day
                      else "weekly whole-house catch-up")
            return Decision(action="dispatch", kind="catchup", segments=pend,
                            reason=reason)
        return Decision(action="idle", kind="catchup", reason="week_already_complete")

    return Decision(action="idle", reason="not_time")


def edge_zones_for_box(x0, y0, x1, y1, width):
    """Four thin axis-aligned rectangles hugging the walls of a room's map box
    [x0,y0,x1,y1] — the strips an edge pass zone-cleans. Pure geometry: each
    strip is clamped inside the box and never wider than half the box (so a
    narrow room collapses to a couple of overlapping strips, not garbage).
    Coordinates are in the robot's map mm (same as vacuum_clean_zone)."""
    x0, x1 = (x0, x1) if x0 <= x1 else (x1, x0)
    y0, y1 = (y0, y1) if y0 <= y1 else (y1, y0)
    w = max(60, min(width, (x1 - x0) / 2, (y1 - y0) / 2))
    strips = [
        [x0, y0, x0 + w, y1],     # one wall
        [x1 - w, y0, x1, y1],     # opposite wall
        [x0, y0, x1, y0 + w],     # third wall
        [x0, y1 - w, x1, y1],     # fourth wall
    ]
    return [[int(round(v)) for v in s] for s in strips]


def edge_due(last_iso, every_days, today):
    """True when a dedicated edge clean is due: never run, or the last one was
    ``every_days`` or more days ago. ``last_iso`` is the YYYY-MM-DD of the last
    edge run (or None); ``today`` is a date. ``every_days`` < 1 disables it."""
    if int(every_days) < 1:
        return False
    last = _date_of(last_iso)
    if last is None:
        return True
    return (today - last).days >= int(every_days)


def fold_room_area(prev, sample, lo, hi, *, alpha=0.3, min_sample=1.0):
    """Fold one finished room's cleaned area `sample` (m²) into a per-room running
    estimate — the self-tuning that lets the edge cut adapt to each real room instead
    of a hand-picked constant. Pure/deterministic (no ML): clamp the sample into
    [lo, hi] to reject a stuck/partial run, seed on the first good sample, then EMA.

    ``prev`` is ``{"area": float, "n": int}`` or None. Returns the updated dict, or
    ``prev`` unchanged when the sample is unusable (None / too small / bad bounds)."""
    try:
        s = float(sample)
    except (TypeError, ValueError):
        return prev
    if s < float(min_sample) or hi <= 0 or lo > hi:
        return prev
    s = min(max(s, float(lo)), float(hi))
    if not isinstance(prev, dict) or int(prev.get("n", 0) or 0) <= 0:
        return {"area": round(s, 1), "n": 1}
    a = float(alpha)
    blended = (1.0 - a) * float(prev.get("area", s)) + a * s
    return {"area": round(blended, 1), "n": int(prev["n"]) + 1}


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
