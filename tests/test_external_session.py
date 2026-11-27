"""Credit for cleans started outside the scheduler (#4).

Run: python tests/test_external_session.py   (no Home Assistant install needed)
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


es = load("external_session")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


START = "2026-11-18T09:00:00+11:00"
START_TS = 1794952800  # same instant, epoch seconds


def rec(ts, completed, blocked=None):
    r = {"timestamp": ts, "completed": completed}
    if blocked is not None:
        r["blocked_rooms"] = blocked
    return r


# ---- opening and following ----
s = es.open_session(START, 465, START_TS - 5000, [4, 7], "Through App")
check("open: segments as strings, sorted", s["segments"] == ["4", "7"])
check("open: remembers how it started", s["how"] == "Through App")
es.note_progress(s, [15], 4)
es.note_progress(s, None, 7)
es.note_progress(s, None, 7)
check("progress: late segment list merged", s["segments"] == ["15", "4", "7"])
check("progress: visited rooms in order, no repeats", s["visited"] == ["4", "7"])
es.note_progress(s, None, None)
es.note_progress(s, None, 0)
check("progress: empty current segment ignored", s["visited"] == ["4", "7"])

# ---- matching the robot's record to the session ----
check("record: the one that existed at the start is not ours",
      es.record_for_session(s, rec(START_TS - 5000, True)) is None)
check("record: a newer record from before the start is not ours",
      es.record_for_session(s, rec(START_TS - 600, True)) is None)
check("record: within clock skew counts", es.record_for_session(s, rec(START_TS - 60, True)) is not None)
check("record: after the start counts", es.record_for_session(s, rec(START_TS + 30, True)) is not None)
check("record: garbage ignored", es.record_for_session(s, {"timestamp": "x"}) is None)
check("record: none", es.record_for_session(s, None) is None)

# ---- what gets credited ----
room = es.open_session(START, 1, 0, ["4", "7"], None)
room["visited"] = ["4", "7", "15"]
check("credit: room clean credits rooms asked for and visited (transit room 15 not credited)",
      es.rooms_to_credit(room, rec(START_TS, True)) == ["4", "7"])
check("credit: blocked room left out",
      es.rooms_to_credit(room, rec(START_TS, True, {"7": "Blocked by dynamic obstacle"})) == ["4"])
never = es.open_session(START, 1, 0, ["1", "2"], None)
never["visited"] = ["2"]
check("credit: asked room never entered is not credited (Lounge Room2, 2026-11-19)",
      es.rooms_to_credit(never, rec(START_TS, True)) == ["2"])
blind = es.open_session(START, 1, 0, ["8"], None)
check("credit: no room sightings at all earns nothing", es.rooms_to_credit(blind, rec(START_TS, True)) == [])
check("credit: interrupted clean earns nothing", es.rooms_to_credit(room, rec(START_TS, False)) == [])
check("credit: no record earns nothing", es.rooms_to_credit(room, None) == [])
house = es.open_session(START, 1, 0, [], None)
house["visited"] = ["2", "8", "1"]
check("credit: whole-house clean credits only rooms visited",
      es.rooms_to_credit(house, rec(START_TS, True)) == ["2", "8", "1"])
check("credit: whole-house blocked room left out",
      es.rooms_to_credit(house, rec(START_TS, True, {"8": "Step too low"})) == ["2", "1"])

# ---- stricter checks (from PR #5 review) ----
check("credit: interrupt reason other than a finish earns nothing",
      es.rooms_to_credit(room, dict(rec(START_TS, True), interrupt_reason="Manually stopped")) == [])
check("credit: 'Task completed' interrupt reason still credits",
      es.rooms_to_credit(room, dict(rec(START_TS, True), interrupt_reason="Task completed")) == ["4", "7"])
check("credit: 'Incomplete' is not a normal finish",
      es.rooms_to_credit(room, dict(rec(START_TS, True), interrupt_reason="Incomplete")) == [])
check("credit: skipped_rooms left out",
      es.rooms_to_credit(room, dict(rec(START_TS, True), skipped_rooms={"4": "x"})) == ["7"])
check("credit: odd blocked_rooms type ignored, no crash",
      es.rooms_to_credit(room, dict(rec(START_TS, True), blocked_rooms="7")) == ["4", "7"])

# ---- run-history row ----
row = es.history_entry("2026-11-19T21:50:00+11:00", 3, ["8"], "Through App")
check("history: kind external", row["kind"] == "external")
check("history: room marked cleaned", row["per_room"] == {"8": {"status": "cleaned", "mode": "native", "will_mop": None}})
check("history: keeps how it started", row["how"] == "Through App")

# ---- closing ----
check("close: completed record credits", es.close_action(10, rec(START_TS, True)) == "credit")
check("close: incomplete record waits for a mid-task dock", es.close_action(600, rec(START_TS, False)) == "wait")
check("close: incomplete record that never resumes drops",
      es.close_action(es.INCOMPLETE_DWELL_SECONDS, rec(START_TS, False)) == "drop")
check("close: no record yet waits", es.close_action(60, None) == "wait")
check("close: no record after the wait drops", es.close_action(es.GIVE_UP_SECONDS, None) == "drop")

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
