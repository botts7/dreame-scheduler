"""How long the robot actually spent cleaning in each room during a run. Pure.

When a run ends early and no saved map can be read (the robot never logged a
record for that session), crediting falls back to the rooms the robot was
seen in. Seen isn't cleaned: live 2026-11-26 it passed Kitchen Dining, Kitchen
and Entrance in 36 seconds on its way to the Lounge Room, got stuck there, and
the run credited Kitchen and Kitchen Dining. So the fallback now also needs
the robot to have spent real time cleaning in the room.

The engine adds time on each evaluation while the robot is cleaning, to the
room it's in. Gaps are capped so a missed tick or an HA restart can't award a
room hours it didn't spend.
"""

from __future__ import annotations

# A room counts as cleaned from sightings only after this much cleaning time
# in it. A drive-through takes seconds; even a tiny room takes over a minute.
MIN_DWELL_SECONDS = 60
# Longest gap between two evaluations that is counted (ticks are 60 s, plus a
# state event on every room change).
MAX_STEP_SECONDS = 120


def add(dwell: dict | None, room: str | None, elapsed: float) -> dict:
    """``dwell`` plus up to MAX_STEP_SECONDS of cleaning time for ``room``."""
    out = dict(dwell or {})
    if room and elapsed and elapsed > 0:
        out[room] = round(float(out.get(room, 0)) + min(float(elapsed), MAX_STEP_SECONDS), 1)
    return out


def counts(dwell: dict | None, room: str | None) -> bool:
    """Whether a sighting of ``room`` is backed by enough cleaning time. Runs
    started before dwell was tracked (no data at all) keep the old behaviour."""
    if not dwell:
        return True
    return bool(room) and float(dwell.get(room, 0)) >= MIN_DWELL_SECONDS
