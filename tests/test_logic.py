"""Behavioural tests for the HA-import-free scheduler logic.

Run: python tests/test_logic.py   (no Home Assistant install needed)
"""
import importlib.util
import os
import sys
from datetime import date, datetime, timedelta, timezone

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
co = load("consumables")
tl = load("trap_learner")

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

# ---- multi-day rooms must clean on EACH ticked day, not once per week ----
# A room ticked Mon+Thu, cleaned on its Monday slot, is due again Thursday.
md = {"1": {"enabled": True, "days": [0, 3]}}          # Mon + Thu
d5 = sc.choose_dispatch(now_date=date(2026, 7, 23), weekday=3, now_min=610, rooms=md,
                        cleaned={"1": "2026-07-20T10:30:00+10:00"},  # cleaned Monday
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("Mon+Thu room re-cleans Thu (Mon-cleaned)", d5.action == "dispatch" and d5.segments == ["1"])
# ...but not twice on the same day.
d6 = sc.choose_dispatch(now_date=date(2026, 7, 23), weekday=3, now_min=610, rooms=md,
                        cleaned={"1": "2026-07-23T08:00:00+10:00"},  # already cleaned today
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("no second clean same day (cleaned today)", d6.action == "idle")
# An every-day room cleans every day (Tue, having last cleaned Mon).
ed = {"1": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6]}}
d7 = sc.choose_dispatch(now_date=date(2026, 7, 21), weekday=1, now_min=610, rooms=ed,
                        cleaned={"1": "2026-07-20T10:30:00+10:00"},  # cleaned yesterday (Mon)
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None)
check("every-day room cleans again next day", d7.action == "dispatch" and d7.segments == ["1"])

# ---- per-room explicit times (multiple cleans per day) ----
kt = {"4": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6],
            "times": [{"at": "13:00", "mop": False}, {"at": "08:00"}]}}
rt = sc.room_times(kt["4"])
check("room_times sorts + defaults mop True", rt == [(480, "08:00", True), (780, "13:00", False)])
check("room_times empty when absent", sc.room_times({"enabled": True}) == [])
check("room_times skips malformed", sc.room_times({"times": [{"at": "9999"}, {"nope": 1}, {"at": "07:15"}]}) == [(435, "07:15", True)])
check("has_explicit_times true", sc.has_explicit_times(kt["4"]) is True)
check("has_explicit_times false", sc.has_explicit_times({"days": [0]}) is False)
# due_slots: at 13:20 both slots are due; at 08:20 only the 08:00 one.
ds_all = sc.due_slots(kt, weekday=0, now_min=800, slots_done=set())
check("due_slots both due at 13:20", ds_all == [("4", "4@08:00", True), ("4", "4@13:00", False)])
ds_early = sc.due_slots(kt, weekday=0, now_min=500, slots_done=set())
check("due_slots only 08:00 due at 08:20", ds_early == [("4", "4@08:00", True)])
ds_after = sc.due_slots(kt, weekday=0, now_min=800, slots_done={"4@08:00"})
check("due_slots skips a fired slot", ds_after == [("4", "4@13:00", False)])
check("due_slots respects days (not scheduled)", sc.due_slots({"4": {"enabled": True, "days": [2], "times": [{"at": "08:00"}]}}, weekday=0, now_min=800, slots_done=set()) == [])
check("next_slot_minute first of day", sc.next_slot_minute(kt, weekday=0) == 480)
check("next_slot_minute after 08:00 -> 13:00", sc.next_slot_minute(kt, weekday=0, after_min=480) == 780)
# choose_dispatch: the 13:00 sweep-only slot fires as a "slot" dispatch.
sd = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=780, rooms=kt, cleaned={"4": "2026-07-06T08:05:00+10:00"},
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=False, catchup_day=5,
                        catchup_time_min=None, catchup_dispatched_on=None, slots_done={"4@08:00"})
check("slot dispatch fires despite cleaned-today", sd.action == "dispatch" and sd.kind == "slot" and sd.segments == ["4"])
check("slot dispatch marks sweep-only + slot key", sd.sweep_only == ["4"] and sd.slot_keys == ["4@13:00"])
# A slotted room is excluded from the global daily fire (it runs on its own times).
sd2 = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=610, rooms=kt, cleaned=set(),
                         daily_time_min=600, day_dispatched_on=None, catchup_enabled=False, catchup_day=5,
                         catchup_time_min=None, catchup_dispatched_on=None, slots_done={"4@08:00"})
check("slotted room not in daily fire", sd2.kind != "daily" or "4" not in sd2.segments)
# Same room, two slots pending on one tick -> one dispatch, mop wins, both keys marked.
kt2 = {"4": {"enabled": True, "days": [0], "times": [{"at": "08:00", "mop": True}, {"at": "13:00", "mop": False}]}}
sd3 = sc.choose_dispatch(now_date=date(2026, 7, 6), weekday=0, now_min=800, rooms=kt2, cleaned=set(),
                         daily_time_min=None, day_dispatched_on=None, catchup_enabled=False, catchup_day=5,
                         catchup_time_min=None, catchup_dispatched_on=None, slots_done=set())
check("two pending slots -> single seg, mop wins", sd3.segments == ["4"] and sd3.sweep_only == [] and sorted(sd3.slot_keys) == ["4@08:00", "4@13:00"])

# ---- opportunistic (rolling) catch-up ----
# Rooms scheduled Mon only; "today" is Wed (weekday 2), catch-up day is Sat (5).
oc = {"1": {"enabled": True, "days": [0]}, "2": {"enabled": True, "days": [0]}}
oc_kw = dict(now_date=date(2026, 7, 8), weekday=2, now_min=610, rooms=oc, cleaned=set(),
             daily_time_min=None, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
             catchup_time_min=600, catchup_dispatched_on=None)
o1 = sc.choose_dispatch(**oc_kw)
check("opportunistic OFF: no catch-up off-day", o1.action == "idle")
o2 = sc.choose_dispatch(**{**oc_kw, "opportunistic_catchup": True})
check("opportunistic ON: catch-up any empty day", o2.action == "dispatch" and o2.kind == "catchup" and o2.segments == ["1", "2"])
o3 = sc.choose_dispatch(**{**oc_kw, "opportunistic_catchup": True, "now_min": 590})
check("opportunistic ON: waits for catch-up time", o3.action == "idle")
o4 = sc.choose_dispatch(**{**oc_kw, "opportunistic_catchup": True, "cleaned": {"1": "x", "2": "x"}})
check("opportunistic ON: nothing pending -> idle", o4.action == "idle" and o4.reason == "week_already_complete")
o5 = sc.choose_dispatch(**{**oc_kw, "opportunistic_catchup": True, "catchup_dispatched_on": "2026-07-08"})
check("opportunistic ON: once per day", o5.action == "idle")
# Daily still wins over opportunistic catch-up on the same tick.
oc_daily = {"1": {"enabled": True, "days": [2]}}   # scheduled Wed
o6 = sc.choose_dispatch(now_date=date(2026, 7, 8), weekday=2, now_min=610, rooms=oc_daily, cleaned=set(),
                        daily_time_min=600, day_dispatched_on=None, catchup_enabled=True, catchup_day=5,
                        catchup_time_min=600, catchup_dispatched_on=None, opportunistic_catchup=True)
check("daily wins over opportunistic on same tick", o6.action == "dispatch" and o6.kind == "daily")

# ---- holiday / extended-away pause ----
check("holiday off -> no hold", sc.holiday_hold(False, 10, 3, True) is False)
check("holiday: away >= N days + clean -> hold", sc.holiday_hold(True, 3, 3, True) is True)
check("holiday: away < N days -> no hold", sc.holiday_hold(True, 2, 3, True) is False)
check("holiday: house not clean -> no hold (first clean still runs)", sc.holiday_hold(True, 5, 3, False) is False)
check("holiday: presence unknown (away_days None) -> no hold", sc.holiday_hold(True, None, 3, True) is False)

# ---- stale presence-tracker watchdog ----
check("stale off (0) -> never stale", sc.tracker_stale(999999, 0) is False)
check("stale: age over limit -> stale", sc.tracker_stale(31 * 60, 30) is True)
check("stale: age under limit -> fresh", sc.tracker_stale(29 * 60, 30) is False)
check("stale: unknown age (None) -> not stale", sc.tracker_stale(None, 30) is False)
check("stale: bad limit -> not stale", sc.tracker_stale(9999, None) is False)

# ---- mop-every-N cadence (per-room) ----
check("is_mopping_mode: sweeping is False", sc.is_mopping_mode("sweeping") is False)
check("is_mopping_mode: mopping_after_sweeping True", sc.is_mopping_mode("mopping_after_sweeping") is True)
check("is_mopping_mode: sweeping_and_mopping True", sc.is_mopping_mode("sweeping_and_mopping") is True)
check("is_mopping_mode: None False", sc.is_mopping_mode(None) is False)
# N=2, base mops: day 1 sweeps only, day 2 mops (Anton's example).
c1, m1, w1 = sc.advance_mop_cadence("mopping_after_sweeping", 2, None, None, "2026-07-20")
check("cadence N=2 day1 -> sweep only", (c1, m1, w1) == (1, "sweeping", False))
c2, m2, w2 = sc.advance_mop_cadence("mopping_after_sweeping", 2, 1, "2026-07-20", "2026-07-21")
check("cadence N=2 day2 -> mop", (c2, m2, w2) == (2, "mopping_after_sweeping", True))
c3, m3, w3 = sc.advance_mop_cadence("mopping_after_sweeping", 2, 2, "2026-07-21", "2026-07-22")
check("cadence N=2 day3 -> sweep only", (c3, m3, w3) == (3, "sweeping", False))
# Same calendar day (resume / manual) must NOT advance the counter.
c4, m4, w4 = sc.advance_mop_cadence("mopping_after_sweeping", 2, 1, "2026-07-20", "2026-07-20")
check("cadence same-day resume keeps count", (c4, m4, w4) == (1, "sweeping", False))
# N=1 (off) leaves the base mode untouched.
c5, m5, w5 = sc.advance_mop_cadence("sweeping_and_mopping", 1, None, None, "2026-07-20")
check("cadence N=1 -> base mode, mops", (m5, w5) == ("sweeping_and_mopping", True))
# Non-mopping base is returned verbatim regardless of N.
c6, m6, w6 = sc.advance_mop_cadence("sweeping", 3, None, None, "2026-07-20")
check("cadence non-mop base stays sweeping", (m6, w6) == ("sweeping", False))
# N=0 -> "never mop": always sweep-only, whatever the base mode (all-rug room).
c7, m7, w7 = sc.advance_mop_cadence("mopping_after_sweeping", 0, None, None, "2026-07-20")
check("cadence N=0 mop base -> never mops", (m7, w7) == ("sweeping", False))
c8, m8, w8 = sc.advance_mop_cadence("sweeping_and_mopping", 0, 5, "2026-07-19", "2026-07-20")
check("cadence N=0 stays sweep across days", (m8, w8) == ("sweeping", False))
# Every-3rd-day cadence lands the mop on day 3.
seq = []
pc, pd = None, None
for i, day in enumerate(["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]):
    pc, mode, will = sc.advance_mop_cadence("mopping", 3, pc, pd, day)
    pd = day
    seq.append(will)
check("cadence N=3 mops on day 3 only", seq == [False, False, True, False])

# ---- door_open_long_enough (retry-when-door-reopens) ----
_now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
check("door open 40min >= 30 -> retry",
      sc.door_open_long_enough("on", _now - timedelta(minutes=40), _now, 30) is True)
check("door open 20min < 30 -> no",
      sc.door_open_long_enough("on", _now - timedelta(minutes=20), _now, 30) is False)
check("door open exactly 30 -> retry",
      sc.door_open_long_enough("open", _now - timedelta(minutes=30), _now, 30) is True)
check("door closed -> no",
      sc.door_open_long_enough("off", _now - timedelta(minutes=40), _now, 30) is False)
check("door open but no timestamp -> no",
      sc.door_open_long_enough("open", None, _now, 30) is False)
check("door unknown state -> no",
      sc.door_open_long_enough("unavailable", _now - timedelta(minutes=40), _now, 30) is False)

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

# --- evaluate_progress ("can't get home" watchdog productivity) --------------
MM = 80  # STUCK_MIN_ESCAPE_MM

# First call seeds the tracker and is always "productive".
r0, p0 = sc.evaluate_progress(None, "t0", prog=0.0, area=0.0, dist=200.0, min_escape_mm=MM)
check("evaluate_progress seeds tracker productive", p0 is True and r0["anchor_dist"] == 200.0)

# Slow-but-real cleaning: area flat, but progress % climbs -> productive (the bug we fixed).
r1, p1 = sc.evaluate_progress(dict(r0), "t1", prog=1.0, area=0.0, dist=3000.0, min_escape_mm=MM)
check("evaluate_progress progress%% climb = productive", p1 is True)

# Totally flat mid-clean (prog, area unchanged; moving AWAY from dock) -> NOT productive.
seed = {"since": "t0", "anchor_dist": 3000.0, "best_area": 5.0, "best_prog": 20.0, "notified": False}
r2, p2 = sc.evaluate_progress(dict(seed), "t2", prog=20.0, area=5.0, dist=3200.0, min_escape_mm=MM)
check("evaluate_progress flat + moving away = not productive", p2 is False)

# Netting closer to the dock (return home) -> productive, and the anchor re-sets.
r3, p3 = sc.evaluate_progress(dict(seed), "t3", prog=20.0, area=5.0, dist=2800.0, min_escape_mm=MM)
check("evaluate_progress closer to dock = productive", p3 is True and r3["anchor_dist"] == 2800.0)

# A sub-threshold nudge toward the dock (< min_escape_mm) is NOT productive.
r4, p4 = sc.evaluate_progress(dict(seed), "t4", prog=20.0, area=5.0, dist=2950.0, min_escape_mm=MM)
check("evaluate_progress tiny nudge (<80mm) = not productive", p4 is False)

# Missing readings (None) never count as progress on their own.
r5, p5 = sc.evaluate_progress(dict(seed), "t5", prog=None, area=None, dist=None, min_escape_mm=MM)
check("evaluate_progress all-None = not productive", p5 is False)

# --- evaluate_consumables (maintenance-part alerts) --------------------------
# Filter low (<=10), rest fine -> only filter is due, and it gets flagged.
due, na = co.evaluate_consumables(
    {"filter": 4, "main_brush": 52, "side_brush": 28}, 10, {})
check("consumables filter<=thr -> due", [c["key"] for c in due] == ["filter"])
check("consumables due sets alerted flag", na.get("filter") is True)
# Already flagged -> not due again (alert once, no nagging).
due2, _ = co.evaluate_consumables({"filter": 4}, 10, {"filter": True})
check("consumables already-alerted -> not due", due2 == [])
# Back above threshold (replaced / counter reset) -> flag cleared.
_, na3 = co.evaluate_consumables({"filter": 100}, 10, {"filter": True})
check("consumables replaced -> flag cleared", "filter" not in na3)
# None reading (sensor unavailable) -> ignored, no due, flag untouched.
due4, na4 = co.evaluate_consumables({"filter": None}, 10, {"filter": True})
check("consumables None reading ignored", due4 == [] and na4.get("filter") is True)
# Exactly at threshold counts as due; two low at once both fire.
due5, _ = co.evaluate_consumables({"filter": 10, "side_brush": 3}, 10, {})
check("consumables at-threshold + multi due",
      sorted(c["key"] for c in due5) == ["filter", "side_brush"])

# --- edge clean: strip geometry + schedule due ------------------------------
_ez = sc.edge_zones_for_box(0, 0, 2000, 1600, 250)
check("edge_zones returns 4 strips", len(_ez) == 4)
check("edge_zones left strip hugs x0 wall", _ez[0] == [0, 0, 250, 1600])
check("edge_zones right strip hugs x1 wall", _ez[1] == [1750, 0, 2000, 1600])
check("edge_zones bottom strip hugs y0 wall", _ez[2] == [0, 0, 2000, 250])
check("edge_zones top strip hugs y1 wall", _ez[3] == [0, 1350, 2000, 1600])
# narrow room: strip width capped at half the smaller side (300/2=150), reversed coords ok
_ezn = sc.edge_zones_for_box(1000, 500, 700, 800, 250)
check("edge_zones narrow room caps width + normalises box",
      _ezn[0] == [700, 500, 850, 800] and _ezn[1] == [850, 500, 1000, 800])

from datetime import date as _d
check("edge_due never-run -> due", sc.edge_due(None, 3, _d(2026, 8, 15)) is True)
check("edge_due 3 days ago, every 3 -> due", sc.edge_due("2026-08-12", 3, _d(2026, 8, 15)) is True)
check("edge_due 2 days ago, every 3 -> not due", sc.edge_due("2026-08-13", 3, _d(2026, 8, 15)) is False)
check("edge_due every 0 -> disabled", sc.edge_due(None, 0, _d(2026, 8, 15)) is False)

# ---- fold_room_area (per-room learning) ----
check("fold seeds on first sample", sc.fold_room_area(None, 8.0, 2.0, 40.0) == {"area": 8.0, "n": 1})
check("fold EMAs toward new sample",
      sc.fold_room_area({"area": 8.0, "n": 1}, 6.0, 2.0, 40.0) == {"area": round(0.7 * 8 + 0.3 * 6, 1), "n": 2})
check("fold clamps high outlier to hi", sc.fold_room_area(None, 999.0, 2.0, 40.0) == {"area": 40.0, "n": 1})
check("fold clamps low outlier to lo", sc.fold_room_area(None, 2.0, 5.0, 40.0)["area"] == 5.0)
check("fold ignores None sample", sc.fold_room_area({"area": 8.0, "n": 3}, None, 2.0, 40.0) == {"area": 8.0, "n": 3})
check("fold ignores tiny sample (min_sample)", sc.fold_room_area({"area": 8.0, "n": 3}, 0.4, 2.0, 40.0) == {"area": 8.0, "n": 3})
check("fold ignores bad bounds (lo>hi)", sc.fold_room_area(None, 8.0, 40.0, 2.0) is None)
check("fold treats n=0 as unseeded", sc.fold_room_area({"area": 0.0, "n": 0}, 8.0, 2.0, 40.0) == {"area": 8.0, "n": 1})

# ---- trap_learner: door-aware no-go + box clamp ----
def _wedge(room, x, y, run, door=False):
    return {"room": room, "x": x, "y": y, "error": "forward_suffocate",
            "beached": True, "run_id": run, "door_closed": door}

# 3 runs of physical wedges at the same spot, NO door → a real trap → no-go suggested.
trap = [_wedge("Bedroom", 1000, 1000, f"r{i}") for i in range(3)]
out = tl.analyze(trap)
check("trap_learner: recurring physical wedge -> no-go", len(out["nogo_suggestions"]) == 1)
check("trap_learner: real trap not a door_block", len(out.get("door_blocks", [])) == 0)

# Same recurrence but every wedge happened with a shut door → doorway → NO no-go,
# surfaced as a door_block instead (the Passage/Study-door case).
door = [_wedge("Passage", 5000, 2000, f"r{i}", door=True) for i in range(3)]
out2 = tl.analyze(door)
check("trap_learner: shut-door wedges -> NO no-go", len(out2["nogo_suggestions"]) == 0)
check("trap_learner: shut-door wedges -> door_block", len(out2["door_blocks"]) == 1)

# Mostly route-blocked with only a few incidental beaches among many visits — a
# doorway with no sensor (the real Passage case) → path_block, NOT a no-go.
mixed = []
for i in range(40):
    e = _wedge("Passage", 3000, 8800, f"route{i}"); e["error"] = "route"; e["beached"] = False
    mixed.append(e)
mixed += [_wedge("Passage", 3000, 8800, f"beach{i}") for i in range(3)]   # 3/43 physical = 7%
outm = tl.analyze(mixed)
check("trap_learner: low physical fraction -> NO no-go", len(outm["nogo_suggestions"]) == 0)
check("trap_learner: low physical fraction -> path_block", any(p["room"] == "Passage" for p in outm["path_blocks"]))

# Box clamp: a wedge at a room's edge, box clamped to the room rect can't exceed it.
edge = [_wedge("Passage", 5900, 2000, f"r{i}") for i in range(3)]
rb = {"Passage": [1000, 1000, 6000, 3000]}   # Passage ends at x=6000; Study is beyond
outc = tl.analyze(edge, room_boxes=rb)
box = outc["nogo_suggestions"][0]["box"]
check("trap_learner: no-go box clamped within room (x1<=6000)", box[2] <= 6000)
check("trap_learner: no-go box stays inside room bounds", box[0] >= 1000 and box[1] >= 1000 and box[3] <= 3000)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
