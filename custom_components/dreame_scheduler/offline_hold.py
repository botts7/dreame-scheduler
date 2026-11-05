"""Robot-offline handling: pure decisions, no Home Assistant imports.

Cloud-only Dreames (e.g. the L20) go `unavailable` in HA whenever the internet
or the Dreame cloud drops. Before this module the start gates all failed OPEN
on an unavailable robot (unknown battery = fine, unknown station = fine,
`unavailable` is not `error`), so a scheduled clean that fell inside an outage
was "dispatched" into the void and its day marked done: the rooms then waited
for the catch-up. Live 2026-10-29: the internet was out 04:50 to 08:56; a 10:00
start would have been lost.

Now an offline robot HOLDS the dispatch (the day stays due) and it goes out as
soon as the robot reconnects. This module decides when the robot counts as
offline and what to tell the user once a hold resolves.
"""

from __future__ import annotations

OFFLINE_REASON = "robot offline, waiting for it to reconnect"

# A hold shorter than this is just HA (re)loading the robot's entities after a
# restart, not an outage worth a notification.
NOTICE_MIN_SECONDS = 600

_UNAVAILABLE = ("unknown", "unavailable", "none", "")


def robot_online(vacuum_state: str | None) -> bool:
    """True when the vacuum entity reports a real state.

    `unavailable` / `unknown` / missing all mean HA can't talk to the robot
    right now (internet or Dreame cloud down, or entities still loading)."""
    return vacuum_state is not None and str(vacuum_state).strip().lower() not in _UNAVAILABLE


def _duration(seconds: float) -> str:
    mins = int(seconds // 60)
    if mins < 60:
        return f"{mins} min"
    hours, rem = divmod(mins, 60)
    return f"{hours} h" if rem == 0 else f"{hours} h {rem} min"


def hold_notice(held_seconds: float, outcome: str,
                *, min_seconds: float = NOTICE_MIN_SECONDS) -> tuple[str, str] | None:
    """The (title, message) to send when an offline hold resolves, or None.

    ``outcome`` is ``"running"`` (the robot reconnected and the held clean went
    out) or ``"missed"`` (it couldn't go out that day: the cleaning window
    closed, or the day rolled over, while it was still offline). Holds shorter
    than ``min_seconds`` stay silent; that's an HA restart, not an outage."""
    if held_seconds < min_seconds:
        return None
    took = _duration(held_seconds)
    if outcome == "running":
        return ("Vacuum back online",
                f"The scheduled clean was held for {took} because the vacuum was "
                "offline (no connection to the Dreame cloud). It's running now.")
    if outcome == "missed":
        return ("Scheduled clean missed",
                f"The vacuum was offline for {took} and couldn't run its scheduled "
                "clean in time. Those rooms stay pending and the catch-up will get them.")
    return None
