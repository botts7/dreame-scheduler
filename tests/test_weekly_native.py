"""Exercise the real engine dispatch against an in-memory robot. No network or HA needed."""
import asyncio
import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler"
NOW = datetime(2026, 1, 10, 18, tzinfo=timezone.utc)


def load_engine():
    package = ModuleType("scheduler_fixture")
    package.__path__ = [str(ROOT)]
    stubs = {package.__name__: package}
    for name, fields in {
        "homeassistant": {}, "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"callback": lambda fn: fn, "Event": object, "HomeAssistant": object},
        "homeassistant.helpers": {},
        "homeassistant.helpers.event": {"async_track_state_change_event": None, "async_track_time_interval": None},
        "homeassistant.helpers.storage": {"Store": object},
        "homeassistant.util": {"dt": SimpleNamespace(now=lambda: NOW)},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(fields)
        stubs[name] = module
    with patch.dict(sys.modules, stubs):
        return importlib.import_module(f"{package.__name__}.engine")


engine = load_engine()
plans = engine.weekday_plans


class NativeDispatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {}
        self.calls = []
        self.tracker = SimpleNamespace(state={}, enabled=True, active_run=None, edge_run=None,
                                       edge_restore=None, async_save=AsyncMock())
        opts = {"weekday_profiles": {str(i): k for i,k in enumerate(list(plans.MODES) + list(plans.GENIUS)[:3])},
                "presence_entities": ["person.a", "person.b"], plans.DEPARTURE: True,
                "away_grace_min": 5, plans.GUARDS: {"binary_sensor.permission": "on"}}
        hass = SimpleNamespace(states=self.states, services=SimpleNamespace(async_call=self.call))
        entry = SimpleNamespace(options=opts, data={"vacuum_entity": "vacuum.robot", "prefix": "robot"})
        self.e = engine.SchedulerEngine(hass, entry, self.tracker)
        for ent, state in {"vacuum.robot":"docked", "person.a":"not_home", "person.b":"not_home",
                           "binary_sensor.permission":"on", "select.robot_cleaning_route":"deep",
                           "select.robot_cleangenius":"off", "select.robot_cleangenius_mode":"vacuum_and_mop"}.items():
            self.states[ent] = SimpleNamespace(state=state, last_changed=NOW-timedelta(minutes=10), attributes={})
        for suffix, options in {"cleaning_route":["standard","deep"],
                                "cleangenius":["off", "deep_cleaning", "routine_cleaning"],
                                "cleangenius_mode":["vacuum_and_mop", "mop_after_vacuum"]}.items():
            self.states["select.robot_"+suffix].attributes["options"] = options

    async def call(self, domain, service, data, blocking):
        self.assertTrue(blocking)
        self.calls.append((service, data.copy()))
        if domain == "select":
            self.states[data["entity_id"]].state = data["option"]
        elif domain == "vacuum":
            self.assertTrue(self.tracker.state["native_dispatches"])  # already durable

    async def test_seven_independent_choices(self):
        before = [self.e._weekday_profile(i) for i in range(7)]
        self.assertEqual(len(set(before)), 7)
        self.e.entry.options["weekday_profiles"]["5"] = "genius_deep_sequential"
        after = [self.e._weekday_profile(i) for i in range(7)]
        self.assertEqual([i for i in range(7) if before[i] != after[i]], [5])
        for key, mode in plans.MODES.items():
            self.assertEqual(plans.settings(key, lambda k: True),
                             [("select.cleangenius","off"), ("switch.customized_cleaning","off"),
                              ("select.cleaning_mode",mode)])

    async def test_native_start_restore_and_restart(self):
        profile = "genius_deep_sequential"
        await self.e._start_native_profile(profile, NOW)
        self.assertEqual([data.get("option") for _,data in self.calls],
                         ["standard","deep_cleaning","mop_after_vacuum",None])
        self.assertEqual(self.calls[-1], ("start", {"entity_id":"vacuum.robot"}))
        self.assertTrue(await self.e._settle_native_task(NOW+timedelta(hours=2)))
        self.assertFalse(await self.e._settle_native_task(NOW+timedelta(hours=2,minutes=16)))
        self.assertEqual(self.states["select.robot_cleangenius"].state, "off")
        restarted = engine.SchedulerEngine(self.e.hass, self.e.entry, self.tracker)
        await restarted._start_native_profile(profile, NOW+timedelta(hours=3))
        self.assertEqual(sum(service=="start" for service,_ in self.calls), 1)

    async def test_gate_and_refused_mode(self):
        profile = "genius_deep_sequential"
        for ent, new in [("binary_sensor.permission","off"), ("person.b","unknown"), ("vacuum.robot","cleaning")]:
            old = self.states[ent].state
            self.states[ent].state = new
            await self.e._start_native_profile(profile, NOW)
            self.assertFalse(self.calls)
            self.states[ent].state = old
        self.states["person.a"].last_changed = NOW-timedelta(minutes=2)
        await self.e._start_native_profile(profile, NOW)
        self.assertFalse(self.calls)
        self.states["person.a"].last_changed = NOW-timedelta(minutes=8)
        self.states["select.robot_cleangenius_mode"].attributes["options"] = ["vacuum_and_mop"]
        with patch.object(asyncio, "sleep", new=AsyncMock()):
            await self.e._start_native_profile(profile, NOW)
        self.assertFalse(any(service=="start" for service,_ in self.calls))
        self.assertNotIn("native_dispatches", self.tracker.state)
        self.assertFalse(await self.e._settle_native_task(NOW))
        self.assertEqual(self.states["select.robot_cleaning_route"].state, "deep")

    def test_credit_requires_full_map_start(self):
        rows = [{"start": 1000.1, "profile":"home_mop"}]
        self.assertEqual(plans.scheduled_profile([{"scope":"Cleaning","start":1000}], rows), "home_mop")
        self.assertIsNone(plans.scheduled_profile([{"scope":"Room cleaning","start":1000}], rows))
        self.assertIsNone(plans.scheduled_profile([{"scope":"Cleaning","start":1400}], rows))


if __name__ == "__main__":
    unittest.main()
