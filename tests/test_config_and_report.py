"""Tests for config_bridge select option filtering and report._schedule defaults."""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import datetime, timezone

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def _ensure_stubs():
    def _stub(name, **attrs):
        if name in sys.modules:
            mod = sys.modules[name]
        else:
            mod = types.ModuleType(name)
            mod.__path__ = []
            sys.modules[name] = mod
        for k, v in attrs.items():
            if not hasattr(mod, k):
                setattr(mod, k, v)
        return mod

    _stub("voluptuous", Schema=lambda x: x, Optional=lambda k, **kw: k, Required=lambda k, **kw: k,
          All=lambda *a: a, Match=lambda *a: a, Coerce=lambda x: x, Range=lambda **kw: kw,
          In=lambda x: x, Invalid=Exception)
    _stub("homeassistant")
    _stub("homeassistant.config_entries", ConfigEntry=object)
    _stub("homeassistant.core", HomeAssistant=object, ServiceCall=object,
          SupportsResponse=types.SimpleNamespace(ONLY="only"))
    _stub("homeassistant.exceptions", HomeAssistantError=Exception)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.entity_registry", async_get=lambda h: None,
          async_entries_for_config_entry=lambda r, e: [])
    _stub("homeassistant.util")
    _stub("homeassistant.util.dt", now=lambda: datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc))

    if "dreame_scheduler" not in sys.modules:
        pkg = types.ModuleType("dreame_scheduler")
        pkg.__path__ = [os.path.abspath(BASE)]
        sys.modules["dreame_scheduler"] = pkg


_ensure_stubs()
config_bridge = importlib.import_module("dreame_scheduler.config_bridge")
report = importlib.import_module("dreame_scheduler.report")
const = importlib.import_module("dreame_scheduler.const")


class _DummyEngine:
    def __init__(self, home=True, presence=None, nxt=None):
        self._home = home
        self._presence = presence if presence is not None else ["person.dan"]
        self._nxt = nxt

    def _presence_home(self):
        return self._home

    def _presence_entities(self):
        return self._presence

    def next_run(self, now):
        return self._nxt


def test_filter_select_options_drops_placeholders():
    assert config_bridge.filter_select_options(["unavailable"]) == []
    assert config_bridge.filter_select_options(["", "  ", "Unknown", "NONE", "Unavailable"]) == []
    assert config_bridge.filter_select_options(None) == []
    assert config_bridge.filter_select_options(["Map 1", "unavailable", "Upstairs"]) == ["Map 1", "Upstairs"]


def test_report_schedule_uses_defaults_when_omitted():
    now = datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc)
    nxt = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    sched = report._schedule(None, _DummyEngine(home=True, nxt=nxt), {}, {}, now)

    assert sched["require_away"] is const.DEFAULT_REQUIRE_AWAY
    assert sched["daily_time"] == const.DEFAULT_DAILY_TIME
    assert sched["window"] == {
        "enabled": const.DEFAULT_WINDOW_ENABLED,
        "start": const.DEFAULT_WINDOW_START,
        "end": const.DEFAULT_WINDOW_END,
    }
    assert sched["catchup"] == {
        "enabled": const.DEFAULT_CATCHUP_ENABLED,
        "day": const.WEEKDAYS[const.DEFAULT_CATCHUP_DAY],
        "time": const.DEFAULT_CATCHUP_TIME,
    }


def test_report_schedule_respects_explicit_false_and_overrides():
    now = datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc)
    opts = {
        const.OPT_REQUIRE_AWAY: False,
        const.OPT_DAILY_TIME: "08:15",
        const.OPT_WINDOW_ENABLED: False,
        const.OPT_WINDOW_START: "07:00",
        const.OPT_WINDOW_END: "18:00",
        const.OPT_CATCHUP_ENABLED: False,
        const.OPT_CATCHUP_DAY: "0",
        const.OPT_CATCHUP_TIME: "11:30",
    }
    sched = report._schedule(None, _DummyEngine(home=False), opts, {}, now)

    assert sched["require_away"] is False
    assert sched["daily_time"] == "08:15"
    assert sched["window"] == {"enabled": False, "start": "07:00", "end": "18:00"}
    assert sched["catchup"] == {"enabled": False, "day": "Mon", "time": "11:30"}


def test_report_schedule_mirrors_engine_for_stored_none():
    # The engine reads entry.options.get(key, default): a stored None stays
    # None (off), so the report must show it the same way.
    now = datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc)
    sched = report._schedule(None, _DummyEngine(home=True), {const.OPT_REQUIRE_AWAY: None}, {}, now)
    assert sched["require_away"] is False


if __name__ == "__main__":
    test_filter_select_options_drops_placeholders()
    test_report_schedule_uses_defaults_when_omitted()
    test_report_schedule_respects_explicit_false_and_overrides()
    test_report_schedule_mirrors_engine_for_stored_none()
    print("test_config_and_report: all passed")
