"""Allowed-cleaning-window logic — pure & testable.

A user can restrict cleaning to a time window (e.g. 09:00-16:00 while the
house is empty) so the robot never starts during the evening or overnight.
One policy relaxes the tail end:

  * ``overrun`` — if a clean is already RUNNING when the window closes, let
                  it finish rather than force-docking a half-done job. This
                  only ever EXTENDS a running clean; it never STARTS one
                  outside the window.

This module deliberately has no Home Assistant imports so the decisions can
be unit-tested in isolation. Times are "minutes since local midnight"
(0-1439). Windows may wrap past midnight: ``start > end`` means the window
spans midnight (e.g. 22:00-06:00).

Adapted from the wallbox_gateway charge_window with the SOC/departure logic
dropped — a vacuum has no target-SOC/pre-start concept, just "may I run now".
"""

from __future__ import annotations


def to_minutes(hhmm: str | None) -> int | None:
    """'HH:MM' (or 'HH:MM:SS') -> minutes since midnight, or None if unparseable."""
    if not hhmm:
        return None
    parts = str(hhmm).split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def in_window(now_min: int, start_min: int | None, end_min: int | None) -> bool:
    """Is ``now`` within [start, end)? Handles midnight wrap. An unset or
    zero-length window is treated as 'always' (no restriction)."""
    if start_min is None or end_min is None or start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= now_min < end_min
    # wraps past midnight
    return now_min >= start_min or now_min < end_min


def evaluate(
    now_min: int,
    *,
    start: str | None,
    end: str | None,
    enabled: bool = True,
    overrun: bool = False,
    already_cleaning: bool = False,
) -> dict:
    """Decide whether the robot may run *right now* under the window policy.

    Returns a dict:
      in_window      — is ``now`` inside the configured window?
      allow_start    — may we START a fresh clean at this instant?
      allow_continue — may an already-running clean keep going right now?
      reason         — 'disabled' | 'no_window' | 'in_window' |
                       'overrun' | 'outside_window'

    A disabled window, or an unset/zero-length one, means "always allowed".
    ``overrun`` requires ``already_cleaning`` — it can only extend a running
    job past the window end, never initiate one outside the window.
    """
    if not enabled:
        return {"in_window": True, "allow_start": True,
                "allow_continue": True, "reason": "disabled"}

    s = to_minutes(start)
    e = to_minutes(end)

    if s is None or e is None or s == e:
        return {"in_window": True, "allow_start": True,
                "allow_continue": True, "reason": "no_window"}

    if in_window(now_min, s, e):
        return {"in_window": True, "allow_start": True,
                "allow_continue": True, "reason": "in_window"}

    # Outside the window: never start; only continue if overrun is on AND a
    # clean is already running (finish the job we already began).
    if overrun and already_cleaning:
        return {"in_window": False, "allow_start": False,
                "allow_continue": True, "reason": "overrun"}

    return {"in_window": False, "allow_start": False,
            "allow_continue": False, "reason": "outside_window"}
