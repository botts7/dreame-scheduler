"""Cleaning time per room: the sightings fallback for runs that end early with
no saved map to check.

Run: python tests/test_room_dwell.py   (no Home Assistant install needed)
"""
import importlib.util
import os
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rd = load("room_dwell")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


# Live 2026-11-26: Kitchen Dining, Kitchen, Entrance passed in 36 s at 10:01,
# then 24 minutes cleaning the Lounge Room before getting stuck.
d = {}
for room, secs in (("Kitchen Dining", 6), ("Kitchen", 6), ("Entrance", 24)):
    d = rd.add(d, room, secs)
for _ in range(24):
    d = rd.add(d, "Lounge Room", 60)
check("drive-through rooms don't count (2026-11-26)",
      not rd.counts(d, "Kitchen Dining") and not rd.counts(d, "Kitchen") and not rd.counts(d, "Entrance"))
check("the room it actually cleaned counts", rd.counts(d, "Lounge Room"))
check("a long gap is capped (missed tick / restart)", rd.add({}, "Kitchen", 3600)["Kitchen"] == rd.MAX_STEP_SECONDS)
check("no room, nothing added", rd.add({}, None, 60) == {})
check("negative time ignored", rd.add({}, "Kitchen", -5) == {})
check("runs with no dwell data keep the old behaviour", rd.counts(None, "Kitchen") and rd.counts({}, "Kitchen"))
check("a tiny room cleaned for a minute counts", rd.counts(rd.add({}, "Lounge Room2", 61), "Lounge Room2"))
check("input not modified", (lambda x: (rd.add(x, "A", 10), x == {})[1])({}))

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
