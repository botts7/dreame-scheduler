"""Behavioural tests for the HA-import-free scheduler logic.

Run: python tests/test_logic.py   (no Home Assistant install needed)
"""
import importlib.util
import os
import sys
from datetime import date

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses needs the module resolvable by name
    spec.loader.exec_module(mod)
    return mod


cw = load("clean_window")
cg = load("clean_guards")
sc = load("scheduler")
ha = load("history_analytics")

fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


# ---- clean_window ----
check("to_minutes 09:30 -> 570", cw.to_minutes("09:30") == 570)
check("to_minutes HH:MM:SS", cw.to_minutes("16:00:00") == 960)
check("in_window normal inside", cw.in_window(600, 540, 960) is True)
check("in_window normal outside", cw.in_window(1000, 540, 960) is False)
check("in_window midnight wrap inside", cw.in_window(30, 1320, 360) is True)
check("in_window midnight wrap outside", cw.in_window(720, 1320, 360) is False)
check("evaluate disabled -> always", cw.evaluate(1000, start="09:00", end="16:00", enabled=False)["allow_start"] is True)
check("evaluate in-window start ok", cw.evaluate(600, start="09:00", end="16:00")["allow_start"] is True)
check("evaluate outside no start", cw.evaluate(1000, start="09:00", end="16:00")["allow_start"] is False)
check("evaluate overrun continues", cw.evaluate(1000, start="09:00", end="16:00", overrun=True, already_cleaning=True)["allow_continue"] is True)
check("evaluate overrun won't start", cw.evaluate(1000, start="09:00", end="16:00", overrun=True, already_cleaning=True)["allow_start"] is False)

# ---- clean_guards ----
check("presence blocks when home & away-required", cg.presence_blocks(True, True) is True)
check("presence ok when away", cg.presence_blocks(True, False) is False)
check("presence unknown fails safe", cg.presence_blocks(True, None) is True)
check("presence ignored when not away-required", cg.presence_blocks(False, True) is False)
ready, reasons = cg.station_ready(dust_bag="full", clean_water="ok")
check("station not ready on full bag", ready is False and any("bag" in r for r in reasons))
check("station ready when nothing bad", cg.station_ready(dust_bag=None)[0] is True)
check("station ready ignores 'installed'", cg.station_ready(dust_bag="installed", clean_water="installed")[0] is True)
check("battery ok fail-open on unknown", cg.battery_ok(None, 20) is True)
check("battery below min blocks", cg.battery_ok(10, 20) is False)
check("battery at/above min ok", cg.battery_ok(55, 20) is True)
check("room reachable: no sensor -> True", cg.room_reachable(None) is True)
check("room reachable: 'on'(open) -> True", cg.room_reachable("on") is True)
check("room unreachable: 'off'(closed) -> False", cg.room_reachable("off") is False)
check("room reachable: unavailable fails open", cg.room_reachable("unavailable") is True)

# ---- scheduler ----
rooms = {
    "1": {"enabled": True, "days": [0, 2, 4]},
    "2": {"enabled": True, "days": [0]},
    "3": {"enabled": True, "days": []},
    "4": {"enabled": False, "days": [0]},
}
check("due Monday = [1,2]", sc.rooms_due_today(rooms, 0) == ["1", "2"])
check("due Wednesday = [1]", sc.rooms_due_today(rooms, 2) == ["1"])
check("due Sunday = []", sc.rooms_due_today(rooms, 6) == [])
check("pending excludes cleaned+disabled", sc.pending_rooms(rooms, {"1"}) == ["2", "3"])
check("all enabled segs sorted", sc.all_enabled_segments(rooms) == ["1", "2", "3"])
check("week_start Monday anchor", sc.week_start_for(date(2026, 7, 8), 0) == date(2026, 7, 6))
check("rollover true on new week", sc.needs_week_rollover(date(2026, 7, 13), "2026-07-06", 0) is True)
check("rollover false same week", sc.needs_week_rollover(date(2026, 7, 9), "2026-07-06", 0) is False)

d = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=610, rooms=rooms, cleaned=set(),
                       daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                       catchup_time_min=600, catchup_dispatched_on=None)
check("dispatch daily fires Mon 10:10", d.action == "dispatch" and d.kind == "daily" and d.segments == ["1", "2"])
d2 = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=590, rooms=rooms, cleaned=set(),
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("no dispatch before daily_time", d2.action == "idle")
d3 = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=610, rooms=rooms, cleaned=set(),
                        daily_time_min=600, day_dispatched_on="2026-07-06", catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("no second daily dispatch same day", d3.action == "idle")
d4 = sc.choose_dispatch(now_date=date(2026, 7, 11), weekday=5, now_min=610, rooms=rooms, cleaned={"1": "x"},
                        daily_time_min=None, day_dispatched_on="2026-07-11", catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("catch-up fires Sat with pending [2,3]", d4.action == "dispatch" and d4.kind == "catchup" and d4.segments == ["2", "3"])

# ---- history_analytics ----
_cl = lambda: {"status": "cleaned"}
_sk = lambda why: {"status": "skipped", "reason": why}
H = [  # chronological, oldest -> newest
    {"weekday": 5, "per_room": {"6": _cl()}},
    {"weekday": 1, "per_room": {"6": _sk("door"), "13": _cl()}},
    {"weekday": 3, "per_room": {"6": _sk("door")}},
    {"weekday": 5, "per_room": {"6": _cl(), "13": _cl()}},
    {"weekday": 1, "per_room": {"6": _sk("passage too low")}},
    {"weekday": 3, "per_room": {"6": _sk("passage too low")}},
]
check("consecutive_fails counts recent misses", ha.consecutive_fails(H, "6") == 2)
check("consecutive_fails 0 when latest cleaned", ha.consecutive_fails(H, "13") == 0)
check("consecutive_fails 0 for unknown room", ha.consecutive_fails(H, "99") == 0)
check("last_fail_reason = latest miss reason", ha.last_fail_reason(H, "6") == "passage too low")
check("last_fail_reason None when latest cleaned", ha.last_fail_reason(H, "13") is None)
_ws = ha.weekday_stats(H, "6")
check("weekday_stats Tue = 2 att / 0 clean", _ws[1]["attempts"] == 2 and _ws[1]["cleaned"] == 0)
check("weekday_stats Sat = 2 att / 2 clean", _ws[5]["attempts"] == 2 and _ws[5]["cleaned"] == 2)
check("suggest_better_day Tue/Thu -> Sat(5)", ha.suggest_better_day(H, "6", [1, 3]) == 5)
check("suggest_better_day none w/ too little data", ha.suggest_better_day(H, "13", [5]) is None)
check("suggest_better_day none when already on good day",
      ha.suggest_better_day(H, "6", [5]) is None)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
