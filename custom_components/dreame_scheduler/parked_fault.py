"""Faults that halt a run without putting the vacuum in `error`: pure, no HA imports.

Station faults (an empty clean water tank, a full dirty tank, a missing tank)
stop the task at the dock with the vacuum entity reading `paused`, not `error`.
The error text carries the fault, but every alert and watchdog keyed off the
`error` state, so nothing fired. Live 2026-11-06: `water_tank_dry` at 13:14 left
an Entrance run paused on the dock for 26 h, no alert, and the next day's clean
never went out because the run was still open.
"""

from __future__ import annotations

# Wait this long before alerting: the robot blips through odd states while it
# washes or swaps mops at the dock.
ALERT_AFTER_SECONDS = 120
# Past this, close the run as interrupted so its rooms stay pending and the next
# scheduled clean isn't blocked.
CLOSE_AFTER_SECONDS = 3 * 3600

_NO_ERROR = ("", "no error", "no_error", "none", "unknown", "unavailable")
# sensor.error also carries maintenance reminders that never halt a task.
_REMINDER_WORDS = ("clean_mop_pad", "sensor_dirty", "dirty_sensor", "clean_sensor", "replace")
# A mid-clean recharge reports battery_low and then pauses on the dock: that is
# the robot carrying on normally, not a fault (live 2026-11-19, twice in one run).
_ROUTINE_WORDS = ("battery", "charg")

# Plain-language fix for the faults we know. Anything else gets a generic line.
_FIXES = (
    ("water_tank_dry", "Refill the clean water tank."),
    ("low_water", "Refill the clean water tank."),
    ("clean_water", "Check the clean water tank is filled and seated."),
    ("dirty_water", "Empty the dirty water tank and reseat it."),
    ("sewage", "Empty the dirty water tank and reseat it."),
    ("water_tank", "Check both water tanks are filled, emptied and seated."),
    ("dust_bag", "Check the dust bag in the base."),
)


def parked_fault(vacuum_state: str | None, error_text: str | None) -> bool:
    """True when the task is halted (`paused`) and the robot reports a real fault.

    The vacuum's own `error` state is handled elsewhere; this covers the paused
    case only, and ignores maintenance reminders."""
    if (vacuum_state or "").strip().lower() != "paused":
        return False
    err = (error_text or "").strip().lower()
    if err in _NO_ERROR:
        return False
    return not any(w in err for w in _REMINDER_WORDS + _ROUTINE_WORDS)


def fault_fix(error_text: str | None) -> str:
    """What the user should do about it, in one short sentence."""
    err = (error_text or "").strip().lower()
    for word, fix in _FIXES:
        if word in err:
            return fix
    return "Check the robot and the base in the Dreame app."


def plan(fault_seconds: float, notified: bool) -> str:
    """What to do this tick, given how long the fault has lasted.

    Returns "close" past CLOSE_AFTER_SECONDS, "alert" once past
    ALERT_AFTER_SECONDS if not yet notified, else "wait"."""
    if fault_seconds >= CLOSE_AFTER_SECONDS:
        return "close"
    if fault_seconds >= ALERT_AFTER_SECONDS and not notified:
        return "alert"
    return "wait"
