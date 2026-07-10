"""Pure analytics over the run-history log — no Home Assistant imports, so it's
fully unit-testable in isolation (like clean_window / scheduler).

Each history entry looks like::

    {
      "ts": "iso",
      "weekday": 0-6,                       # Mon=0 .. Sun=6
      "kind": "daily|catchup|manual",
      "per_room": {
        "<seg>": {"status": "cleaned"|"skipped"|"failed", "reason": str|None, ...}
      }
    }

Only runs that actually TARGETED a room appear for that room, so streaks and
weekday rates ignore days the room wasn't scheduled.
"""

from __future__ import annotations


def _room(entry: dict, seg: str) -> dict | None:
    return (entry.get("per_room") or {}).get(str(seg))


def consecutive_fails(history: list, seg: str) -> int:
    """Times in a row the room was NOT cleaned, over the most recent runs that
    targeted it, up until the last time it WAS cleaned. 0 if the latest outcome
    was a clean (or it never appears)."""
    n = 0
    for entry in reversed(history):
        r = _room(entry, seg)
        if r is None:
            continue
        if r.get("status") == "cleaned":
            break
        n += 1
    return n


def last_fail_reason(history: list, seg: str) -> str | None:
    """The reason attached to the most recent non-clean outcome, if the room's
    latest outcome was a miss; else None."""
    for entry in reversed(history):
        r = _room(entry, seg)
        if r is None:
            continue
        return r.get("reason") if r.get("status") != "cleaned" else None
    return None


def weekday_stats(history: list, seg: str) -> dict:
    """Per-weekday {"attempts", "cleaned"} counts for a room."""
    stats = {d: {"attempts": 0, "cleaned": 0} for d in range(7)}
    for entry in history:
        r = _room(entry, seg)
        if r is None:
            continue
        d = entry.get("weekday")
        if not isinstance(d, int) or not 0 <= d < 7:
            continue
        stats[d]["attempts"] += 1
        if r.get("status") == "cleaned":
            stats[d]["cleaned"] += 1
    return stats


def suggest_better_day(history: list, seg: str, current_days,
                       min_attempts: int = 2, good_rate: float = 0.75) -> int | None:
    """If a room keeps missing on its CURRENT scheduled days, suggest a weekday
    where it cleans reliably and isn't already scheduled. None when the current
    schedule is fine, there's too little data, or no confidently-better day.
    Returns a weekday index (Mon=0 .. Sun=6)."""
    stats = weekday_stats(history, seg)
    cur = {d for d in (current_days or []) if isinstance(d, int)}

    cur_att = sum(stats[d]["attempts"] for d in cur)
    cur_clean = sum(stats[d]["cleaned"] for d in cur)
    if cur_att < min_attempts or (cur_att and cur_clean / cur_att >= good_rate):
        return None  # not enough evidence, or current days already work

    best, best_rate = None, good_rate
    for d in range(7):
        if d in cur:
            continue
        att = stats[d]["attempts"]
        if att >= min_attempts:
            rate = stats[d]["cleaned"] / att
            if rate >= best_rate:
                best, best_rate = d, rate
    return best
