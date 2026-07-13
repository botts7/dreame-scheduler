"""Persistent operational state for one scheduler entry.

Backed by a Home Assistant ``Store`` (survives restarts) exactly like the
wallbox schedule_arbiter's snapshot — so a crash or reboot never loses which
rooms were already cleaned this week, nor forgets an in-flight run it still
needs to verify.

State shape (all JSON-serialisable):
    {
      "enabled": bool,                 # master on/off (mirrored by the switch)
      "week_start": "YYYY-MM-DD",      # anchor date of the current tracking week
      "cleaned": {seg: "iso-ts"},      # rooms CONFIRMED cleaned this week
      "unreachable": {seg: count},     # rooms skipped (door shut / not reached)
      "day_dispatched": "YYYY-MM-DD",  # last date the daily schedule fired
      "catchup_dispatched": "YYYY-MM-DD",
      "away_since": "iso-ts" | null,   # when the house last became empty
      "active_run": {                  # in-flight dispatch awaiting verification
          "kind": "daily|catchup|manual",
          "segments": [seg, ...],
          "started": "iso-ts",
          "notified_stuck": bool,
      } | null,
      "last_run": { ... summary ... } | null,
    }
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


def _empty_state() -> dict:
    return {
        "enabled": True,
        "week_start": None,
        "cleaned": {},
        "unreachable": {},
        "day_dispatched": None,
        "catchup_dispatched": None,
        "away_since": None,
        "active_run": None,
        "last_run": None,
        "last_clean": None,     # iso-ts of the most recent confirmed room clean
        "nudged_on": None,      # date we last sent a stale-house nudge (once/day)
        "resume": None,         # {"segments": [...], "kind": "..."} to finish when empty
        "history": [],          # capped run log: [{ts, weekday, kind, per_room:{seg:{...}}}]
        "stuck_events": [],     # capped stuck/recovery log for the learning engine:
                                # [{ts, room, x, y, error, kind, attempt}]
    }


class WeekTracker:
    """Loads/saves one entry's operational state; small typed helpers on top."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(hass, _STORE_VERSION, f"dreame_scheduler_{entry_id}")
        self._state: dict = _empty_state()
        self._loaded = False

    # ---- lifecycle ----
    async def async_load(self) -> dict:
        data = await self._store.async_load()
        if isinstance(data, dict):
            merged = _empty_state()
            merged.update(data)
            self._state = merged
        self._loaded = True
        return self._state

    async def async_save(self) -> None:
        await self._store.async_save(self._state)

    @property
    def state(self) -> dict:
        return self._state

    # ---- enabled ----
    @property
    def enabled(self) -> bool:
        return bool(self._state.get("enabled", True))

    async def async_set_enabled(self, value: bool) -> None:
        self._state["enabled"] = bool(value)
        await self.async_save()

    # ---- week tracking ----
    @property
    def week_start(self) -> str | None:
        return self._state.get("week_start")

    @property
    def cleaned(self) -> dict:
        return self._state.setdefault("cleaned", {})

    @property
    def unreachable(self) -> dict:
        return self._state.setdefault("unreachable", {})

    async def async_reset_week(self, new_week_start: str) -> dict:
        """Snapshot the finishing week's outcome, then clear counters for the
        new week. Returns the previous week's summary for the weekly notice."""
        summary = {
            "week_start": self._state.get("week_start"),
            "cleaned": dict(self.cleaned),
            "unreachable": dict(self.unreachable),
        }
        self._state["week_start"] = new_week_start
        self._state["cleaned"] = {}
        self._state["unreachable"] = {}
        self._state["day_dispatched"] = None
        self._state["catchup_dispatched"] = None
        await self.async_save()
        return summary

    async def async_mark_cleaned(self, segments, ts: str) -> None:
        for seg in segments:
            self.cleaned[str(seg)] = ts
            # A confirmed clean clears any prior unreachable strike.
            self.unreachable.pop(str(seg), None)
        if segments:
            self._state["last_clean"] = ts
        await self.async_save()

    # ---- stale-house nudge + resume queue ----
    @property
    def last_clean(self) -> str | None:
        return self._state.get("last_clean")

    @property
    def nudged_on(self) -> str | None:
        return self._state.get("nudged_on")

    async def async_set_nudged_on(self, iso_date: str | None) -> None:
        self._state["nudged_on"] = iso_date
        await self.async_save()

    @property
    def resume(self) -> dict | None:
        return self._state.get("resume")

    async def async_set_resume(self, resume: dict | None) -> None:
        self._state["resume"] = resume
        await self.async_save()

    async def async_mark_unreachable(self, segments) -> None:
        for seg in segments:
            self.unreachable[str(seg)] = int(self.unreachable.get(str(seg), 0)) + 1
        await self.async_save()

    # ---- dispatch bookkeeping ----
    async def async_set_day_dispatched(self, iso_date: str) -> None:
        self._state["day_dispatched"] = iso_date
        await self.async_save()

    async def async_set_catchup_dispatched(self, iso_date: str) -> None:
        self._state["catchup_dispatched"] = iso_date
        await self.async_save()

    # ---- presence grace ----
    @property
    def away_since(self) -> str | None:
        return self._state.get("away_since")

    async def async_set_away_since(self, ts: str | None) -> None:
        if self._state.get("away_since") != ts:
            self._state["away_since"] = ts
            await self.async_save()

    # ---- in-flight run ----
    @property
    def active_run(self) -> dict | None:
        return self._state.get("active_run")

    async def async_set_active_run(self, run: dict | None) -> None:
        self._state["active_run"] = run
        await self.async_save()

    async def async_set_last_run(self, summary: dict | None) -> None:
        self._state["last_run"] = summary
        await self.async_save()

    @property
    def last_run(self) -> dict | None:
        return self._state.get("last_run")

    # ---- run-history log (for fail-trend / weekday analytics) ----
    @property
    def history(self) -> list:
        return self._state.setdefault("history", [])

    async def async_append_history(self, entry: dict, cap: int = 200) -> None:
        hist = self.history
        hist.append(entry)
        if len(hist) > cap:          # keep only the most recent `cap` runs
            del hist[: len(hist) - cap]
        await self.async_save()

    # ---- stuck/recovery event log (feeds the learning engine) ----
    @property
    def stuck_events(self) -> list:
        return self._state.setdefault("stuck_events", [])

    async def async_log_stuck(self, event: dict, cap: int = 300) -> None:
        """Append a stuck/recovery event (where it wedged + context) for the
        recurring-trap learner. Capped; pure telemetry, no behaviour of its own."""
        evs = self.stuck_events
        evs.append(event)
        if len(evs) > cap:
            del evs[: len(evs) - cap]
        await self.async_save()
