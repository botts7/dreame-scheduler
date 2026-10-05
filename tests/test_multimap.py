"""Multi-map / multi-floor behaviour test for WeekTracker.

Exercises the real WeekTracker code with an in-memory stand-in for Home
Assistant's Store, simulating one robot with two saved maps (the RickDangerous
case): same room segment-ids on different floors must not collide.

Run: python tests/test_multimap.py   (no Home Assistant install needed)
"""
import asyncio
import copy
import importlib
import os
import sys
import types

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _MockStore:
    """In-memory Store standing in for homeassistant.helpers.storage.Store."""
    _data: dict = {}

    def __init__(self, hass, version, key):
        self._key = key

    async def async_load(self):
        return copy.deepcopy(_MockStore._data.get(self._key))

    async def async_save(self, data):
        _MockStore._data[self._key] = copy.deepcopy(data)


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.storage", Store=_MockStore)

pkg = types.ModuleType("dreame_scheduler")
pkg.__path__ = [os.path.abspath(BASE)]
sys.modules["dreame_scheduler"] = pkg
sc = importlib.import_module("dreame_scheduler.scheduler")
wt = importlib.import_module("dreame_scheduler.week_tracker")

fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


async def main():
    # 1) Two maps keep separate cleaned state — the core RickDangerous bug.
    _MockStore._data.clear()
    t = wt.WeekTracker(None, "testentry")
    await t.async_load()
    t.set_active_map("Ground")
    await t.async_mark_cleaned(["3", "5"], "2026-01-01T10:00")
    t.set_active_map("Upstairs")
    await t.async_mark_cleaned(["3"], "2026-01-01T11:00")   # same seg id, different floor
    t.set_active_map("Ground")
    check("Ground cleaned = {3,5}", set(t.cleaned.keys()) == {"3", "5"})
    check("Ground room 3 keeps the ground timestamp", t.cleaned["3"] == "2026-01-01T10:00")
    t.set_active_map("Upstairs")
    check("Upstairs cleaned = {3} only (no cross-floor collision)", set(t.cleaned.keys()) == {"3"})
    check("Upstairs room 3 keeps the upstairs timestamp", t.cleaned["3"] == "2026-01-01T11:00")

    # 2) day_dispatched is per-map (each floor fires its own daily).
    t.set_active_map("Ground")
    await t.async_set_day_dispatched("2026-01-01")
    check("Ground day_dispatched set", t.day_dispatched == "2026-01-01")
    t.set_active_map("Upstairs")
    check("Upstairs day_dispatched independent (still None)", t.day_dispatched is None)

    # 3) active_run is tagged with the active map (verification marks the right floor).
    t.set_active_map("Upstairs")
    await t.async_set_active_run({"kind": "daily", "segments": ["3"]})
    check("active_run tagged map=Upstairs", (t.active_run or {}).get("map") == "Upstairs")

    # 4) Weekly reset clears every map's counters and aggregates the summary.
    summary = await t.async_reset_week("2026-01-05")
    check("reset summary aggregates both maps", set(summary["cleaned"].keys()) == {"3", "5"})
    t.set_active_map("Ground")
    check("Ground cleared after reset", t.cleaned == {})
    t.set_active_map("Upstairs")
    check("Upstairs cleared after reset", t.cleaned == {})

    # 5) Legacy flat state migrates into maps[default] on load (single-floor upgrade).
    _MockStore._data.clear()
    _MockStore._data["dreame_scheduler_legacy"] = {
        "enabled": True, "week_start": "2026-01-01",
        "cleaned": {"7": "ts"}, "unreachable": {"9": 2}, "day_dispatched": "2026-01-01",
    }
    t2 = wt.WeekTracker(None, "legacy")
    await t2.async_load()
    t2.set_active_map("default")
    check("legacy cleaned migrated into default slice", t2.cleaned == {"7": "ts"})
    check("legacy day_dispatched migrated", t2.day_dispatched == "2026-01-01")
    check("legacy global week_start preserved", t2.week_start == "2026-01-01")
    check("no legacy per-room keys left top-level", "cleaned" not in t2.state)

    # 6) Persistence round-trip: reload keeps the per-map slices.
    await t2.async_save()
    t3 = wt.WeekTracker(None, "legacy")
    await t3.async_load()
    t3.set_active_map("default")
    check("reloaded default cleaned survives", t3.cleaned == {"7": "ts"})

    # 7) Enabling multi-floor on a legacy flat schedule must NOT blank it.
    flat = {"3": {"enabled": True, "days": [0, 1]}}
    check("flat schedule applies under 'default'", sc.rooms_for_map(flat, "default") == flat)
    check("flat schedule still applies when active map flips to 'Map 1'",
          sc.rooms_for_map(flat, "Map 1") == flat)

    print()
    print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
    sys.exit(1 if fails else 0)


asyncio.run(main())
