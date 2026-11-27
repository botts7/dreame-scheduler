"""Catching up on cleans that happened while HA wasn't watching.

Run: python tests/test_missed_cleans.py   (no Home Assistant install needed)
"""
import importlib.util
import os
import sys
from datetime import timedelta, timezone

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mc = load("missed_cleans")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


# Shaped like the cleaning_history sensor attributes (plus its own metadata).
ATTRS = {
    "11-21 09:00": {"timestamp": 1795125600, "completed": True, "cleaned_area": "15 m²"},
    "11-20 13:00": {"timestamp": 1795039200, "completed": False, "blocked_rooms": {}},
    "11-20 10:00": {"timestamp": 1795028400, "completed": True, "interrupt_reason": "Manually stopped"},
    "11-19 21:34": {"timestamp": 1794952440, "completed": True},
    "device_class": "timestamp",
    "icon": "mdi:clipboard-text-clock",
}
recs = mc.records(ATTRS)

# ---- reading the sensor ----
check("records: metadata keys ignored", len(recs) == 4)
check("records: newest first", [t for t, _ in recs] == [1795125600, 1795039200, 1795028400, 1794952440])
check("index: newest is 1", mc.history_index(recs, 1795125600) == 1)
check("index: oldest is 4", mc.history_index(recs, 1794952440) == 4)
check("index: unknown is None", mc.history_index(recs, 123) is None)

# ---- which records to fetch ----
p = mc.pending(recs, 1794952440)
check("pending: only completed, uninterrupted, newer than the watermark", [t for t, _ in p] == [1795125600])
check("pending: oldest first", [t for t, _ in mc.pending(recs, 0)] == [1794952440, 1795125600])
check("pending: nothing before the watermark is set", mc.pending(recs, None) == [])
check("pending: capped", len(mc.pending([(i, {"completed": True}) for i in range(20, 0, -1)], 0)) == mc.MAX_PER_CATCHUP)
check("creditable: 'Task completed' is a normal finish", mc.creditable({"completed": True, "interrupt_reason": "Task completed"}))
check("creditable: 'Incomplete' is not", not mc.creditable({"completed": True, "interrupt_reason": "Incomplete"}))

# ---- map evidence ----
check("map: matches the record time", mc.map_matches(1795125600.0, 1795125600))
check("map: different record is rejected", not mc.map_matches(1795039200, 1795125600))
check("map: missing time rejected", not mc.map_matches(None, 1795125600))

rec = {"completed": True}
check("credit: only rooms the map shows cleaned, that are scheduled",
      mc.rooms_to_credit(rec, [8, 7, 3], {"8", "7", "1"}) == ["7", "8"])
check("credit: no map evidence, no credit", mc.rooms_to_credit(rec, None, {"8"}) == [])
check("credit: empty cleaned list, no credit", mc.rooms_to_credit(rec, [], {"8"}) == [])
check("credit: blocked room left out",
      mc.rooms_to_credit({"completed": True, "blocked_rooms": {"7": "x"}}, [8, 7], {"8", "7"}) == ["8"])
check("credit: skipped room left out",
      mc.rooms_to_credit({"completed": True, "skipped_rooms": [8]}, [8, 7], {"8", "7"}) == ["7"])
check("credit: interrupted record earns nothing",
      mc.rooms_to_credit({"completed": False}, [8], {"8"}) == [])
check("credit: junk segment ids ignored", mc.rooms_to_credit(rec, ["x", 0, -1, "8"], {"8"}) == ["8"])

# ---- path evidence (live 2026-11-19 data from an L20) ----
test_hits, test_pts = {"7": 130, "8": 652}, 782          # Main Room test; Kitchen Dining = transit
daily_hits, daily_pts = {"2": 910, "4": 805, "7": 2053, "8": 586, "15": 1}, 4355
check("path: Main Room test credits Main Room only (Kitchen Dining was transit)",
      mc.rooms_to_credit(rec, None, {"8", "7"}, asked=[8], path_hits=test_hits, total_points=test_pts) == ["8"])
check("path: asked room never entered (Lounge Room2) is not credited",
      mc.rooms_to_credit(rec, None, {"1", "2", "4", "7", "8"}, asked=[1, 2, 4, 7, 8],
                         path_hits=daily_hits, total_points=daily_pts) == ["2", "4", "7", "8"])
# ---- UrbanErosion's L50 cases (#6, 2026-11-27): whole-home jobs save no asked list ----
whole = {"1": 410, "2": 920, "4": 610, "7": 880, "8": 700, "13": 12}      # 13 = a corridor brushed past
check("whole-home completed, no lists: rooms the path spent time in",
      mc.rooms_to_credit(rec, None, {"1", "2", "4", "7", "8", "13"}, asked=None,
                         path_hits=whole, total_points=3547) == ["1", "2", "4", "7", "8"])
check("whole-home: still only scheduled rooms",
      mc.rooms_to_credit(rec, None, {"2", "8"}, asked=None, path_hits=whole, total_points=3547) == ["2", "8"])
check("whole-home: blocked room left out",
      mc.rooms_to_credit({"completed": True, "blocked_rooms": {"7": "x"}}, None, {"2", "7", "8"},
                         asked=None, path_hits=whole, total_points=3547) == ["2", "8"])
check("whole-home interrupted (16 m2, 739 pts): no credit",
      mc.rooms_to_credit({"completed": False}, None, {"1", "2", "8"}, asked=None,
                         path_hits={"2": 500, "8": 239}, total_points=739) == [])
check("targeted interrupted, 2 blocked of 7: no credit",
      mc.rooms_to_credit({"completed": False, "blocked_rooms": {"3": "x", "6": "y"}}, None,
                         {"1", "2", "3", "4", "6", "7", "8"}, asked=[1, 2, 3, 4, 6, 7, 8],
                         path_hits={"1": 300, "2": 300, "4": 300}, total_points=900) == [])
check("targeted completed, 2 blocked of 7: blocked left out, unvisited left out",
      mc.rooms_to_credit({"completed": True, "blocked_rooms": {"3": "x", "6": "y"}}, None,
                         {"1", "2", "3", "4", "6", "7", "8"}, asked=[1, 2, 3, 4, 6, 7, 8],
                         path_hits={"1": 300, "2": 300, "3": 40, "4": 300}, total_points=940) == ["1", "2", "4"])
check("whole-home with no path either: nothing",
      mc.rooms_to_credit(rec, None, {"8"}, asked=None, path_hits=None, total_points=None) == [])
check("path: a brief touch is not a visit", mc.path_rooms({"15": 1, "8": 652}, 782) == {"8"})
check("path: a tiny room in a long run still counts (Lounge Room2, 2026-11-23)",
      mc.rooms_to_credit(rec, None, {"1", "2", "8"}, asked=[1, 2, 8],
                         path_hits={"1": 35, "2": 1371, "8": 525, "15": 3}, total_points=1934) == ["1", "2", "8"])
check("path: just under the floor is not a visit", mc.path_rooms({"1": 19}) == set())
check("path: map's cleaned list wins over the path",
      mc.rooms_to_credit(rec, [7], {"7", "8"}, asked=[8], path_hits=test_hits, total_points=test_pts) == ["7"])
check("path: interrupted record still earns nothing",
      mc.rooms_to_credit({"completed": False}, None, {"8"}, asked=[8], path_hits=test_hits, total_points=test_pts) == [])

# ---- a scheduler run's own sessions ----
run_recs = [(1795057979, {}), (1795042848, {}), (1794956936, {})]   # 14:12, 10:00 (this run), and the day before
mine = mc.run_records(run_recs, base_ts=1794956936, started_ts=1795042851)
check("run records: both sessions of a split run, not the earlier clean", [t for t, _ in mine] == [1795057979, 1795042848])
check("run records: nothing before the run started", mc.run_records(run_recs, base_ts=None, started_ts=1795057000) == [(1795057979, {})])
# 19 Nov 10:00 run: two sessions, neither path entered Lounge Room2 (seg 1)
s1 = mc.path_rooms({"2": 910, "4": 805, "7": 2053, "8": 586, "15": 1}, 4355)
s2 = mc.path_rooms({"2": 1517, "15": 3}, 1520)
check("split run: union of both sessions' paths", s1 | s2 == {"2", "4", "7", "8"})
check("split run: Lounge Room2 not among them", "1" not in (s1 | s2))

# ---- timing ----
aest = timezone(timedelta(hours=11))
check("time: credited at the record's start, local", mc.record_time(1795212000, aest).startswith("2026-11-21T09:00:00"))

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
