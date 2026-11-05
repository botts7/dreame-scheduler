"""Mop-pad-lost handling: pure decisions, no Home Assistant imports.

When a mop pad comes off out on the floor the robot reports `mop_removed` and
stops in `error`. No manoeuvre puts the pad back and driving on just re-errors,
so the engine sends it home, tells the user where the pad is, and ends any run
in flight. Live 2026-10-27: a pad came off while the L20 was returning from a
clean and it sat errored mid-room for ~2.5 min with no alert.

This module decides WHEN that applies and WHEN to (re)send the robot home; the
engine does the HA calls.
"""

from __future__ import annotations

# Error-sensor substrings meaning a pad came OFF the robot. `edge_mop_detached`
# (the side mop) is covered by `mop_detached`. Deliberately NOT `clean_mop_pad`,
# which is only a maintenance reminder.
MOP_LOST_ERROR_WORDS = ("mop_removed", "mop_detached", "mop_lost")

RETRY_SECONDS = 20   # still errored this long after a send-home -> try once more
MAX_SENDS = 2        # send-home attempts per episode, then hand over to the
                     # stranded watchdog (it escalates if the robot never moves)


def is_mop_lost(vacuum_state: str | None, error_text: str | None, at_dock: bool) -> bool:
    """True when the robot dropped a pad OUT ON THE FLOOR.

    Needs the vacuum in `error` (the error sensor alone can carry stale text) and
    the robot away from the dock: the L20 also detaches its pads at the dock on
    purpose (carpet "remove mop"), which must not trigger an alert."""
    if (vacuum_state or "") != "error" or at_dock:
        return False
    err = (error_text or "").lower()
    return bool(err) and any(w in err for w in MOP_LOST_ERROR_WORDS)


def plan_send(sends: int, seconds_since_last_send: float | None,
              *, max_sends: int = MAX_SENDS, retry_seconds: float = RETRY_SECONDS) -> tuple[bool, bool]:
    """Decide this tick's action for an ongoing mop-lost episode.

    Returns ``(send_now, keep_owning)``:
      - send_now: issue `return_to_base` now (first sighting, or still errored a
        full retry window after the last send, up to ``max_sends``).
      - keep_owning: the episode still owns the robot this tick, so other
        watchdogs should stand down. After the last send has had its retry
        window, ownership is handed back so the stranded watchdog can escalate a
        robot that never actually left.
    """
    if sends <= 0 or seconds_since_last_send is None:
        return True, True
    window_open = seconds_since_last_send < retry_seconds
    if sends < max_sends and not window_open:
        return True, True
    return False, sends < max_sends or window_open
