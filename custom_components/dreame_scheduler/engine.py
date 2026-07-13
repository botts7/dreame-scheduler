"""The scheduler engine — all Home Assistant I/O for one config entry.

Runs the behaviour itself (like the wallbox ChargeAssistant): a 60s tick plus
state-change reactions drive pure decisions from scheduler.py / clean_window.py
/ clean_guards.py, then this layer reads live states, dispatches the clean,
verifies what actually happened, and notifies.

Nothing here is model-specific: every dreame entity is derived from the chosen
vacuum's object_id prefix (const.room_entity / const.e), so the same code runs
any robot the Tasshack ``dreame_vacuum`` integration exposes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import clean_guards, clean_window
from .const import (
    CONF_PREFIX,
    CONF_VACUUM_ENTITY,
    DEFAULT_AWAY_GRACE_MIN,
    DEFAULT_CATCHUP_DAY,
    DEFAULT_CATCHUP_ENABLED,
    DEFAULT_CATCHUP_TIME,
    DEFAULT_DAILY_TIME,
    DEFAULT_GUARD_DUSTBIN,
    DEFAULT_GUARD_WATER,
    DEFAULT_MAP_RESUME,
    DEFAULT_MIN_BATTERY,
    DEFAULT_VACUUM_BEFORE_MOP,
    DEFAULT_AUTO_RECOVER,
    DEFAULT_NOTIFY_SKIPPED,
    DEFAULT_NOTIFY_STUCK,
    DEFAULT_NOTIFY_WEEKLY,
    DEFAULT_REPEATS,
    DEFAULT_RESUME_WHEN_AWAY,
    DEFAULT_RETURN_ON_ARRIVAL,
    DEFAULT_STALE_AFTER_DAYS,
    DEFAULT_STALE_NUDGE_ENABLED,
    DEFAULT_REQUIRE_AWAY,
    DEFAULT_WEEK_START_DAY,
    DEFAULT_WINDOW_ENABLED,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_OVERRUN,
    DEFAULT_WINDOW_START,
    DREAME_DOMAIN,
    OPT_AWAY_GRACE_MIN,
    OPT_CATCHUP_DAY,
    OPT_CATCHUP_ENABLED,
    OPT_CATCHUP_TIME,
    OPT_DAILY_TIME,
    OPT_DEFAULT_MODE,
    OPT_DEFAULT_SUCTION,
    OPT_GUARD_DUSTBIN,
    OPT_GUARD_WATER,
    OPT_MAP_RESUME,
    OPT_MIN_BATTERY,
    OPT_NOTIFY_SKIPPED,
    OPT_NOTIFY_STUCK,
    OPT_NOTIFY_TARGETS,
    OPT_NOTIFY_WEEKLY,
    OPT_PRESENCE_ENTITIES,
    OPT_QUIET_SUCTION,
    OPT_REQUIRE_AWAY,
    OPT_RESUME_WHEN_AWAY,
    OPT_RETURN_ON_ARRIVAL,
    OPT_ROOMS,
    OPT_STALE_AFTER_DAYS,
    OPT_STALE_NUDGE_ENABLED,
    OPT_VACUUM_BEFORE_MOP,
    OPT_AUTO_RECOVER,
    OPT_WEEK_START_DAY,
    OPT_WINDOW_ENABLED,
    OPT_WINDOW_END,
    OPT_WINDOW_OVERRUN,
    OPT_WINDOW_START,
    ROOM_DAYS,
    ROOM_DOOR_SENSOR,
    ROOM_ENABLED,
    ROOM_MODE,
    ROOM_REPEATS,
    ROOM_SUCTION,
    ROOM_WETNESS,
    SEQ_MOP_MODES,
    SERVICE_CLEAN_SEGMENT,
    SUF_CLEANING_MODE,
    SUF_CLEAN_WATER,
    SUF_CURRENT_ROOM,
    SUF_CUSTOMIZED,
    SUF_DIRTY_WATER,
    SUF_DUST_BAG,
    SUF_ERROR,
    SUF_STATUS,
    SUF_SUCTION,
    SUF_TASK_STATUS,
    TICK_SECONDS,
    WEEKDAYS,
    e as entity_of,
    room_entity,
)
from .scheduler import (
    all_enabled_segments,
    choose_dispatch,
    needs_week_rollover,
    pending_rooms,
    rooms_due_today,
    week_start_for,
)
from .week_tracker import WeekTracker

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = ("unknown", "unavailable", "none", "")
# Vacuum entity states that mean it's actively working (so a run has begun).
_ACTIVE_STATES = ("cleaning", "returning", "paused")
# States that mean the run has ended and the robot is parked.
_PARKED_STATES = ("docked", "idle")
# Substrings in status/task_status that mean the robot is still in a cleaning
# job — including going back to the dock to FETCH/INSTALL the mop mid-task (it
# will resume cleaning after). Post-clean maintenance (washing/drying/emptying)
# is deliberately NOT here: once the robot is parked and only doing those, the
# clean itself is finished. Keep 'install_mop' but never bare 'mop' (that would
# also match the post-clean 'washing_mop' and hang the run forever).
_CLEANING_ACTIVE_WORDS = (
    "cleaning", "sweeping", "mopping", "install_mop", "installing",
    "relocat", "building_map", "mapping", "spot", "segment", "zone",
)
# How long the robot must sit parked-and-not-cleaning before we call the run
# done. Debounces the brief dock it makes to install the mop before cleaning.
COMPLETE_DWELL_SECONDS = 45
# The cleaning_count counter ticks a beat before the matching history record
# syncs into the sensor. After completion is detected, wait this long for the
# fresh record (carrying THIS run's blocked_rooms/cleaned_area) before giving up
# and finalising without one. (Live 2026-07-09: the lag exceeded 90 s and a run
# was finalised record-less, mis-skipping every unvisited room.)
RECORD_SYNC_WAIT_SECONDS = 300
# Watchdog for a suspended (map-resume) run: a presence entity wedged at "home"
# (or a genuinely full house) must not freeze the scheduler forever — the Dreame
# breakpoint expires after hours anyway. Past this age, finalise as interrupted
# and queue the remaining rooms.
SUSPEND_MAX_SECONDS = 24 * 3600
# A history record with completed=False is a PARTIAL session of a task the robot
# intends to resume (dock to wash the mop / recharge, then head back out). Only
# trust it as the task end after this long parked with no renewed activity —
# the final session's record arrives with completed=True and finalises at once.
INCOMPLETE_RECORD_DWELL_SECONDS = 1800
# Allowed skew (seconds) between HA 'now' and a record's timestamp (the cleaning
# session start, per the robot cloud) when deciding a record belongs to a run.
RECORD_TS_TOLERANCE = 120

# --- Auto-recovery (unstick + carry on) ---
# Vacuum error substrings that mean "physically stuck but likely reversible" —
# worth trying to free rather than just giving up.
_RECOVERABLE_ERROR_WORDS = (
    "suffocate", "stuck", "trap", "wheel", "bumper", "cliff", "tangle",
    "edge", "picked", "lifted", "route", "path",
)
REVERSE_OUT_STEPS = 3              # remote-control reverse nudges to back off a trap
REVERSE_OUT_VELOCITY = -110       # straight reverse (negative), retracing the entry route
MAX_RECOVER_ATTEMPTS = 3            # per run, before giving up and docking
RECOVER_FREE_TIMEOUT = 90          # seconds to wait for it to free itself
NOGO_HALF_MM = 300                 # half-size of the temp no-go box around the stuck point (30 cm)
MIN_AREA_PER_ROOM_M2 = 2.0         # a run sweeping less than this per dispatched room didn't really clean


class SchedulerEngine:
    """One engine per config entry. Owns the timer + listeners + decisions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, tracker: WeekTracker) -> None:
        self.hass = hass
        self.entry = entry
        self.tracker = tracker
        self._unsub: list = []
        self._status: dict = {"state": "starting", "reason": "", "enabled": True}
        # coordinator sets this so entities refresh when status changes.
        self._notify_update = None
        # Serialises evaluations AND manual actions: state-event re-evaluations
        # can land while a tick is still awaiting mid-finalise (double-banking
        # rooms + duplicate history entries, seen live 2026-07-09), and a manual
        # dispatch racing a tick's dispatch would double-send clean_segment.
        self._eval_lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    async def async_start(self) -> None:
        await self.tracker.async_load()
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._handle_tick, timedelta(seconds=TICK_SECONDS)
            )
        )
        watched = [self._vacuum_entity, entity_of("sensor", self._prefix, SUF_ERROR)]
        watched += [ent for ent in self._presence_entities() if ent]
        current_room = entity_of("sensor", self._prefix, "current_room")
        watched.append(current_room)
        self._unsub.append(
            async_track_state_change_event(self.hass, watched, self._handle_state_event)
        )
        await self._tick()  # evaluate immediately on startup

    async def async_stop(self) -> None:
        for u in self._unsub:
            u()
        self._unsub.clear()

    def set_update_callback(self, cb) -> None:
        self._notify_update = cb

    @property
    def status(self) -> dict:
        return self._status

    # -------------------------------------------------------------- accessors
    @property
    def _prefix(self) -> str:
        return self.entry.data[CONF_PREFIX]

    @property
    def _vacuum_entity(self) -> str:
        return self.entry.data[CONF_VACUUM_ENTITY]

    def _opt(self, key, default):
        return self.entry.options.get(key, default)

    def _opt_int(self, key, default):
        """Option read that always yields an int — a stored value that's None or
        non-numeric (legacy entry, hand-edited options) falls back to the default
        instead of raising and failing the entry's setup tick."""
        try:
            return int(self._opt(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _rooms(self) -> dict:
        return self._opt(OPT_ROOMS, {}) or {}

    def _presence_entities(self) -> list[str]:
        return list(self._opt(OPT_PRESENCE_ENTITIES, []) or [])

    def _notify_names(self) -> list[str]:
        targets = list(self._opt(OPT_NOTIFY_TARGETS, []) or [])
        return targets or ["persistent_notification"]

    # ------------------------------------------------------------ state reads
    def _sval(self, entity_id: str) -> str | None:
        st = self.hass.states.get(entity_id)
        if st is None or str(st.state).strip().lower() in _UNAVAILABLE:
            return None
        return st.state

    def _vacuum_state(self) -> str | None:
        return self._sval(self._vacuum_entity)

    def _is_active(self) -> bool:
        return (self._vacuum_state() or "") in _ACTIVE_STATES

    def _robot_busy(self) -> bool:
        """The robot is mid-task RIGHT NOW per its own sensors. Dispatching
        clean_segment then would silently REPLACE its current job (live
        2026-07-09: a daily dispatch stomped an in-flight 9-room task)."""
        if self._is_active():
            return True
        status = (self._sval(entity_of("sensor", self._prefix, SUF_STATUS)) or "").lower()
        task = (self._sval(entity_of("sensor", self._prefix, SUF_TASK_STATUS)) or "").lower()
        return (any(w in task for w in _CLEANING_ACTIVE_WORDS)
                or any(w in status for w in _CLEANING_ACTIVE_WORDS))

    def _error_active(self) -> bool:
        # Only a GENUINE fault should block/interrupt cleaning. The vacuum
        # entity's own 'error' state is that signal. sensor.error also reports
        # harmless maintenance reminders (e.g. 'clean_mop_pad', 'dust_bag_full'
        # hints) which must NOT stop a scheduled clean — so we deliberately do
        # not treat sensor.error text as blocking.
        return (self._vacuum_state() or "") == "error"

    def _recoverable_error(self) -> bool:
        """True if the vacuum is in error AND the error text looks like a
        physically-stuck-but-freeable fault (not a maintenance reminder)."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _RECOVERABLE_ERROR_WORDS)

    # -------- map reads / zone writes (for auto-recovery no-go placement) -----
    def _map_attr(self, key: str):
        st = self.hass.states.get(entity_of("camera", self._prefix, "map"))
        return st.attributes.get(key) if st else None

    def _vacuum_position(self) -> tuple[int, int] | None:
        pos = self._map_attr("vacuum_position")
        if isinstance(pos, dict) and pos.get("x") is not None and pos.get("y") is not None:
            return int(pos["x"]), int(pos["y"])
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            return int(pos[0]), int(pos[1])
        return None

    @staticmethod
    def _area_to_rect(a) -> list[int] | None:
        """Camera no-go/no-mop area (4-corner dict) -> service [x0,y0,x1,y1]."""
        if isinstance(a, dict):
            xs = [a[k] for k in ("x0", "x1", "x2", "x3") if isinstance(a.get(k), (int, float))]
            ys = [a[k] for k in ("y0", "y1", "y2", "y3") if isinstance(a.get(k), (int, float))]
            if xs and ys:
                return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        if isinstance(a, (list, tuple)) and len(a) >= 4:
            return [int(a[0]), int(a[1]), int(a[2]), int(a[3])]
        return None

    def _current_zones(self) -> tuple[list, list, list]:
        """Existing (walls, no-go, no-mop) from the map, in the service's
        [x0,y0,x1,y1] format, so a write preserves the user's own zones."""
        walls = [self._area_to_rect(w) for w in (self._map_attr("virtual_walls") or [])]
        zones = [self._area_to_rect(z) for z in (self._map_attr("no_go_areas") or [])]
        mops = [self._area_to_rect(m) for m in (self._map_attr("no_mopping_areas") or [])]
        return ([w for w in walls if w], [z for z in zones if z], [m for m in mops if m])

    async def _write_zones(self, walls: list, zones: list, no_mops: list) -> None:
        await self._svc("dreame_vacuum", "vacuum_set_restricted_zone",
                        {"entity_id": self._vacuum_entity,
                         "walls": walls, "zones": zones, "no_mops": no_mops})

    async def _add_temp_nogo(self, run: dict, x: int, y: int) -> None:
        """Drop a small temporary no-go box around (x,y) so the robot stops
        driving back into whatever just wedged it. Backs the map up once per run
        before mutating it; the box is removed again in _finalize_run."""
        box = [x - NOGO_HALF_MM, y - NOGO_HALF_MM, x + NOGO_HALF_MM, y + NOGO_HALF_MM]
        walls, zones, no_mops = self._current_zones()
        if not run.get("map_backed_up"):
            try:
                await self._svc("dreame_vacuum", "vacuum_backup_map",
                                {"entity_id": self._vacuum_entity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("auto-recover: map backup failed: %s", exc)
            run["map_backed_up"] = True
        await self._write_zones(walls, zones + [box], no_mops)
        run.setdefault("temp_nogos", []).append(box)

    async def _clear_temp_nogos(self, run: dict) -> None:
        boxes = run.get("temp_nogos") or []
        if not boxes:
            return
        walls, zones, no_mops = self._current_zones()
        keep = [z for z in zones if z not in boxes]
        try:
            await self._write_zones(walls, keep, no_mops)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("auto-recover: clearing temp no-go failed: %s", exc)

    async def _reverse_out(self, steps: int = REVERSE_OUT_STEPS,
                           velocity: int = REVERSE_OUT_VELOCITY) -> None:
        """Back the robot straight out the way it came — the primitive that
        reliably frees a wedge when return_to_base alone keeps ramming the
        blocked path forward. Verified live 2026-07-13: a `route` error at a rug
        lip cleared after a few reverse nudges. No vacuuming, minimal battery."""
        for _ in range(max(1, steps)):
            try:
                await self._svc("dreame_vacuum", "vacuum_remote_control_move_step",
                                {"entity_id": self._vacuum_entity,
                                 "rotation": 0, "velocity": velocity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("auto-recover: reverse step failed: %s", exc)
                return
            await asyncio.sleep(1.0)

    async def _maybe_auto_recover(self, run: dict, now: datetime) -> bool:
        """Unstick a wedged robot and CARRY ON cleaning (avoiding the spot),
        rather than docking. Returns True if it handled this tick.

        NOTE: the free-then-continue command sequence (return_to_base to free,
        then vacuum.start to continue) is the one part that wants live tuning per
        model — kept isolated here so it's easy to adjust."""
        if not bool(self._opt(OPT_AUTO_RECOVER, DEFAULT_AUTO_RECOVER)):
            return False

        if run.get("recovering"):
            if self._vacuum_state() is not None and not self._error_active():
                # Freed itself. (An UNAVAILABLE entity is NOT "error cleared" —
                # a blind vacuum.start on an unknown state can launch a
                # full-house clean.) If someone came home during the recovery,
                # dock instead of resuming into an occupied house.
                home_block = (run.get("interrupting") or run.get("suspended")
                              or (run.get("kind") != "manual"
                                  and bool(self._opt(OPT_RETURN_ON_ARRIVAL, DEFAULT_RETURN_ON_ARRIVAL))
                                  and bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))
                                  and self._presence_home() is True))
                if home_block:
                    await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
                    self._set_status("returning", "recovered — someone home, docking")
                else:
                    # Resume the clean; the fresh no-go keeps it clear of the
                    # trap. Don't let it just dock.
                    await self._svc("vacuum", "start", {"entity_id": self._vacuum_entity})
                    self._set_status("running", "recovered — carrying on, steering clear of the stuck spot")
                run["recovering"] = False
                await self.tracker.async_set_active_run(run)
                return True
            started = _parse_iso(run.get("recover_started"))
            waited = (now - started).total_seconds() if started else 0
            if waited < RECOVER_FREE_TIMEOUT:
                self._set_status("returning", "freeing itself from a stuck spot")
                return True
            # Couldn't free itself in time — stop trying; fall through to the
            # normal stuck-notify + completion handling (which will dock/alert).
            run["recovering"] = False
            await self.tracker.async_set_active_run(run)
            return False

        if not self._recoverable_error():
            return False
        if int(run.get("recover_count", 0)) >= MAX_RECOVER_ATTEMPTS:
            # Give up — but at least try to bring it home once before falling
            # through to the error notify (nothing else ever docks it).
            if not run.get("gave_up_dock"):
                run["gave_up_dock"] = True
                await self.tracker.async_set_active_run(run)
                await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            return False

        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        pos = self._vacuum_position()
        if pos is not None:
            try:
                await self._add_temp_nogo(run, pos[0], pos[1])
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("auto-recover: no-go write failed: %s", exc)
        run["recover_count"] = int(run.get("recover_count", 0)) + 1
        run["recovering"] = True
        run["recover_started"] = now.isoformat()
        run["errored"] = True
        await self.tracker.async_set_active_run(run)
        # Reverse out first — back off the trap the way it came in. return_to_base
        # alone kept ramming the blocked path forward (live 2026-07-13: a route
        # error at a rug lip); a straight reverse retraces the entry route and
        # frees it. Then hand off to return_to_base, which continues the robot's
        # own reverse-out recovery and re-localises (verified 2026-07-07 + 07-13).
        await self._reverse_out()
        await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._notify(
                "🛟 Vacuum recovering",
                f"Got stuck near {where} — walled off the spot and freeing it to carry on.",
                high_priority=True,
            )
        _LOGGER.info("auto-recover: attempt %s near %s at %s", run["recover_count"], where, pos)
        return True

    def _battery(self) -> float | None:
        st = self.hass.states.get(self._vacuum_entity)
        if st is not None:
            bl = st.attributes.get("battery_level")
            if bl is not None:
                try:
                    return float(bl)
                except (TypeError, ValueError):
                    pass
        raw = self._sval(entity_of("sensor", self._prefix, "battery_level"))
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _presence_home(self) -> bool | None:
        """True if anyone home, False if all away, None if undeterminable."""
        ents = self._presence_entities()
        if not ents:
            return None
        seen = []
        for ent in ents:
            st = self.hass.states.get(ent)
            if st is None or str(st.state).lower() in _UNAVAILABLE:
                continue
            seen.append(str(st.state).lower() in ("home", "on"))
        if not seen:
            return None
        return any(seen)

    def _door_state(self, seg: str) -> str | None:
        cfg = self._rooms().get(seg, {})
        ent = cfg.get(ROOM_DOOR_SENSOR)
        if not ent:
            return None
        return self._sval(ent)

    def _room_name(self, seg) -> str:
        nm = self._sval(room_entity("select", self._prefix, seg, "name"))
        return nm or f"Room {seg}"

    def _cleaning_count(self) -> int | None:
        """Monotonic 'number of cleans' counter — increments once per completed
        clean. The definitive 'a clean just finished' signal (works even for a
        room that finishes in seconds)."""
        raw = self._sval(entity_of("sensor", self._prefix, "cleaning_count"))
        try:
            return int(float(raw)) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _latest_history_record(self, min_ts: float | None = None) -> dict | None:
        """The most recent cleaning-history record. Each record carries
        ``blocked_rooms`` ({segment_id: reason}) and ``cleaned_area`` — the map-
        derived truth of what actually got cleaned this run.

        When ``min_ts`` is given, only return the newest record if it belongs to
        this run (its timestamp — the cleaning session start — is at/after the
        run start, minus a skew tolerance). Otherwise return None so the caller
        knows the fresh record hasn't synced yet and can wait, rather than
        finalising off the PREVIOUS run's record."""
        st = self.hass.states.get(entity_of("sensor", self._prefix, "cleaning_history"))
        if st is None:
            return None
        best, best_ts = None, -1
        for val in st.attributes.values():
            if isinstance(val, dict) and "timestamp" in val:
                try:
                    ts = int(val["timestamp"])
                except (TypeError, ValueError):
                    continue
                if ts > best_ts:
                    best, best_ts = val, ts
        if min_ts is not None and best is not None and best_ts < int(min_ts) - RECORD_TS_TOLERANCE:
            return None
        return best

    def _newest_record_ts(self) -> int | None:
        """Timestamp of the newest history record right now — captured at
        dispatch as the run's baseline, so a record that already existed can
        never be mistaken for this run's outcome (the min_ts skew tolerance
        alone lets a task that ended seconds before dispatch through)."""
        rec = self._latest_history_record()
        try:
            return int(rec["timestamp"]) if rec else None
        except (KeyError, TypeError, ValueError):
            return None

    def _run_record(self, run: dict) -> dict | None:
        """Newest history record attributable to THIS run: at/after the run's
        start (skew-tolerant) AND strictly newer than the newest record that
        existed at dispatch."""
        started = _parse_iso(run.get("started"))
        rec = self._latest_history_record(
            min_ts=started.timestamp() if started else None)
        base_ts = run.get("record_ts_at_start")
        if rec is not None and base_ts is not None:
            try:
                if int(rec.get("timestamp", 0)) <= int(base_ts):
                    return None
            except (TypeError, ValueError):
                return None
        return rec

    def _charging_mid_task(self) -> bool:
        """Recharging below full — a big multi-session task can sit on the dock
        well past any fixed dwell before heading back out; don't declare the run
        finished while the robot is just refuelling."""
        st = (self._sval(entity_of("sensor", self._prefix, "charging_status")) or "").lower()
        b = self._battery()
        return st == "charging" and b is not None and b < 90

    # ------------------------------------------------------------- tick logic
    @callback
    def _handle_tick(self, _now) -> None:
        self.hass.async_create_task(self._tick())

    @callback
    def _handle_state_event(self, event: Event) -> None:
        # Accumulate visited rooms while a run is in flight, then re-evaluate.
        run = self.tracker.active_run
        if run is not None and event.data.get("entity_id") == entity_of(
            "sensor", self._prefix, "current_room"
        ):
            new = event.data.get("new_state")
            if new is not None and str(new.state).lower() not in _UNAVAILABLE:
                visited = run.setdefault("visited", [])
                if new.state not in visited:
                    visited.append(new.state)
                    # Persist — otherwise a Store reload (e.g. options update)
                    # drops the in-flight visited list, and it's the fallback
                    # signal for crediting a room on an abnormal end.
                    self.hass.async_create_task(self._persist_visited(run))
        self.hass.async_create_task(self._tick())

    async def _persist_visited(self, run: dict) -> None:
        # Queued from a state event: by the time this executes the run may have
        # been finalised — writing it back then RESURRECTS a finished run, which
        # the next tick re-finalises (duplicate history + double room banking).
        if self.tracker.active_run is run:
            await self.tracker.async_set_active_run(run)

    async def _tick(self) -> None:
        # One evaluation at a time — an overlapping tick (state events fire one
        # per current_room change) must not re-finalise or re-dispatch; the next
        # interval tick re-evaluates anyway. Manual actions share the same lock
        # (they wait rather than skip — a user action must run).
        if self._eval_lock.locked():
            return
        async with self._eval_lock:
            await self._tick_once()

    async def _tick_once(self) -> None:
        if not self.tracker.enabled:
            self._set_status("disabled", "scheduler disabled")
            return

        now = dt_util.now()
        today = now.date()
        weekday = now.weekday()
        now_min = now.hour * 60 + now.minute

        # 1) Weekly rollover — finalise the finished week, then reset counters.
        # Deferred while a run is in flight: rolling mid-run would land that
        # run's cleaned marks in the NEW week (those rooms would then be skipped
        # for the whole following week) and seed it with the old run's strikes.
        week_start_day = self._opt_int(OPT_WEEK_START_DAY, DEFAULT_WEEK_START_DAY)
        if (self.tracker.active_run is None
                and needs_week_rollover(today, self.tracker.week_start, week_start_day)):
            new_start = week_start_for(today, week_start_day).isoformat()
            summary = await self.tracker.async_reset_week(new_start)
            await self._maybe_weekly_notice(summary)

        # 2) Presence grace bookkeeping.
        away_ok = await self._update_presence(now)

        # 3) A run is in flight → verify/await it; never dispatch concurrently.
        if self.tracker.active_run is not None:
            await self._check_active_run(now, away_ok)
            return

        # 3b) An interrupted run waiting for the house to empty again?
        if self.tracker.resume and bool(self._opt(OPT_RESUME_WHEN_AWAY, DEFAULT_RESUME_WHEN_AWAY)):
            if await self._try_resume(now, now_min, away_ok):
                return

        # 4) Is a daily or catch-up dispatch due right now?
        decision = choose_dispatch(
            now_date=today,
            weekday=weekday,
            now_min=now_min,
            rooms=self._rooms(),
            cleaned=self.tracker.cleaned,
            daily_time_min=clean_window.to_minutes(self._opt(OPT_DAILY_TIME, DEFAULT_DAILY_TIME)),
            day_dispatched_on=self.tracker.state.get("day_dispatched"),
            catchup_enabled=bool(self._opt(OPT_CATCHUP_ENABLED, DEFAULT_CATCHUP_ENABLED)),
            catchup_day=self._opt_int(OPT_CATCHUP_DAY, DEFAULT_CATCHUP_DAY),
            catchup_time_min=clean_window.to_minutes(self._opt(OPT_CATCHUP_TIME, DEFAULT_CATCHUP_TIME)),
            catchup_dispatched_on=self.tracker.state.get("catchup_dispatched"),
        )

        if decision.action == "idle":
            # Nothing due, OR the day/week is already handled — record that so we
            # don't re-scan every tick, and surface a friendly idle status.
            if decision.kind == "daily" and decision.reason == "nothing_due_today":
                await self.tracker.async_set_day_dispatched(today.isoformat())
            elif decision.kind == "catchup" and decision.reason == "week_already_complete":
                await self.tracker.async_set_catchup_dispatched(today.isoformat())
            await self._maybe_stale_nudge(now, away_ok)
            self._set_status("idle", self._idle_reason(now, away_ok))
            return

        # 5) A dispatch is due — apply live gates (door / presence / window /
        #    battery / station) before actually sending the robot out.
        await self._attempt_dispatch(decision, now, now_min, away_ok)

    # --------------------------------------------------------- presence grace
    async def _update_presence(self, now: datetime) -> bool:
        require_away = bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))
        if not require_away or not self._presence_entities():
            await self.tracker.async_set_away_since(None)
            return True

        home = self._presence_home()
        if home is False:  # everyone away
            if self.tracker.away_since is None:
                await self.tracker.async_set_away_since(now.isoformat())
            grace = self._opt_int(OPT_AWAY_GRACE_MIN, DEFAULT_AWAY_GRACE_MIN)
            since = _parse_iso(self.tracker.away_since)
            if since is None:
                return False
            return (now - since).total_seconds() >= grace * 60
        # someone home, or presence unknown → not away
        await self.tracker.async_set_away_since(None)
        return False

    # --------------------------------------------------------------- dispatch
    async def _attempt_dispatch(self, decision, now, now_min, away_ok) -> None:
        today_iso = now.date().isoformat()
        segments = list(decision.segments)

        # a) Presence gate (manual runs pass away_ok=True from the caller).
        #    Nothing with side effects may run before the gates: the door check
        #    used to run first, re-striking and re-notifying blocked rooms once
        #    per tick for as long as the run stayed gated here.
        if not away_ok:
            self._set_status("waiting", "waiting for everyone to leave")
            return

        # b) Window + battery + station + robot-idle gates.
        gates_ok, reason = self._start_gates_ok(now_min)
        if not gates_ok:
            self._set_status("waiting", reason)
            return

        # c) Door reachability — drop closed-door rooms. Side effects are safe
        #    now: we dispatch or consume the day immediately below, so this runs
        #    once per decision, not once per tick.
        reachable, blocked = [], []
        for seg in segments:
            if clean_guards.room_reachable(self._door_state(seg)):
                reachable.append(seg)
            else:
                blocked.append(seg)
        if blocked:
            await self.tracker.async_mark_unreachable(blocked)
            if bool(self._opt(OPT_NOTIFY_SKIPPED, DEFAULT_NOTIFY_SKIPPED)):
                names = ", ".join(self._room_name(s) for s in blocked)
                await self._notify(
                    "Vacuum: rooms skipped",
                    f"Door closed — skipped {names}. Will retry on the catch-up day.",
                )

        if not reachable:
            await self._mark_dispatched(decision.kind, today_iso)
            self._set_status("idle", "all due rooms unreachable (doors closed)")
            return

        # d) All clear — go.
        await self._dispatch_clean(reachable, decision.kind, now)
        await self._mark_dispatched(decision.kind, today_iso)

    def _start_gates_ok(self, now_min: int) -> tuple[bool, str]:
        """Window + battery + station preconditions for STARTING a clean.
        Presence and door reachability are handled by the caller."""
        win = clean_window.evaluate(
            now_min,
            start=self._opt(OPT_WINDOW_START, DEFAULT_WINDOW_START),
            end=self._opt(OPT_WINDOW_END, DEFAULT_WINDOW_END),
            enabled=bool(self._opt(OPT_WINDOW_ENABLED, DEFAULT_WINDOW_ENABLED)),
            overrun=bool(self._opt(OPT_WINDOW_OVERRUN, DEFAULT_WINDOW_OVERRUN)),
            already_cleaning=False,
        )
        if not win["allow_start"]:
            return False, "outside the allowed cleaning window"
        if self._robot_busy():
            return False, "robot is busy with another task"
        if not clean_guards.battery_ok(self._battery(), self._opt_int(OPT_MIN_BATTERY, DEFAULT_MIN_BATTERY)):
            return False, "battery too low to start"
        ready, reasons = clean_guards.station_ready(
            dust_bag=self._sval(entity_of("sensor", self._prefix, SUF_DUST_BAG))
            if self._opt(OPT_GUARD_DUSTBIN, DEFAULT_GUARD_DUSTBIN) else None,
            clean_water=self._sval(entity_of("sensor", self._prefix, SUF_CLEAN_WATER))
            if self._opt(OPT_GUARD_WATER, DEFAULT_GUARD_WATER) else None,
            dirty_water=self._sval(entity_of("sensor", self._prefix, SUF_DIRTY_WATER))
            if self._opt(OPT_GUARD_WATER, DEFAULT_GUARD_WATER) else None,
            error_active=self._error_active(),
        )
        if not ready:
            return False, "station needs attention: " + ", ".join(reasons)
        return True, "ok"

    async def _mark_dispatched(self, kind: str, today_iso: str) -> None:
        if kind == "catchup":
            await self.tracker.async_set_catchup_dispatched(today_iso)
        else:
            await self.tracker.async_set_day_dispatched(today_iso)

    def _pick_seq_mode(self) -> str | None:
        """Best available global sweep-then-mop mode for this vacuum, or None if
        it exposes neither (run then just uses whatever global mode is set)."""
        st = self.hass.states.get(entity_of("select", self._prefix, SUF_CLEANING_MODE))
        options = list(st.attributes.get("options", [])) if st else []
        return next((m for m in SEQ_MOP_MODES if m in options), None)

    async def _dispatch_clean(self, segments: list[str], kind: str, now: datetime,
                              quiet: bool = False) -> None:
        """Apply per-room settings (via customized cleaning) then start the
        segment clean. Every service call is best-effort so a model missing one
        of the optional per-room entities still cleans. ``quiet`` forces the
        configured quiet suction on every room (for cleaning while home)."""
        seg_names = {str(s): self._room_name(s) for s in segments}
        quiet_suction = self._opt(OPT_QUIET_SUCTION, "")

        # Claim the run BEFORE any service call — a concurrent evaluation must
        # see active_run set, or both dispatch. count_at_start and the newest-
        # record baseline are read here, before the robot can react, so a record
        # from a PREVIOUS task can never score this run.
        await self.tracker.async_set_active_run({
            "kind": kind,
            "segments": [str(s) for s in segments],
            "seg_names": seg_names,
            "started": now.isoformat(),
            "count_at_start": self._cleaning_count(),
            "record_ts_at_start": self._newest_record_ts(),
            "visited": [],
            "seen_active": False,
            "notified_stuck": False,
        })

        default_suction = self._opt(OPT_DEFAULT_SUCTION, "")
        deep = bool(self._opt(OPT_VACUUM_BEFORE_MOP, DEFAULT_VACUUM_BEFORE_MOP)) and not quiet

        if deep:
            # Vacuum-before-mop: sweep the whole area, THEN mop it (no smearing).
            # Uses the GLOBAL sequential mode, so per-room modes/suction don't
            # apply this run — the robot does one sweep-then-mop pass over all
            # target rooms.
            seq = self._pick_seq_mode()
            await self._svc("switch", "turn_off",
                            {"entity_id": entity_of("switch", self._prefix, SUF_CUSTOMIZED)})
            if seq:
                await self._select(entity_of("select", self._prefix, SUF_CLEANING_MODE), seq)
            if default_suction:
                await self._select(entity_of("select", self._prefix, SUF_SUCTION), default_suction)
        else:
            await self._svc("switch", "turn_on",
                            {"entity_id": entity_of("switch", self._prefix, SUF_CUSTOMIZED)})
            rooms = self._rooms()
            default_mode = self._opt(OPT_DEFAULT_MODE, "")
            for seg in segments:
                cfg = rooms.get(seg, {})
                mode = cfg.get(ROOM_MODE) or default_mode
                suction = (quiet_suction if quiet and quiet_suction
                           else (cfg.get(ROOM_SUCTION) or default_suction))
                wetness = cfg.get(ROOM_WETNESS)
                if mode:
                    await self._select(room_entity("select", self._prefix, seg, "cleaning_mode"), mode)
                if suction:
                    await self._select(room_entity("select", self._prefix, seg, "suction_level"), suction)
                if wetness not in (None, ""):
                    await self._svc("number", "set_value", {
                        "entity_id": room_entity("number", self._prefix, seg, "wetness_level"),
                        "value": wetness,
                    })

        int_segments = [int(s) for s in segments if str(s).isdigit()]
        await self._svc(DREAME_DOMAIN, SERVICE_CLEAN_SEGMENT, {
            "entity_id": self._vacuum_entity,
            "segments": int_segments,
        })
        self._set_status("running", f"{kind}: cleaning {', '.join(seg_names.values())}")
        _LOGGER.info("dreame_scheduler: dispatched %s clean of segments %s", kind, int_segments)

    # ------------------------------------------------------- run verification
    async def _check_active_run(self, now: datetime, away_ok: bool) -> None:
        run = self.tracker.active_run
        if run is None:
            return

        # Robot entities not loaded (HA just restarted / integration reloading):
        # every signal reads unavailable, which is indistinguishable from
        # "parked, no counter" and used to finalise the run as failed within
        # ~60 s of boot while the robot was still out cleaning. Hold everything
        # until the vacuum entity reports a real state.
        if self._vacuum_state() is None:
            self._set_status("waiting", "waiting for the robot's entities to load")
            return

        # (beta) Map-resume: the task was suspended at the dock when someone came
        # home. Resume only the un-cleaned area via vacuum.start once the house is
        # empty again; never finalise while suspended.
        if run.get("suspended"):
            # Watchdog: a presence entity wedged at "home" must not freeze the
            # scheduler forever (no dispatch happens while a run is active).
            started = _parse_iso(run.get("started"))
            if started and (now - started).total_seconds() > SUSPEND_MAX_SECONDS:
                await self._finalize_run(run, now, record=None, interrupted=True)
                return
            if away_ok:
                await self._svc("vacuum", "start", {"entity_id": self._vacuum_entity})
                run["suspended"] = False
                await self.tracker.async_set_active_run(run)
                self._set_status("running", "resuming the un-cleaned area")
                await self._notify(
                    "Vacuum resuming",
                    "House is empty again — continuing where it left off (un-cleaned area only).",
                )
            else:
                # Someone's still home. The robot firmware can auto-resume the
                # paused segment task on its own, so keep ENFORCING the dock
                # rather than passively waiting — otherwise it escapes and cleans
                # while they're home, and can get stuck with no alert (exactly
                # what happened live 2026-07-08).
                if (self._vacuum_state() or "") == "cleaning":
                    await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
                    self._set_status("returning", "someone home — sending back to the dock")
                else:
                    self._set_status("waiting", "paused (someone home) — will resume when empty")
                # Stuck while suspended? The normal stuck-notify lives past this
                # early return, so it never fires for a suspended run — alert here.
                if self._error_active() and not run.get("notified_stuck"):
                    run["notified_stuck"] = True
                    await self.tracker.async_set_active_run(run)
                    if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
                        await self._notify(
                            "⚠️ Vacuum needs help",
                            f"The robot errored near {where} while paused for someone being home.",
                            high_priority=True,
                        )
            return

        vstate = self._vacuum_state() or ""
        status = (self._sval(entity_of("sensor", self._prefix, SUF_STATUS)) or "").lower()
        task = (self._sval(entity_of("sensor", self._prefix, SUF_TASK_STATUS)) or "").lower()
        # "Actually cleaning" per the robot's own signals. The vacuum entity
        # state can briefly read docked/idle while task/status still say
        # room_cleaning (mop fetch, base visits), so trust all three — otherwise
        # seen_active can stay False through a whole run and the no-counter
        # fallback misclassifies it as failed_start.
        cleaning_active = (
            vstate in _ACTIVE_STATES
            or any(w in task for w in _CLEANING_ACTIVE_WORDS)
            or any(w in status for w in _CLEANING_ACTIVE_WORDS)
        )

        # Note that the robot actually started working.
        if cleaning_active and not run.get("seen_active"):
            run["seen_active"] = True
            await self.tracker.async_set_active_run(run)

        # An 'interrupting' run was already sent home — but the firmware can
        # auto-resume the paused segment task on its own (seen live 2026-07-08).
        # Keep enforcing the dock while someone is home, mirroring the
        # suspended-branch enforcement; without this the presence check below
        # (which skips interrupting runs) never sends it back.
        if (run.get("interrupting") and vstate == "cleaning"
                and self._presence_home() is True):
            await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            self._set_status("returning", "someone home — sending back to the dock")
            return

        # Someone came home mid-run → retreat to the dock so we don't annoy them.
        # (Manual runs opt out via kind == "manual".)
        if (run.get("kind") != "manual" and not run.get("interrupting")
                and bool(self._opt(OPT_RETURN_ON_ARRIVAL, DEFAULT_RETURN_ON_ARRIVAL))
                and bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))
                and self._presence_home() is True):
            map_resume = bool(self._opt(OPT_MAP_RESUME, DEFAULT_MAP_RESUME))
            await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            await self._notify(
                "Vacuum stepping aside — someone's home",
                "Returning to the dock to stay out of your way. It'll "
                + ("continue the un-cleaned area" if map_resume else "finish the remaining rooms")
                + " once everyone's out again.",
            )
            if map_resume:
                # Suspend the breakpoint-resume task — don't finalise; resume the
                # un-cleaned area later via vacuum.start.
                run["suspended"] = True
                await self.tracker.async_set_active_run(run)
                self._set_status("waiting", "someone home — paused (will resume un-cleaned area)")
            else:
                # Whole-room resume: mark interrupting so the metric-based
                # completion finalises it, then the un-done rooms re-dispatch from
                # the resume queue once empty. DON'T finalise here (the robot is
                # still moving) so room banking stays accurate.
                run["interrupting"] = True
                await self.tracker.async_set_active_run(run)
                self._set_status("returning", "someone home — returning to dock")
            return

        # Stuck? Try to unstick it and carry on (wall off the spot) before we
        # fall through to just flagging it errored. Takes precedence over the
        # completion checks while a recovery is in flight.
        if await self._maybe_auto_recover(run, now):
            return

        # Stuck/trapped mid-run → remember it and notify once. The error state
        # frequently auto-clears once the robot gives up and returns to the dock,
        # so a finalize-time check misses it — stick an `errored` flag on the run
        # now so a room the robot never actually reached isn't later credited as
        # a trusted full pass.
        if self._error_active():
            changed = False
            if not run.get("errored"):
                run["errored"] = True
                changed = True
            # Only cry "needs help" once auto-recover has genuinely given up (it's
            # off, or all attempts are spent). While it still has attempts the
            # recovering notice covers it -- a needs-help here just because one
            # free-timeout elapsed is a false alarm (reverse-out often frees it
            # shortly after). Verified live 2026-07-13: a double-notify (recover +
            # needs-help) fired for one wedge that then self-healed fine.
            recover_done = (not bool(self._opt(OPT_AUTO_RECOVER, DEFAULT_AUTO_RECOVER))
                            or int(run.get("recover_count", 0)) >= MAX_RECOVER_ATTEMPTS)
            if recover_done and not run.get("notified_stuck"):
                run["notified_stuck"] = True
                changed = True
                if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                    where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
                    await self._notify(
                        "⚠️ Vacuum needs help",
                        f"The robot reported an error near {where} during the {run['kind']} clean.",
                        high_priority=True,
                    )
            if changed:
                await self.tracker.async_set_active_run(run)

        # --- Completion, driven by the robot's own metric, not a timer ---
        # A new cleaning-history record (cleaning_count increments) is the
        # definitive "task finished" signal — a small room can finish in
        # seconds, and the robot docks mid-task to fetch/wash the mop, so
        # elapsed time and a bare "docked" state are both unreliable.
        count = self._cleaning_count()
        start_count = run.get("count_at_start")
        if count is not None and start_count is not None and count > start_count:
            # The counter also ticks when the robot docks MID-task (mop wash,
            # recharge) and logs a partial session before heading back out — a
            # tick alone is not the end of the task. While the robot is still
            # (or again) working, keep the run open. (Live 2026-07-09: the run
            # was finalised at 10:02 while the robot was starting its second
            # session, mis-marking the remaining rooms as skipped.)
            # (Interrupted runs that have physically docked skip this hold —
            # lingering "paused" task words would otherwise keep them open.)
            if cleaning_active and not (run.get("interrupting") and vstate in _PARKED_STATES):
                if run.get("parked_since") or run.get("completed_at"):
                    run["parked_since"] = None
                    run["completed_at"] = None
                    await self.tracker.async_set_active_run(run)
                self._set_status("running", f"{run['kind']} clean in progress")
                return
            # The counter ticks a beat before the matching record syncs, so
            # "newest record" here can be the PREVIOUS run's (_run_record also
            # rejects records that already existed at dispatch). Only finalise
            # once this run's record is available; if it hasn't landed within
            # RECORD_SYNC_WAIT_SECONDS, give up and finalise with record=None
            # (best-effort: unseen rooms stay pending).
            record = self._run_record(run)
            if record is None:
                if not run.get("completed_at"):
                    run["completed_at"] = now.isoformat()
                    await self.tracker.async_set_active_run(run)
                completed = _parse_iso(run.get("completed_at"))
                waited = (now - completed).total_seconds() if completed else 0
                if waited < RECORD_SYNC_WAIT_SECONDS:
                    self._set_status("running", f"{run['kind']} wrapping up (syncing map)")
                    return
            # completed=False → a partial session of a task the robot means to
            # resume. Hold the run open through a long parked dwell (extended
            # while it's recharging mid-task); a resume clears parked_since
            # above, and the final session's completed=True record finalises
            # immediately as before. Interrupted runs skip the hold — we sent
            # the robot home; it is not going to resume.
            if (record is not None and record.get("completed") is False
                    and not run.get("interrupting")):
                if not run.get("parked_since"):
                    run["parked_since"] = now.isoformat()
                    await self.tracker.async_set_active_run(run)
                parked = _parse_iso(run.get("parked_since"))
                dwell = (now - parked).total_seconds() if parked else 0
                if dwell < INCOMPLETE_RECORD_DWELL_SECONDS or self._charging_mid_task():
                    self._set_status(
                        "running",
                        f"{run['kind']} paused at the dock — waiting for the robot to resume",
                    )
                    return
            await self._finalize_run(run, now, record=record,
                                     interrupted=bool(run.get("interrupting")))
            return

        started = _parse_iso(run.get("started"))
        elapsed = (now - started).total_seconds() if started else 0

        # Fallbacks when the counter never moves (aborted / odd firmware / the
        # counter sensor unavailable). cleaning_active was computed above.
        if cleaning_active:
            if run.get("parked_since") or run.get("completed_at"):
                run["parked_since"] = None
                run["completed_at"] = None
                await self.tracker.async_set_active_run(run)
            self._set_status("running", f"{run['kind']} clean in progress")
            return

        # Parked and not in any cleaning/mop-fetch state, yet no counter tick —
        # dwell briefly then finalise (best-effort coverage), or declare
        # failed-to-start if it never became active.
        if not run.get("parked_since"):
            run["parked_since"] = now.isoformat()
            await self.tracker.async_set_active_run(run)
        parked = _parse_iso(run.get("parked_since"))
        dwell = (now - parked).total_seconds() if parked else 0
        if run.get("seen_active") and dwell >= COMPLETE_DWELL_SECONDS:
            # Counter lagging or unavailable — check the record anyway. A
            # completed=False record is a mid-task dock (wash/recharge), not
            # the end: give it the long dwell rather than 45 s.
            record = self._run_record(run)
            if (record is not None and record.get("completed") is False
                    and not run.get("interrupting")
                    and (dwell < INCOMPLETE_RECORD_DWELL_SECONDS
                         or self._charging_mid_task())):
                self._set_status(
                    "running",
                    f"{run['kind']} paused at the dock — waiting for the robot to resume",
                )
                return
            await self._finalize_run(run, now, record=record,
                                     interrupted=bool(run.get("interrupting")))
        elif (not run.get("seen_active")) and elapsed > 300:
            await self._finalize_run(run, now, failed_start=True)
        else:
            self._set_status("running", f"{run['kind']} finishing up")

    async def _finalize_run(self, run: dict, now: datetime, *, record: dict | None = None,
                            failed_start: bool = False, interrupted: bool = False) -> None:
        targets = [str(s) for s in run.get("segments", [])]
        seg_names = run.get("seg_names", {})
        ts = now.isoformat()

        # Remove any temporary no-go boxes we dropped to recover from a wedge —
        # they were for this run only (the furniture that caused it may move).
        await self._clear_temp_nogos(run)

        # blocked_rooms from the completion record is the map-derived truth of
        # what couldn't be reached (door/obstacle), keyed by segment id.
        blocked_reasons: dict[str, str] = {}
        if record and isinstance(record.get("blocked_rooms"), dict):
            blocked_reasons = {str(k): v for k, v in record["blocked_rooms"].items()}
        blocked = set(blocked_reasons)

        # A room is banked as cleaned only when we can TRUST it — never from the
        # robot's "Task completed" text (it reports that even on a manual return-
        # to-base). We trust: a normal, error-free, non-interrupted full pass
        # (rooms not in the map's blocked list), or a room we actually saw the
        # robot enter. An aborted / errored / interrupted run credits only what
        # we can confirm; everything else stays pending and gets retried.
        cleaned, skipped = [], []
        visited = set(run.get("visited", []))
        error_active = self._error_active()
        # Area sanity: a run that swept an implausibly small total area didn't
        # really clean, even if the record says "completed" (e.g. it left the
        # dock, cleaned ~1 sq m, and returned). Don't bank rooms on the trusted-
        # pass path in that case; require we actually saw the robot enter them.
        run_area = _parse_area(record.get("cleaned_area") if record else None)
        too_small = run_area is not None and run_area < max(1.0, len(targets) * MIN_AREA_PER_ROOM_M2)
        if too_small:
            _LOGGER.debug("run swept only %.1f sq m for %d rooms; not crediting the trusted-pass path", run_area, len(targets))
        for seg in targets:
            name = seg_names.get(seg)
            seen = bool(visited and name and name in visited)
            if failed_start:
                skipped.append(seg)
            elif seg in blocked:
                skipped.append(seg)                     # map says door/obstacle
            elif interrupted:
                (cleaned if seen else skipped).append(seg)
            elif (record is not None and record.get("completed") is not False
                    and not error_active and not run.get("errored") and not too_small):
                cleaned.append(seg)                     # trusted full pass
            elif seen:
                cleaned.append(seg)                     # abnormal end, but seen
            else:
                skipped.append(seg)                     # unconfirmed -> pending

        if cleaned:
            await self.tracker.async_mark_cleaned(cleaned, ts)

        # Interrupted: the un-done rooms are deferred, not failures — queue them.
        if interrupted:
            if skipped and bool(self._opt(OPT_RESUME_WHEN_AWAY, DEFAULT_RESUME_WHEN_AWAY)):
                await self.tracker.async_set_resume(
                    {"segments": skipped, "kind": run.get("kind", "daily")}
                )
            await self.tracker.async_set_last_run({
                "kind": run.get("kind"), "finished": ts, "interrupted": True,
                "cleaned": [seg_names.get(s, s) for s in cleaned],
                "remaining": [seg_names.get(s, s) for s in skipped],
            })
            await self.tracker.async_set_active_run(None)
            self._set_status("waiting", "paused (someone home) — will resume when empty")
            return

        # Normal end: skipped rooms stay pending; notify WITH the reason.
        if skipped:
            await self.tracker.async_mark_unreachable(skipped)
            if bool(self._opt(OPT_NOTIFY_SKIPPED, DEFAULT_NOTIFY_SKIPPED)):
                parts = [
                    seg_names.get(s, f"Room {s}")
                    + (f" ({blocked_reasons[s]})" if s in blocked_reasons else "")
                    for s in skipped
                ]
                await self._notify(
                    "Vacuum: rooms not finished",
                    "Skipped " + ", ".join(parts) + " — kept pending for the weekly catch-up.",
                )

        area = record.get("cleaned_area") if record else None
        await self.tracker.async_set_last_run({
            "kind": run["kind"], "finished": ts,
            "cleaned": [seg_names.get(s, s) for s in cleaned],
            "skipped": [seg_names.get(s, s) for s in skipped],
            "cleaned_area": area,
        })

        # Append to the run-history log (fail-trend / weekday analytics). Only
        # normal ends are logged — interrupted runs returned above, so a room
        # deferred by "someone came home" never counts as a fail.
        cfg_rooms = self._rooms()
        default_mode = self._opt(OPT_DEFAULT_MODE, "")
        per_room: dict = {}
        for seg in cleaned:
            per_room[seg] = {"status": "cleaned",
                             "mode": cfg_rooms.get(seg, {}).get(ROOM_MODE) or default_mode or None}
        for seg in skipped:
            per_room[seg] = {"status": "failed" if failed_start else "skipped",
                             "reason": blocked_reasons.get(seg)}
        if per_room:
            await self.tracker.async_append_history({
                "ts": ts,
                "weekday": now.weekday(),
                "kind": run.get("kind"),
                "per_room": per_room,
            })

        await self.tracker.async_set_active_run(None)
        self._set_status("idle", f"finished {run['kind']} clean")
        _LOGGER.info("dreame_scheduler: %s run done — cleaned %s, skipped %s (area %s)",
                     run["kind"], cleaned, skipped, area)

    async def _maybe_weekly_notice(self, summary: dict) -> None:
        if not bool(self._opt(OPT_NOTIFY_WEEKLY, DEFAULT_NOTIFY_WEEKLY)):
            return
        # First-ever rollover (no previous week recorded) — nothing to report.
        if not summary.get("week_start"):
            return
        rooms = self._rooms()
        all_segs = all_enabled_segments(rooms)
        done = set((summary.get("cleaned") or {}).keys())
        missed = [s for s in all_segs if s not in done]
        if not all_segs:
            return
        n_done = len(all_segs) - len(missed)
        if missed:
            # Fail counts for the finished week (snapshot taken before reset), so
            # "why" context is week-accurate without touching the map camera.
            unreach = summary.get("unreachable") or {}

            def _line(seg: str) -> str:
                name = self._room_name(seg)
                c = int(unreach.get(seg, 0))
                return f"{name} ({c}× blocked)" if c else name

            names = ", ".join(_line(s) for s in missed)
            await self._notify(
                "Weekly vacuum summary",
                f"{n_done}/{len(all_segs)} rooms cleaned this week. "
                f"Never finished: {names}.",
            )
        else:
            await self._notify(
                "Weekly vacuum summary",
                f"✅ All {len(all_segs)} rooms were cleaned at least once this week.",
            )

    # ----------------------------------------------------- resume + nudge
    async def _try_resume(self, now: datetime, now_min: int, away_ok: bool) -> bool:
        """Finish an interrupted run's remaining rooms once the house is empty
        again. Returns True if it handled the tick (dispatched or is waiting)."""
        resume = self.tracker.resume
        pend = set(pending_rooms(self._rooms(), self.tracker.cleaned))
        segs = [s for s in resume.get("segments", []) if s in pend]
        if not segs:
            await self.tracker.async_set_resume(None)
            return False
        if not away_ok:
            return False  # still someone home — keep the queue, wait
        reachable = [s for s in segs if clean_guards.room_reachable(self._door_state(s))]
        gates_ok, reason = self._start_gates_ok(now_min)
        if reachable and gates_ok:
            await self._dispatch_clean(reachable, resume.get("kind", "daily"), now)
            await self.tracker.async_set_resume(None)
            return True
        # Not dispatchable right now (doors closed / a gate failing). Keep the
        # queue but DON'T claim the tick — returning True here starved the daily
        # and catch-up schedules for as long as one queued room stayed blocked.
        why = reason if not gates_ok else "doors closed"
        self._set_status("waiting", f"waiting to resume remaining rooms ({why})")
        return False

    async def _maybe_stale_nudge(self, now: datetime, away_ok: bool) -> None:
        """When the house can't be cleaned because people are always home, and
        it's been too long, nudge the user (once/day) to clean while home."""
        if not bool(self._opt(OPT_STALE_NUDGE_ENABLED, DEFAULT_STALE_NUDGE_ENABLED)):
            return
        if away_ok:
            return  # it can (or soon will) run on its own — no nudge needed
        if not all_enabled_segments(self._rooms()):
            return
        # Only nudge during sensible hours: the user's cleaning window if one is
        # set, otherwise a daytime default. Without this the once-per-day counter
        # re-arms at 00:00 and fires an overnight "shall I clean?" ping.
        now_min = now.hour * 60 + now.minute
        win_start = win_end = None
        if bool(self._opt(OPT_WINDOW_ENABLED, DEFAULT_WINDOW_ENABLED)):
            win_start = clean_window.to_minutes(self._opt(OPT_WINDOW_START, DEFAULT_WINDOW_START))
            win_end = clean_window.to_minutes(self._opt(OPT_WINDOW_END, DEFAULT_WINDOW_END))
        if win_start is None or win_end is None:
            win_start, win_end = 8 * 60, 20 * 60   # daytime default 08:00-20:00
        if not clean_window.in_window(now_min, win_start, win_end):
            return
        today_iso = now.date().isoformat()
        if self.tracker.nudged_on == today_iso:
            return
        days = self._opt_int(OPT_STALE_AFTER_DAYS, DEFAULT_STALE_AFTER_DAYS)
        last = _parse_iso(self.tracker.last_clean)
        stale = last is None or (now - last).total_seconds() >= days * 86400
        if not stale:
            return
        await self.tracker.async_set_nudged_on(today_iso)
        how_long = "a while" if last is None else f"{int((now - last).total_seconds() // 86400)} days"
        await self._notify(
            "🧹 Vacuum: shall I clean?",
            f"The house hasn't been cleaned in {how_long} and someone's usually "
            "home. Tap 'Clean now' (or 'Clean now – quiet') on the dashboard to run it while you're in.",
        )

    # --------------------------------------------------------- manual actions
    async def async_run_scheduled_now(self, quiet: bool = False) -> None:
        """Manual: run today's scheduled rooms now, bypassing presence/window/
        time gates but still respecting door + station + error guards."""
        async with self._eval_lock:
            now = dt_util.now()
            due = [s for s in rooms_due_today(self._rooms(), now.weekday())
                   if s not in self.tracker.cleaned]
            await self._manual_dispatch(due, "manual", now, quiet)

    async def async_run_catchup_now(self, quiet: bool = False) -> None:
        """Manual: run everything still pending this week, now."""
        async with self._eval_lock:
            now = dt_util.now()
            pend = pending_rooms(self._rooms(), self.tracker.cleaned)
            await self._manual_dispatch(pend, "manual", now, quiet)

    async def async_clean_rooms(self, segments, quiet: bool = False) -> None:
        """Manual: clean a specific set of rooms NOW (tap-a-room on the plan).
        A user override — it drops any in-flight run first (e.g. a scheduled run
        that presence has paused), then dispatches as a ``manual`` run, which
        bypasses the presence/window/time gates AND is exempt from the
        return-on-arrival dock. So 'someone home' no longer fights it."""
        async with self._eval_lock:
            now = dt_util.now()
            segs = [str(s) for s in (segments or [])]
            if not segs:
                self._set_status("idle", "no rooms selected to clean")
                return
            old = self.tracker.active_run
            if old is not None:
                # Overriding an in-flight run: remove its recovery no-go boxes
                # first — _finalize_run (which normally clears them) never runs
                # for a dropped run, so they'd stay on the map permanently.
                await self._clear_temp_nogos(old)
                await self.tracker.async_set_active_run(None)
            await self._manual_dispatch(segs, "manual", now, quiet)

    async def _manual_dispatch(self, segments, kind, now, quiet: bool = False) -> None:
        if self.tracker.active_run is not None:
            self._set_status("running", "a clean is already running")
            return
        if not segments:
            self._set_status("idle", "nothing to clean right now")
            return
        reachable = [s for s in segments if clean_guards.room_reachable(self._door_state(s))]
        if not reachable:
            self._set_status("idle", "all target rooms unreachable (doors closed)")
            return
        ready, reasons = clean_guards.station_ready(
            dust_bag=self._sval(entity_of("sensor", self._prefix, SUF_DUST_BAG)),
            clean_water=self._sval(entity_of("sensor", self._prefix, SUF_CLEAN_WATER)),
            dirty_water=self._sval(entity_of("sensor", self._prefix, SUF_DIRTY_WATER)),
            error_active=self._error_active(),
        )
        if not ready:
            self._set_status("waiting", "station needs attention: " + ", ".join(reasons))
            return
        await self._dispatch_clean(reachable, kind, now, quiet=quiet)

    async def async_reset_week(self) -> None:
        now = dt_util.now()
        week_start_day = self._opt_int(OPT_WEEK_START_DAY, DEFAULT_WEEK_START_DAY)
        await self.tracker.async_reset_week(week_start_for(now.date(), week_start_day).isoformat())
        self._set_status(self._status.get("state", "idle"), "week counters reset")

    async def async_set_enabled(self, value: bool) -> None:
        await self.tracker.async_set_enabled(value)
        await self._tick()

    # ----------------------------------------------------------- status/notify
    def _idle_reason(self, now: datetime, away_ok: bool) -> str:
        rooms = self._rooms()
        if not all_enabled_segments(rooms):
            return "no rooms configured yet"
        due = rooms_due_today(rooms, now.weekday())
        if not away_ok and self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY):
            return "waiting for the house to be empty"
        if due:
            return f"{len(due)} room(s) scheduled today"
        return "no rooms scheduled today"

    def next_run(self, now: datetime) -> datetime | None:
        """Next datetime the daily schedule fires: soonest upcoming day (today
        included if its time hasn't passed) with any enabled room scheduled for
        that weekday, at the configured daily time. Shared by the report + the
        status snapshot so the GUI and the custom card agree."""
        rooms_cfg = self._rooms()
        try:
            hh, mm = (int(x) for x in str(self._opt(OPT_DAILY_TIME, DEFAULT_DAILY_TIME)).split(":", 1))
        except (ValueError, AttributeError):
            hh, mm = 9, 0
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            wd = day.weekday()
            has = any(
                bool(c.get(ROOM_ENABLED)) and wd in (c.get(ROOM_DAYS) or [])
                for c in rooms_cfg.values() if isinstance(c, dict)
            )
            if not has:
                continue
            run_at = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if offset == 0 and now >= run_at:
                continue
            return run_at
        return None

    def robot_snapshot(self) -> dict:
        """Live robot state for the Robot-status sensor attribute / card."""
        p = self._prefix
        return {
            "vacuum_state": self._vacuum_state(),
            "status": self._sval(entity_of("sensor", p, SUF_STATUS)),
            "battery": self._battery(),
            "error": self._sval(entity_of("sensor", p, SUF_ERROR)),
            "current_room": self._sval(entity_of("sensor", p, SUF_CURRENT_ROOM)),
            "dust_bag": self._sval(entity_of("sensor", p, SUF_DUST_BAG)),
            "clean_water": self._sval(entity_of("sensor", p, SUF_CLEAN_WATER)),
            "dirty_water": self._sval(entity_of("sensor", p, SUF_DIRTY_WATER)),
        }

    def status_snapshot(self) -> dict:
        """Rich status for the sensor entities (built fresh each read)."""
        rooms = self._rooms()
        all_segs = all_enabled_segments(rooms)
        cleaned = list(self.tracker.cleaned.keys())
        pend = pending_rooms(rooms, self.tracker.cleaned)
        cleaned_names = [self._room_name(s) for s in cleaned if s in all_segs]
        pend_names = [self._room_name(s) for s in pend]
        missed = {self._room_name(s): int(n) for s, n in self.tracker.unreachable.items()}

        # Dashboard-ready one/two-line summary (drop into a Markdown card, no
        # add-on GUI needed).
        bits = [f"✅ {len(cleaned_names)} cleaned", f"🟡 {len(pend_names)} pending"]
        if missed:
            bits.append(f"🔴 {len(missed)} missed")
        week_summary = "This week — " + " · ".join(bits)
        if missed:
            week_summary += "\nMissed: " + ", ".join(f"{n} ({c}×)" for n, c in missed.items())

        now = dt_util.now()
        nxt = self.next_run(now)
        presence_home = self._presence_home()

        return {
            **self._status,
            "enabled": self.tracker.enabled,
            "week_start": self.tracker.week_start,
            "rooms_total": len(all_segs),
            "rooms_cleaned": len(cleaned_names),
            "cleaned_rooms": cleaned_names,
            "pending_rooms": pend_names,
            "missed_rooms": missed,               # room name -> times blocked this week
            "unreachable": missed,                # kept for backward-compat
            "week_summary": week_summary,
            "last_run": self.tracker.last_run,
            "active": self.tracker.active_run,
            # Robot + presence/next-run summary — surfaced as sensor attributes
            # so the custom Lovelace card can render them from the one entity.
            "robot": self.robot_snapshot(),
            "presence_home": presence_home,
            "presence_configured": bool(self._presence_entities()),
            "next_run": nxt.isoformat() if nxt else None,
            "next_run_day": WEEKDAYS[nxt.weekday()] if nxt else None,
            "next_run_time": nxt.strftime("%H:%M") if nxt else None,
        }

    def _set_status(self, state: str, reason: str) -> None:
        self._status = {"state": state, "reason": reason, "enabled": self.tracker.enabled}
        if self._notify_update:
            self._notify_update()

    async def _notify(self, title: str, message: str, high_priority: bool = False) -> None:
        # high_priority asks the mobile app to bypass Android Doze / iOS batching
        # so a stuck/needs-help alert arrives now, not 30+ min later. Harmless on
        # persistent_notification, which just ignores the extra data.
        extra = {}
        if high_priority:
            extra = {"data": {"ttl": 0, "priority": "high",
                              "push": {"interruption-level": "time-sensitive"}}}
        for name in self._notify_names():
            try:
                if name == "persistent_notification":
                    await self.hass.services.async_call(
                        "persistent_notification", "create",
                        {"title": title, "message": message,
                         "notification_id": f"dreame_scheduler_{self.entry.entry_id}"},
                        blocking=False,
                    )
                else:
                    await self.hass.services.async_call(
                        "notify", name, {"title": title, "message": message, **extra}, blocking=False
                    )
            except Exception as err:  # noqa: BLE001 — never let a bad notify target break the run
                _LOGGER.warning("dreame_scheduler: notify '%s' failed: %s", name, err)

    async def _svc(self, domain: str, service: str, data: dict) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("dreame_scheduler: %s.%s failed (%s): %s", domain, service, data, err)

    async def _select(self, entity_id: str, option: str) -> None:
        if self.hass.states.get(entity_id) is None:
            return  # this model doesn't expose that per-room select — skip quietly
        await self._svc("select", "select_option", {"entity_id": entity_id, "option": option})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dt_util.parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_area(value) -> float | None:
    """Best-effort parse of a cleaned-area value like '12 m2' or 12.5 -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else None
