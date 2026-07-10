"""Cleaning guards — preconditions that must hold before we dispatch a clean.

Pure and testable: no Home Assistant imports, so every decision can be
exercised in isolation. Callers translate live entity states into the simple
booleans/values these functions take.

Design stance mirrors the wallbox charge_guards: guards fail SAFE where acting
wrongly is disruptive (presence — never run while someone's home if they asked
for away-only), and fail OPEN where a missing/unknown sensor shouldn't block
the core function (a consumable sensor the user didn't wire up must not wedge
the scheduler shut).
"""

from __future__ import annotations

# Consumable/station sensor states (from the dreame_vacuum integration) that
# mean "attention needed" and should defer a clean. Compared case-insensitively.
_BLOCKING_STATION_STATES = {"full", "low", "no", "error", "fault", "missing"}


def presence_blocks(require_away: bool, anyone_home: bool | None) -> bool:
    """True when presence should BLOCK a start.

    Fail-safe: if ``require_away`` is on and presence is unknown (None), we
    treat it as 'someone might be home' and block — better a skipped clean than
    a robot waking a napping household. If away-only isn't required, never
    blocks.
    """
    if not require_away:
        return False
    if anyone_home is None:
        return True
    return anyone_home


def station_ready(
    *,
    dust_bag: str | None = None,
    clean_water: str | None = None,
    dirty_water: str | None = None,
    error_active: bool = False,
) -> tuple[bool, list[str]]:
    """Is the dock/robot in a fit state to start a mopping+vacuum job?

    Each station argument is the raw sensor state string (or None if the user
    hasn't opted to guard on it). A value in ``_BLOCKING_STATION_STATES`` defers
    the clean with a human reason. Unknown/None values fail OPEN (don't block) —
    only an explicitly bad state stops us. Returns (ready, reasons).
    """
    reasons: list[str] = []

    def _bad(state: str | None) -> bool:
        return isinstance(state, str) and state.strip().lower() in _BLOCKING_STATION_STATES

    if error_active:
        reasons.append("robot reporting an error")
    if _bad(dust_bag):
        reasons.append("dust bag full")
    if _bad(clean_water):
        reasons.append("clean-water tank low")
    if _bad(dirty_water):
        reasons.append("dirty-water tank full")

    return (not reasons, reasons)


def battery_ok(level: float | int | str | None, minimum: int) -> bool:
    """True if battery is at/above ``minimum`` (%). Fail-open on unknown level
    so a missing/odd battery sensor never wedges the scheduler; the robot's own
    low-battery handling remains the backstop."""
    if minimum <= 0:
        return True
    try:
        return float(level) >= float(minimum)
    except (TypeError, ValueError):
        return True


def room_reachable(door_state: str | None, *, open_states: tuple[str, ...] = ("on", "open")) -> bool:
    """Given a mapped door contact-sensor state, is the room reachable?

    A room with no mapped door sensor (door_state is None) is always considered
    reachable — we only *proactively* skip when a sensor explicitly says the
    door is shut. Contact sensors vary: device_class door/opening report 'on'
    (or 'open') when OPEN, so those states mean reachable; anything else
    ('off'/'closed') means shut. Unknown/unavailable fails OPEN (reachable) so a
    flaky sensor doesn't silently drop a room from the schedule.
    """
    if door_state is None:
        return True
    s = str(door_state).strip().lower()
    if s in ("unknown", "unavailable", ""):
        return True
    return s in open_states
