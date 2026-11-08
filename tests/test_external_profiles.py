"""Run with python tests/test_external_profiles.py, without Home Assistant."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler"))
import external_history as eh
from scheduler import weekday_deep_clean


class ExternalHistoryTest(unittest.TestCase):
    def setUp(self):
        self.start = int(datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp())
        self.raw = {"timestamp": self.start, "status": "Cleaning", "completed": True,
                    "cleaned_area": "32 m²", "cleaning_time": "30 min", "mop_pad": "Not installed",
                    "multiple_cleaning_time": self.start + 1800}
        self.state = {"history": [], "week_start": "2026-10-26", "maps": {"default": {
            "cleaned": {}, "unreachable": {"1": 3, "2": 2}, "manual_clean": {"1": {}, "2": {}}}}}

    def ingest(self, records):
        for key, start, end, raw in eh.completed_passes(records, self.start, self.start + 86400):
            ids = eh.credit(raw, {"1", "2"}, {"1", "2"}, None, 2)
            eh.apply_session(self.state, key, [{"start": start, "end": end, "rooms": ids,
                "area": raw["cleaned_area"], "pad": raw["mop_pad"], "map_verified": False}], {}, timezone.utc)

    def test_late_mop_after_restart(self):
        sweep = dict(self.raw, blocked_rooms={"2": "Blocked by door"})
        self.ingest({"first": sweep})
        self.state = deepcopy(self.state)
        records = {"second": dict(self.raw, timestamp=self.start+2000, mop_pad="Installed"), "first": sweep}
        self.ingest(records)
        self.assertEqual(len(self.state["history"]), 1)
        self.assertEqual(len(self.state["history"][0]["passes"]), 2)
        slot = self.state["maps"]["default"]
        self.assertEqual(set(slot["cleaned"]), {"1"})
        self.assertEqual(slot["unreachable"], {"2": 2})
        before = deepcopy(self.state)
        self.ingest(records)
        self.assertEqual(self.state, before)

    def test_interruption_and_uncertain_scope(self):
        self.ingest({"aborted": dict(self.raw, completed=False), "error": dict(self.raw, interrupt_reason="Docking")})
        self.assertEqual(self.state["history"], [])
        for scope in ("Zone cleaning", "Room cleaning"):
            self.assertEqual(eh.credit(dict(self.raw,status=scope), {"1"}, {"1"}, None, 2), [])
        self.assertEqual(eh.credit(dict(self.raw,status="Room cleaning"), {"1","2"}, {"1","2"},
                                   {"targeted":[1,2], "cleaned":[2]}, 2), ["2"])
        self.assertEqual(eh.credit(dict(self.raw,cleaned_area="1 m²"), {"1","2"}, {"1","2"}, None, 2), [])
        self.assertTrue(eh.owned_by_scheduler(self.start, [(self.start+0.5, self.start+120)]))
        self.assertFalse(eh.owned_by_scheduler(self.start+121, [(self.start,self.start+120)]))

    def test_late_previous_week_history(self):
        self.state["week_start"]="2026-11-02"
        self.state["last_clean"]="2026-11-03T10:00:00+00:00"
        self.ingest({"previous week":self.raw})
        self.assertEqual(len(self.state["history"]), 1)
        self.assertEqual(self.state["maps"]["default"]["cleaned"], {})
        self.assertEqual(self.state["last_clean"],"2026-11-03T10:00:00+00:00")


class WeekdayProfileTest(unittest.TestCase):
    def test_profile_precedence(self):
        options = dict(protected=False, quiet=False, sweep_only=False, sequential_available=True, room_cadences=[1,1])
        profiles = {"5":"deep", "6":"rooms"}
        self.assertTrue(weekday_deep_clean(profiles["5"], False, **options))
        self.assertFalse(weekday_deep_clean(profiles["6"], True, **options))
        self.assertTrue(weekday_deep_clean(profiles.get("1"), True, **options))
        for override in [dict(protected=True), dict(quiet=True), dict(sweep_only=True),
                         dict(sequential_available=False), dict(room_cadences=[1,0]),dict(room_cadences=[2])]:
            self.assertFalse(weekday_deep_clean("deep", False, **(options | override)))


if __name__ == "__main__":
    unittest.main()
