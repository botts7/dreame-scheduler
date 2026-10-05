"""Persistent operational state for one scheduler entry.

Backed by a Home Assistant ``Store`` (survives restarts) exactly like the
wallbox schedule_arbiter's snapshot — so a crash or reboot never loses which
rooms were already cleaned this week, nor forgets an in-flight run it still
needs to verify.

Multi-floor: Dreame reuses room segment-ids across saved maps, so all per-room
and per-day state lives under ``maps[<map key>]`` (see ``scheduler`` helpers and
docs/ROADMAP.md #5). The engine sets the active map each tick via
``set_active_map`` and every per-room accessor below transparently reads/writes
that map's slice. House/robot-level state (presence, week anchor, consumables,
learned traps, history, the single in-flight run) stays top-level. A single-map
home has no selected-map entity, so it uses the ``default`` slice and behaves
exactly as before.

State shape (all JSON-serialisable):
    {
      "enabled": bool,                 # master on/off (mirrored by the switch)
      "week_start": "YYYY-MM-DD",      # anchor date of the current tracking week
      "away_since": "iso-ts" | null,   # when the house last became empty
      "active_run": { ... , "map": key } | null,  # in-flight dispatch (one at a time)
      "last_run": { ... summary ... } | null,
      "maps": {                        # per-map (per-floor) slices
        "<key>": {
          "cleaned": {seg: "iso-ts"},        # rooms CONFIRMED cleaned this week
          "unreachable": {seg: count},       # rooms skipped (door shut / not reached)
          "day_dispatched": "YYYY-MM-DD",    # last date the daily schedule fired
          "catchup_dispatched": "YYYY-MM-DD",
          "slots_done": {"date": iso, "keys": [...]},  # extra-times de-dup
          "mop_counters": {...}, "door_deferred": {...},
          "manual_clean": {...}, "room_learn": {...}, "last_edge": "YYYY-MM-DD",
        }
      },
    }
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .scheduler import DEFAULT_MAP_KEY, MAP_SCOPED_STATE_KEYS, migrate_state_to_maps

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


def _empty_state() -> dict:
    return {
        "enabled": True,
        "week_start": None,
        "away_since": None,
        "active_run": None,
        "last_run": None,
        "last_clean": None,     # iso-ts of the most recent confirmed room clean
        "nudged_on": None,      # date we last sent a stale-house nudge (once/day)
        "resume": None,         # {"segments": [...], "kind": "...", "map": key} to finish when empty
        "history": [],          # capped run log: [{ts, weekday, kind, map, per_room:{seg:{...}}}]
        "stuck_events": [],     # capped stuck/recovery log for the learning engine
        "learned_suggested": {},  # trap-learner suggestion keys already notified -> iso ts
        "learned_promoted": {},   # trap-learner keys promoted to a permanent no-go -> iso ts
        "tidy_reminded_on": None, # date we last sent a "clear the floor" reminder (once/day)
        "prerun_announced": None, # date we last sent the pre-run tidy heads-up (once/day)
        "consumable_alerted": {}, # wear-parts we've flagged as low -> {key: True}
        "edge_run": None,         # active edge clean {segments:[...], map: key} or None
        "edge_restore": None,     # settings to put back once the robot re-docks after an edge run
        "maps": {},               # per-map slices (see module docstring); created lazily
    }


class WeekTracker:
    """Loads/saves one entry's operational state; small typed helpers on top."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(hass, _STORE_VERSION, f"dreame_scheduler_{entry_id}")
        self._state: dict = _empty_state()
        self._active_map: str = DEFAULT_MAP_KEY
        self._loaded = False

    # ---- lifecycle ----
    async def async_load(self) -> dict:
        data = await self._store.async_load()
        if isinstance(data, dict):
            merged = _empty_state()
            merged.update(data)
            self._state = merged
        # Fold any legacy top-level per-room state into maps[default] (one-time).
        migrate_state_to_maps(self._state)
        self._loaded = True
        return self._state

    async def async_save(self) -> None:
        await self._store.async_save(self._state)

    @property
    def state(self) -> dict:
        return self._state

    # ---- active map (multi-floor) ----
    def set_active_map(self, key: str | None) -> None:
        """Point the per-room accessors at ``key``'s slice (created if new).
        Empty/None normalises to the default map, so single-floor is unchanged."""
        self._active_map = str(key) if key else DEFAULT_MAP_KEY
        self._map()

    @property
    def active_map(self) -> str:
        return self._active_map

    def _map(self) -> dict:
        """The active map's state slice (created on first use)."""
        maps = self._state.setdefault("maps", {})
        slot = maps.get(self._active_map)
        if not isinstance(slot, dict):
            slot = {}
            maps[self._active_map] = slot
        return slot

    # ---- enabled ----
    @property
    def enabled(self) -> bool:
        return bool(self._state.get("enabled", True))

    async def async_set_enabled(self, value: bool) -> None:
        self._state["enabled"] = bool(value)
        await self.async_save()

    # ---- week tracking (per active map) ----
    @property
    def week_start(self) -> str | None:
        return self._state.get("week_start")

    @property
    def cleaned(self) -> dict:
        return self._map().setdefault("cleaned", {})

    @property
    def unreachable(self) -> dict:
        return self._map().setdefault("unreachable", {})

    async def async_reset_week(self, new_week_start: str) -> dict:
        """Snapshot the finishing week's outcome (aggregated across every map),
        then clear the per-map week counters. Returns the previous week's summary
        for the weekly notice. The week anchor is house-wide (top-level)."""
        agg_cleaned: dict = {}
        agg_unreachable: dict = {}
        for slot in self._state.get("maps", {}).values():
            if not isinstance(slot, dict):
                continue
            agg_cleaned.update(slot.get("cleaned") or {})
            agg_unreachable.update(slot.get("unreachable") or {})
        summary = {
            "week_start": self._state.get("week_start"),
            "cleaned": agg_cleaned,
            "unreachable": agg_unreachable,
        }
        self._state["week_start"] = new_week_start
        for slot in self._state.get("maps", {}).values():
            if not isinstance(slot, dict):
                continue
            slot["cleaned"] = {}
            slot["unreachable"] = {}
            slot["day_dispatched"] = None
            slot["catchup_dispatched"] = None
            slot["slots_done"] = None
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

    # ---- dispatch bookkeeping (per active map) ----
    @property
    def day_dispatched(self) -> str | None:
        return self._map().get("day_dispatched")

    @property
    def catchup_dispatched(self) -> str | None:
        return self._map().get("catchup_dispatched")

    async def async_set_day_dispatched(self, iso_date: str) -> None:
        self._map()["day_dispatched"] = iso_date
        await self.async_save()

    async def async_set_catchup_dispatched(self, iso_date: str) -> None:
        self._map()["catchup_dispatched"] = iso_date
        await self.async_save()

    # ---- per-room extra-times de-dup (multiple cleans per day, per active map) ----
    def slots_done_today(self, today_iso: str) -> set:
        """Set of slot-keys already fired TODAY on the active map (empty on a new
        day). The slice keeps one day's keys; a date mismatch means yesterday's."""
        sd = self._map().get("slots_done")
        if isinstance(sd, dict) and sd.get("date") == today_iso:
            return set(sd.get("keys") or [])
        return set()

    async def async_mark_slots(self, today_iso: str, keys) -> None:
        """Record extra-time slot-keys fired today on the active map. Rolls to a
        fresh set when the stored date isn't today's, so de-dup resets each day."""
        current = self.slots_done_today(today_iso)
        before = len(current)
        current.update(str(k) for k in (keys or []))
        stored = self._map().get("slots_done")
        stale_date = not isinstance(stored, dict) or stored.get("date") != today_iso
        if len(current) != before or stale_date:
            self._map()["slots_done"] = {"date": today_iso, "keys": sorted(current)}
            await self.async_save()

    # ---- presence grace (house-wide) ----
    @property
    def away_since(self) -> str | None:
        return self._state.get("away_since")

    async def async_set_away_since(self, ts: str | None) -> None:
        if self._state.get("away_since") != ts:
            self._state["away_since"] = ts
            await self.async_save()

    # ---- in-flight run (one at a time, house-wide; tagged with its map) ----
    @property
    def active_run(self) -> dict | None:
        return self._state.get("active_run")

    async def async_set_active_run(self, run: dict | None) -> None:
        if isinstance(run, dict):
            run.setdefault("map", self._active_map)   # tag the floor it belongs to
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

    # ---- trap-learner bookkeeping (which suggestions were surfaced / applied) --
    @property
    def learned_suggested(self) -> dict:
        return self._state.setdefault("learned_suggested", {})

    @property
    def learned_promoted(self) -> dict:
        return self._state.setdefault("learned_promoted", {})

    async def async_mark_suggested(self, key: str, ts: str) -> None:
        self.learned_suggested[str(key)] = ts
        await self.async_save()

    async def async_mark_promoted(self, key: str, ts: str) -> None:
        self.learned_promoted[str(key)] = ts
        await self.async_save()

    @property
    def tidy_reminded_on(self) -> str | None:
        return self._state.get("tidy_reminded_on")

    async def async_set_tidy_reminded_on(self, iso_date: str | None) -> None:
        self._state["tidy_reminded_on"] = iso_date
        await self.async_save()

    @property
    def prerun_announced(self) -> str | None:
        return self._state.get("prerun_announced")

    async def async_set_prerun_announced(self, iso_date: str | None) -> None:
        self._state["prerun_announced"] = iso_date
        await self.async_save()

    # ---- manual-clean targets (rooms the robot can't reach, per active map) ----
    @property
    def manual_clean(self) -> dict:
        return self._map().setdefault("manual_clean", {})

    async def async_set_manual_clean(self, targets: dict) -> None:
        self._map()["manual_clean"] = dict(targets or {})
        await self.async_save()

    # ---- per-room mop cadence counters (per active map) ----
    @property
    def mop_counters(self) -> dict:
        return self._map().setdefault("mop_counters", {})

    async def async_set_mop_counter(self, seg, count: int, date_iso: str) -> None:
        """Record that room ``seg`` was swept on ``date_iso``, its cadence
        counter now at ``count``. One write per dispatched room per day."""
        self.mop_counters[str(seg)] = {"count": int(count), "date": date_iso}
        await self.async_save()

    # ---- door-retry deferral (rooms skipped for a shut door, per active map) ----
    @property
    def door_deferred(self) -> dict:
        return self._map().setdefault("door_deferred", {})

    async def async_mark_door_deferred(self, segments, date_iso: str) -> None:
        """Note rooms skipped today for a closed door, so the engine can retry
        them once the door reopens. Preserves an existing same-day retry count."""
        changed = False
        for seg in segments:
            cur = self.door_deferred.get(str(seg))
            if not isinstance(cur, dict) or cur.get("date") != date_iso:
                self.door_deferred[str(seg)] = {"date": date_iso, "retries": 0}
                changed = True
        if changed:
            await self.async_save()

    async def async_bump_door_retry(self, seg, date_iso: str) -> None:
        cur = self.door_deferred.get(str(seg))
        n = int(cur.get("retries", 0)) if isinstance(cur, dict) else 0
        self.door_deferred[str(seg)] = {"date": date_iso, "retries": n + 1}
        await self.async_save()

    async def async_clear_door_deferred(self, seg) -> None:
        if str(seg) in self.door_deferred:
            self.door_deferred.pop(str(seg), None)
            await self.async_save()

    @property
    def consumable_alerted(self) -> dict:
        return self._state.setdefault("consumable_alerted", {})

    async def async_set_consumable_alerted(self, flags: dict) -> None:
        """Persist which wear-parts have been flagged as low (so we alert once).
        Only writes when the set actually changed, to avoid needless saves."""
        if flags != self.consumable_alerted:
            self._state["consumable_alerted"] = dict(flags)
            await self.async_save()

    @property
    def edge_run(self) -> dict | None:
        return self._state.get("edge_run")

    async def async_set_edge_run(self, run: dict | None) -> None:
        if isinstance(run, dict):
            run.setdefault("map", self._active_map)   # tag the floor it belongs to
        self._state["edge_run"] = run
        await self.async_save()

    @property
    def edge_restore(self) -> dict | None:
        return self._state.get("edge_restore")

    async def async_set_edge_restore(self, restore: dict | None) -> None:
        self._state["edge_restore"] = restore
        await self.async_save()

    @property
    def room_learn(self) -> dict:
        return self._map().get("room_learn") or {}

    async def async_set_room_learn(self, seg, entry: dict) -> None:
        rl = dict(self._map().get("room_learn") or {})
        rl[str(seg)] = entry
        self._map()["room_learn"] = rl
        await self.async_save()

    @property
    def last_edge(self) -> str | None:
        return self._map().get("last_edge")

    async def async_set_last_edge(self, iso_date: str | None) -> None:
        self._map()["last_edge"] = iso_date
        await self.async_save()
