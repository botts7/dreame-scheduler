"""Station and water faults: what kind they are, when to alert. Pure, no HA imports.

Two gaps, both found live:

* A fault the base reports while no scheduler run is going (dust bag full after
  an app-started clean, 2026-11-22) alerted nobody: fault alerts only existed
  inside scheduler runs.
* A water fault (clean tank dry, 2026-11-06 and 2026-11-20) halted the clean
  and every clean after it until someone refilled. Tested 2026-11-22: the L20
  vacuums fine with the tank dry when the room is set to vacuum only, and the
  dry-tank error clears as soon as the halted task is stopped.

So a water fault during a run turns the rest of that day's cleaning into
vacuum only, and mopping is simply tried again on the next scheduled day.
There's no reliable "tank refilled" signal on the L20 (the low-water flag read
false while dry, and the error clears on stop), so retrying is how we find out.
"""

from __future__ import annotations

# Faults that only stop mopping: the robot can still vacuum.
_WATER_WORDS = ("water_tank", "clean_water", "low_water", "dirty_water", "sewage", "mop_install", "install_mop")
# Base faults worth telling someone about even outside a run.
_STATION_WORDS = ("dust_bag", "bag_full", "dust_bin", "vents", "station", "base_") + _WATER_WORDS
_NO_ERROR = ("", "no error", "no_error", "none", "unknown", "unavailable")

# A fault has to be gone this long before the same one can alert again (the
# error text blinks off and on as the robot changes state).
CLEAR_AFTER_SECONDS = 600

_FIXES = (
    ("water_tank_dry", "Refill the clean water tank."),
    ("low_water", "Refill the clean water tank."),
    ("clean_water", "Check the clean water tank is filled and seated."),
    ("dirty_water", "Empty the dirty water tank and reseat it."),
    ("sewage", "Empty the dirty water tank and reseat it."),
    ("water_tank", "Check both water tanks are filled, emptied and seated."),
    ("dust_bag", "Replace the dust bag in the base, or clear its vents."),
    ("bag_full", "Replace the dust bag in the base, or clear its vents."),
    ("mop_install", "Check the mop pads are seated on the base."),
    ("install_mop", "Check the mop pads are seated on the base."),
)


def _norm(error_text) -> str:
    return str(error_text or "").strip().lower()


def kind(error_text) -> str | None:
    """'water' (mopping is blocked, vacuuming isn't), 'station' (another base
    fault), or None (no fault, or not a base fault)."""
    err = _norm(error_text)
    if err in _NO_ERROR:
        return None
    if any(w in err for w in _WATER_WORDS):
        return "water"
    if any(w in err for w in _STATION_WORDS):
        return "station"
    return None


def fix(error_text) -> str:
    err = _norm(error_text)
    for word, text in _FIXES:
        if word in err:
            return text
    return "Check the robot and the base in the Dreame app."


def idle_alert(state: dict | None, error_text, now_ts: float) -> tuple[bool, dict | None]:
    """Whether to alert about a base fault seen outside a run, and the state to
    keep. ``state`` is {"fault", "seen", "cleared_at"} or None.

    Alerts once per fault. The same fault alerts again only after it has been
    gone for CLEAR_AFTER_SECONDS; a different fault alerts straight away."""
    err = _norm(error_text)
    if kind(err) is None:
        if state and state.get("fault") and not state.get("cleared_at"):
            state = dict(state, cleared_at=now_ts)
        if state and state.get("cleared_at") and now_ts - float(state["cleared_at"]) >= CLEAR_AFTER_SECONDS:
            return False, None
        return False, state
    if state and state.get("fault") == err:
        return False, dict(state, cleared_at=None)
    return True, {"fault": err, "seen": now_ts, "cleared_at": None}


def mop_hold_active(hold_day: str | None, today_iso: str) -> bool:
    """Vacuum only for the rest of the day a water fault hit; mopping is tried
    again from the next day."""
    return bool(hold_day) and hold_day == today_iso
