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
import math
import re
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import clean_guards, clean_window, history_analytics as hist_a, trap_learner
from .const import (
    CONF_PREFIX,
    CONF_VACUUM_ENTITY,
    DEFAULT_AWAY_GRACE_MIN,
    DEFAULT_PRESENCE_STALE_MIN,
    DEFAULT_CATCHUP_DAY,
    DEFAULT_CATCHUP_ENABLED,
    DEFAULT_CATCHUP_TIME,
    DEFAULT_OPPORTUNISTIC_CATCHUP,
    DEFAULT_HOLIDAY_ENABLED,
    DEFAULT_HOLIDAY_AFTER_DAYS,
    DEFAULT_DAILY_TIME,
    DEFAULT_GUARD_DUSTBIN,
    DEFAULT_GUARD_WATER,
    DEFAULT_MAP_RESUME,
    DEFAULT_MANUAL_CLEAN_ENABLED,
    DEFAULT_MANUAL_CLEAN_MIN_MISSES,
    DEFAULT_MANUAL_CLEAN_NOTIFY,
    DEFAULT_MANUAL_CLEAN_STALE_DAYS,
    DEFAULT_SHOW_UNREACHABLE,
    DEFAULT_DOOR_RETRY_ENABLED,
    DEFAULT_DOOR_RETRY_MIN,
    DEFAULT_DOOR_RETRY_WHILE_HOME,
    DEFAULT_MIN_BATTERY,
    DEFAULT_PRERUN_ENABLED,
    DEFAULT_PRERUN_LEAD_MIN,
    DEFAULT_PRERUN_MODE,
    DEFAULT_PRERUN_TIME,
    DEFAULT_VACUUM_BEFORE_MOP,
    DEFAULT_HONOR_NATIVE,
    DEFAULT_AUTO_RECOVER,
    DEFAULT_QUIET_RECOVERY,
    DEFAULT_ROOM_MOP_EVERY,
    DEFAULT_NOTIFY_SKIPPED,
    DEFAULT_NOTIFY_STUCK,
    DEFAULT_NOTIFY_WEEKLY,
    DEFAULT_CONSUMABLE_ALERT,
    DEFAULT_CONSUMABLE_THRESHOLD,
    DEFAULT_EDGE_ENABLED,
    DEFAULT_EDGE_EVERY_DAYS,
    DEFAULT_EDGE_TIME,
    DEFAULT_EDGE_PASSES,
    DEFAULT_EDGE_LEARN,
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
    OPT_OPPORTUNISTIC_CATCHUP,
    OPT_HOLIDAY_ENABLED,
    OPT_HOLIDAY_AFTER_DAYS,
    OPT_DAILY_TIME,
    OPT_DEFAULT_MODE,
    OPT_DEFAULT_SUCTION,
    OPT_GUARD_DUSTBIN,
    OPT_GUARD_WATER,
    OPT_MAP_RESUME,
    OPT_MIN_BATTERY,
    OPT_NOTIFY_SKIPPED,
    OPT_NOTIFY_STUCK,
    OPT_MANUAL_CLEAN_ENABLED,
    OPT_MANUAL_CLEAN_MIN_MISSES,
    OPT_MANUAL_CLEAN_NOTIFY,
    OPT_MANUAL_CLEAN_STALE_DAYS,
    OPT_NOTIFY_TARGETS,
    OPT_NOTIFY_WEEKLY,
    OPT_CONSUMABLE_ALERT,
    OPT_CONSUMABLE_THRESHOLD,
    OPT_EDGE_ENABLED,
    OPT_EDGE_EVERY_DAYS,
    OPT_EDGE_TIME,
    OPT_EDGE_PASSES,
    OPT_EDGE_LEARN,
    OPT_SHOW_UNREACHABLE,
    OPT_DOOR_RETRY_ENABLED,
    OPT_DOOR_RETRY_MIN,
    OPT_DOOR_RETRY_WHILE_HOME,
    OPT_PRERUN_ENABLED,
    OPT_PRERUN_LEAD_MIN,
    OPT_PRERUN_MODE,
    OPT_PRERUN_TIME,
    OPT_PRESENCE_ENTITIES,
    OPT_PRESENCE_STALE_MIN,
    OPT_QUIET_SUCTION,
    OPT_REQUIRE_AWAY,
    OPT_RESUME_WHEN_AWAY,
    OPT_RETURN_ON_ARRIVAL,
    OPT_ROOMS,
    OPT_STALE_AFTER_DAYS,
    OPT_STALE_NUDGE_ENABLED,
    OPT_VACUUM_BEFORE_MOP,
    OPT_HONOR_NATIVE,
    OPT_AUTO_RECOVER,
    OPT_QUIET_RECOVERY,
    OPT_WEEK_START_DAY,
    OPT_WINDOW_ENABLED,
    OPT_WINDOW_END,
    OPT_WINDOW_OVERRUN,
    OPT_WINDOW_START,
    ROOM_DAYS,
    ROOM_DOOR_SENSOR,
    ROOM_ENABLED,
    ROOM_MODE,
    ROOM_MOP_EVERY,
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
    SUF_VOLUME,
    TICK_SECONDS,
    WEEKDAYS,
    e as entity_of,
    room_entity,
)
from .consumables import CONSUMABLE_BY_KEY, CONSUMABLES, evaluate_consumables
from .scheduler import (
    advance_mop_cadence,
    all_enabled_segments,
    choose_dispatch,
    door_open_long_enough,
    edge_due,
    fold_room_area,
    evaluate_progress,
    is_mopping_mode,
    needs_week_rollover,
    pending_rooms,
    rooms_due_today,
    week_start_for,
    has_explicit_times,
    next_slot_minute,
    holiday_hold,
    tracker_stale,
    SWEEP_ONLY_MODE,
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
# Vacuum error substrings that mean "physically wedged but likely REVERSIBLE" —
# a straight reverse-out backs it off the trap and it carries on. Deliberately
# excludes the beach words below: a high-centred robot can't drive out (proven
# live 2026-07-15), so those get a hand-needed alert instead of a futile reverse.
_RECOVERABLE_ERROR_WORDS = (
    "suffocate", "stuck", "trap", "wheel", "bumper", "tangle",
    "edge", "route", "path",
)
# The robot stopped because something was IN ITS WAY — it is not physically
# stuck. The right response is the smallest one: just resume, and let it re-plan
# around whatever it is. A block is a MOMENT, not a property of the room, so we
# never reverse it, never wall it off, and never send it home — and one
# obstruction must never end the whole job. Live 2026-07-17: `blocked` twice in
# 20 min at different spots (faults 63 and 64); a plain vacuum.start carried it
# on both times, having cleared no obstacle at all.
_BLOCKED_ERROR_WORDS = ("blocked", "obstacle")
MAX_BLOCK_RESUMES = 5              # per run, before we stop nudging and ask for help
# How close a photographed obstacle must be to count as "what stopped it".
# Generous: on a BLOCK the robot halts clear of the thing (the obstacle is ahead
# on its path, not under its bumper), so its own position says little about where
# the cause is — but the nearest photo usually names it.
OBSTACLE_MATCH_MM = 2500
# Error substrings that mean the robot is BEACHED / high-centred — it climbed
# onto something and lifted its drive wheels off the floor. The cliff sensors
# then read a fall edge ('drop') and lock out all motion; every reverse/rotate
# returns 0 mm. No command frees it — it needs a manual lift-and-place.
_BEACH_ERROR_WORDS = (
    "drop", "cliff", "lifted", "lift", "tilt", "picked", "pick_up", "high",
)
# Hardware/motor faults a reverse-out CANNOT fix — a wheel-motor error means
# something's tangled/jammed in a wheel or the motor overloaded (live 2026-08-03:
# 'right_wheel_motor' on a rug with wet mop pads). Reversing achieves nothing and
# then mis-reads as "beached", so catch these first and ask for a physical check.
# A wheel-motor fault OR a wheel-speed fault (the drive wheel isn't turning at
# the commanded speed — slipping, jammed, or something wound round the axle). No
# manoeuvre clears either; both need a physical check. Both spellings of the
# speed fault seen in the wild are matched ('wheel_speed' and the robot's own
# 'wheell_speed', live 2026-08-11: recurring right-wheel faults). A plain 'wheel'
# stays in the recoverable list — only these specific faults are hardware.
_HARDWARE_ERROR_WORDS = ("wheel_motor", "wheel_speed", "wheell_speed")
# A DOCK/STATION setup fault — the robot can't get itself ready to clean at the
# dock, most commonly "mop install failed" (it couldn't mount the mop pads).
# No manoeuvre helps and the mop rooms can't run, so we don't reverse/retry — we
# tell the user to fix the station and END the run (live 2026-08-12: pebbles
# knocked off a plant were vacuumed up and jammed the mop-pad mount; the daily
# run then sat wedged open for hours on 'mop install failed').
_STATION_ERROR_WORDS = ("install", "mount")
# A cable / cord / cloth TANGLE (the robot reports it as a 'suffocate' — it can't
# advance). One gentle reverse is worth a try in case it's a loose cord it can
# back off, but repeating it just DRAGS the tangle around — and dragging counts
# as 'movement', so the reverse-moved-nothing escalation never catches it. So we
# cap tangles at a single reverse, then ask for a hand (live 2026-08-09: wrapped
# in bedroom cables; three reverses only dragged it and the user had to carry it
# back). These are a subset of the recoverable words, escalated sooner.
_TANGLE_ERROR_WORDS = ("suffocate", "tangle", "wrap")
TANGLE_MAX_REVERSE = 1             # reverse attempts on a tangle before asking for help
REVERSE_OUT_STEPS = 3              # remote-control reverse nudges to back off a trap
REVERSE_OUT_VELOCITY = -110       # straight reverse (negative), retracing the entry route
MAX_RECOVER_ATTEMPTS = 3            # per run, before giving up and docking
RECOVER_FREE_TIMEOUT = 90          # seconds to wait for it to free itself
NOGO_HALF_MM = 300                 # half-size of the temp no-go box around the stuck point (30 cm)
MIN_AREA_PER_ROOM_M2 = 2.0         # a run sweeping less than this per dispatched room didn't really clean
STUCK_MIN_ESCAPE_MM = 80           # a reverse-out that moved less than this achieved nothing
STUCK_NO_MOVE_LIMIT = 2            # consecutive no-move recoveries before we call it beached
# A ~350 mm robot needs real margin to plan through a gap. A recovery box that
# leaves less than this between itself and an existing zone builds a VIRTUAL
# PINCER the robot simply refuses to enter — live 2026-07-17 a temp box landed
# 426 mm from the rug no-go and stranded the robot in the channel for 15 min
# (located, 97% battery, no error, nothing physically touching it).
ROBOT_CLEARANCE_MM = 500
# A manual "clean now" is exempt from the return-on-arrival dock — the user asked
# for it, knowing they were home. That intent only goes STALE once the run has
# been going for HOURS (it docked to recharge and auto-resumed long after the ask).
# Live 2026-07-17: an 8-minute-old manual run briefly parked because it got STUCK,
# which flipped was_parked and had the engine fighting the user's own deliberate
# test run — dragging it home while they stood watching it.
MANUAL_INTENT_STALE_SECONDS = 2 * 3600
# Deep-'Sleeping' robots silently ignore clean_segment until woken (live
# 2026-07-15) — verify the dispatch actually started; if not, locate to wake and
# re-send. Bounded waits, so a dispatch never hangs the eval loop for long.
WAKE_VERIFY_SECONDS = 8            # after clean_segment, confirm it actually started
WAKE_SETTLE_SECONDS = 12          # after locate, max wait for it to leave 'Sleeping'
WAKE_POLL_SECONDS = 1.5
# Silent stuck: the robot claims to be cleaning but hasn't moved for this long,
# with NO error to trigger auto-recover ('unable to reach', a quiet high-centre).
SILENT_STUCK_SECONDS = 360
# Recovery grace: a "needs help" alert isn't sent the instant a threshold trips —
# it's held this long first. If the robot is CLEANING again before it elapses (the
# app sorted it out: a resume/re-plan carried it on), the alert is cancelled
# silently, killing the false positive. A genuine stuck never resumes, so its
# alert still fires — just this many seconds later.
HELP_GRACE_SECONDS = 90
# STRANDED: no run in flight, yet the robot is sat away from its dock, not
# moving. _watch_silent_stuck only runs while a run is ACTIVE, so once a run
# finalises nothing checks the robot ever actually got home. Live 2026-07-15: a
# run finalised 21:00, the robot then stalled at a no-go on the way to the dock
# and sat there ALL NIGHT with no alert, because the engine had closed the books.
STRANDED_SECONDS = 900
DOCK_RADIUS_MM = 600               # within this of the charger counts as "home"
# NO-PROGRESS: the robot is off the dock and, for this long, has neither cleaned
# more area NOR got any closer to the dock — it's moving but achieving nothing
# (circling, repositioning in place, Blocked while trying to return). The other
# watchdogs miss this because any twitch re-arms their "did it move" check; this
# one measures PRODUCTIVE progress instead, and runs for manual/native runs too.
# Live 2026-08-08: after a reposition it inched around a no-go for many minutes,
# then wedged, with no alert. Longer than the others so it's a clean backstop.
NO_PROGRESS_SECONDS = 480
CONSUMABLE_CHECK_INTERVAL = 1800   # seconds between wear-part life checks (they change slowly)
EDGE_START_SECONDS = 90            # after dispatching a room, wait this long for the robot to
                                   # actually start before treating the room as done
# Edge clean = ONE perimeter lap via the robot's native segment clean (it walls-follows
# the REAL room outline first, then fills), cut to the next room once the lap is done.
# "Lap done" = the robot's own cleaned-area counter reaching a perimeter-sized budget
# (shape-agnostic — no room polygon needed), with a time floor + cap as backstops.
EDGE_ROBOT_BAND_MM = 350           # a single wall-following pass covers ~this wide a band
EDGE_LAP_AREA_MULT = 0.8           # cut once delta-area >= perimeter-band × this. Tuned live
                                   # 2026-08-15 on Kitchen Dining: full edges done at ~7-8 m²
                                   # cleaned (~20% progress), THEN it starts filling. perim×band
                                   # ≈ 9.2 m², so 0.8 → budget ≈ 7.4 m², cutting right as the fill
                                   # begins (all edges done, minimal interior). 1.25 was too high
                                   # and let it fill a big chunk of the room. TUNABLE.
EDGE_LAP_MIN_SPEED = 180           # mm/s assumed, to size the min-lap-time floor / cap
EDGE_LAP_RATE_MM_S = 65            # PRIMARY cut fallback: a perimeter lap takes ~perimeter/this.
                                   # Time is reclean-immune (unlike cleaned_area, which
                                   # auto_recleaning corrupts) — calibrated on Kitchen Dining: edges
                                   # done ~6.7 min for a ~26 m perimeter (26300/65 ≈ 405 s). Used
                                   # until per-room rate-learning has enough samples.
# Rate-learning: measure the robot's early-phase coverage RATE (m²/min) each edge run
# and set edge_secs = edge-band-area ÷ rate — so the time-cut ADAPTS to real speed
# (Turbo vs Quiet, furniture) per room instead of the fixed rate above. Learns from the
# clean part of the area curve (the early climb), not the reclean-corrupted absolute area.
EDGE_RATE_WINDOW = 150             # s into the lap to sample the coverage rate (still perimeter,
                                   # before reclean stalls the counter)
EDGE_RATE_MIN_SAMPLES = 2          # trust the learned rate only after this many samples
EDGE_RATE_LO = 0.3                 # clamp learned rate to a sane m²/min band (reject outliers)
EDGE_RATE_HI = 3.0
EDGE_LAP_MIN_FLOOR = 60            # never cut before this many seconds of actual cleaning
EDGE_LAP_HARD_CAP = 5.0            # pure backstop: bail at this × estimated lap time only if the
                                   # area counter stalls. Must be big enough that the AREA budget
                                   # governs a healthy lap (at ~1 m²/min a big room's lap takes
                                   # ~10 min) — the old 2.5 fired mid-lap and cut edges in half.
EDGE_PLATEAU_SECONDS = 150         # cut once cleaned_area has been FLAT this long: the robot has
                                   # stopped covering new floor (perimeter done, auto_recleaning —
                                   # which the cloud 500s so we can't disable — is just re-treading
                                   # already-clean floor). MUST be comfortably longer than the gap
                                   # between integer-m² steps during real cleaning (~60-90s at
                                   # ~1 m²/min) or it false-cuts mid-perimeter on a slow stretch.
                                   # This is what makes edges-only reliable even with reclean on
                                   # (live 2026-08-15: reclean stalled the area counter so the fixed
                                   # budget never tripped → "whole room").
EDGE_PLATEAU_MIN_FRAC = 0.6        # ...but only after a real pass (delta ≥ this × budget), so a
                                   # furniture-navigation stall mid-perimeter can't cut early.
EDGE_EDGE_FRAC = 0.22              # a room's edge band ≈ this fraction of its full clean area — how
                                   # the LEARNED real room area (from normal runs) sets the cut, so
                                   # it self-tunes per room instead of leaning on the geometric guess.
EDGE_LEARN_MIN_SAMPLES = 2         # trust the learned area only after this many clean samples;
                                   # fall back to the geometric estimate until then.
EDGE_FINISH_GRACE = 90             # s the robot must be idle (not cleaning, not charging/washing)
                                   # before a room counts as DONE — so a mop-wash trip, a brief
                                   # post-error return, or any transient dock trip resumes instead
                                   # of being mistaken for 'finished' and skipping the room (live
                                   # 2026-08-18: a mid-Entrance error cascaded the whole run to a
                                   # premature dock because idle was treated as done instantly).
EDGE_RESUME_BATTERY = 80           # % — while an edge run's robot is docked mid-lap AND charging
                                   # below this, WAIT (it went to charge, resume_cleaning brings it
                                   # back); at/above this it's charged (or finished), so stop waiting.
EDGE_SUCTION_LEVEL = 0             # 0=Quiet: an edge tidy runs QUIET, baked into the segment
                                   # clean call (each room's own suction is often Turbo, and the
                                   # cloud 500s a live suction change) — the side brush still
                                   # sweeps the wall, it's just not loud
# How recent a beaching must be for the "tidy the floor" reminder to keep firing.
TIDY_RECENCY_DAYS = 2
# How long the "show me where I'm stuck" robot waits at the spot (light on) so
# you can see it, before heading home.
SHOW_DWELL_SECONDS = 60
# show-unreachable: max wait for the robot to actually BEGIN moving after a goto
# before we conclude the goto was refused (the target was unreachable and the
# robot never left). Without this phase a robot still at the dock reads as
# "already settled" and the trip looked like a no-op (live 2026-07-13).
MOVE_START_SECONDS = 25


# A door-skipped room may be retried at most this many times a day once its
# door reopens — after that it falls through to the weekly catch-up, so a door
# that keeps flapping (or a mis-wired sensor) can't send the robot out endlessly.
MAX_DOOR_RETRIES_PER_DAY = 2


class ZoneReadError(RuntimeError):
    """Raised when the map camera's zones can't be fully parsed. Because
    vacuum_set_restricted_zone REPLACES the whole zone list, a partial read must
    never be written back — that would delete the user's own zones."""


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
        # Stranded-robot watch (no active run — see _watch_stranded).
        self._stranded_pos: tuple[int, int] | None = None
        self._stranded_since: datetime | None = None
        self._stranded_notified = False
        # A high-priority "needs help" alert has fired and not yet been resolved —
        # set True by any rescue notify, cleared with an "all clear" once the robot
        # has recovered and made it back to the dock (see _maybe_all_clear).
        self._help_pending = False
        # A "needs help" alert queued but held during its recovery-grace window
        # (see _queue_help / _flush_help). {title, message, actions, image, at}.
        self._pending_alert: dict | None = None
        # Productivity tracker for _watch_no_progress: the robot's best (closest)
        # distance-to-dock and most cleaned-area seen this episode, plus when that
        # last improved. Catches a robot that's MOVING but getting nowhere (circling,
        # repositioning in place, Blocked while returning) — which the movement-based
        # watchdogs miss because a twitch re-arms them. Reset when it docks.
        self._progress_ref: dict | None = None
        # Sticky "heading to the dock" flag — the no-progress watchdog only watches
        # the return trip (set when returning, held through a reposition, cleared
        # on dock), so it can't false-fire during a normal long clean.
        self._homing = False
        # Throttle for the wear-part life check (they change over hours, not ticks).
        self._consumables_checked_at: datetime | None = None
        # Serialises evaluations AND manual actions: state-event re-evaluations
        # can land while a tick is still awaiting mid-finalise (double-banking
        # rooms + duplicate history entries, seen live 2026-07-09), and a manual
        # dispatch racing a tick's dispatch would double-send clean_segment.
        self._eval_lock = asyncio.Lock()
        # Read-only per-room area observer (for edge-cut self-tuning). Tracks the
        # room the robot is currently cleaning + its area baseline/peak so a finished
        # room's real cleaned area can be folded into the learned estimate. In-memory
        # only (a partial observation on restart just isn't learned — no harm).
        self._area_obs: dict | None = None

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
        # Door sensors: a door reopening is a trigger to retry a room we skipped.
        watched += [ent for ent in self._door_sensors() if ent]
        current_room = entity_of("sensor", self._prefix, "current_room")
        watched.append(current_room)
        self._unsub.append(
            async_track_state_change_event(self.hass, watched, self._handle_state_event)
        )
        # Buttons on the rescue alerts ("Send home" / "Resume clean") come back
        # as this event when tapped on the phone.
        self._unsub.append(
            self.hass.bus.async_listen(
                "mobile_app_notification_action", self._handle_notification_action
            )
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

    def _robot_active_segments(self) -> set:
        """Segment ids the ROBOT thinks its current task covers (its own
        active_segments attr), as ints. Empty if unknown."""
        st = self.hass.states.get(self._vacuum_entity)
        segs = _plain_attr(st.attributes.get("active_segments")) if st else None
        out: set = set()
        if isinstance(segs, (list, tuple)):
            for s in segs:
                try:
                    out.add(int(s))
                except (TypeError, ValueError):
                    pass
        return out

    def _robot_overreaching(self, run: dict) -> bool:
        """True if the robot is cleaning rooms we did NOT dispatch — a firmware
        task-state bleed (a stale whole-house task resuming, seen 2026-07-20). It
        must never clean rooms unbidden, least of all while someone's home."""
        robot = self._robot_active_segments()
        want = {int(s) for s in run.get("segments", []) if str(s).isdigit()}
        return bool(robot) and bool(want) and not robot.issubset(want)

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

    def _blocked_error(self) -> bool:
        """True if the vacuum stopped because its path was obstructed — it isn't
        stuck, something is merely in the way."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _BLOCKED_ERROR_WORDS)

    def _beached_error(self) -> bool:
        """True if the vacuum is in error AND the text says it's high-centred /
        lifted off the floor ('drop', 'lifted', 'cliff', ...) — a beaching no
        command can free. Distinct from a reversible wedge; needs a manual lift."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _BEACH_ERROR_WORDS)

    def _hardware_error(self) -> bool:
        """True if the vacuum is in error AND the text is a hardware/motor fault
        (e.g. 'right_wheel_motor') that no manoeuvre can clear — needs a physical
        check (something tangled in a wheel, or a motor overload), not a nudge."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _HARDWARE_ERROR_WORDS)

    def _tangle_error(self) -> bool:
        """True if the vacuum is in error AND the text reads as a cable/cord/cloth
        tangle (a 'suffocate'). Still recoverable enough for ONE gentle reverse,
        but escalated to a hand-needed alert sooner than a normal trap — repeated
        reversing just drags the tangle around (see _TANGLE_ERROR_WORDS)."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _TANGLE_ERROR_WORDS)

    def _station_error(self) -> bool:
        """True if the vacuum is in error AND the text is a DOCK/STATION setup
        fault (e.g. 'mop install failed' — it can't mount the mop pads). No
        manoeuvre helps and the mop rooms can't run: the user must clear/re-seat
        the station. Kept off the recoverable path so we never reverse-out of it."""
        if not self._error_active():
            return False
        err = (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "").lower()
        return bool(err) and any(w in err for w in _STATION_ERROR_WORDS)

    def _error_text(self) -> str | None:
        """The robot's RAW current error code/text (e.g. 'right_wheel_speed'), or
        None when there's no genuine fault. Surfaced verbatim in alerts so the
        ACTUAL error shows — not just a friendly summary — which is what makes a
        recurring hardware fault (like a right-wheel error) visible to the user."""
        raw = self._sval(entity_of("sensor", self._prefix, SUF_ERROR))
        if not raw:
            return None
        if str(raw).strip().lower() in ("no error", "no_error", "none", "unknown", "unavailable", ""):
            return None
        return str(raw).strip()

    # -------- map reads / zone writes (for auto-recovery no-go placement) -----
    def _map_attr(self, key: str):
        st = self.hass.states.get(entity_of("camera", self._prefix, "map"))
        return st.attributes.get(key) if st else None

    def _vacuum_position(self) -> tuple[int, int] | None:
        # NOTE: coerce first — the attribute is a Point OBJECT in-process, so a
        # bare isinstance(dict) check reads every position as None (which blinded
        # the stuck telemetry, the reverse-out measurement and the silent-stuck
        # watchdog until 2026-07-17).
        pos = _plain_attr(self._map_attr("vacuum_position"))
        if isinstance(pos, dict) and pos.get("x") is not None and pos.get("y") is not None:
            return int(pos["x"]), int(pos["y"])
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            return int(pos[0]), int(pos[1])
        return None

    @staticmethod
    def _area_to_rect(a) -> list[int] | None:
        """Camera no-go/no-mop area (Area object / 4-corner dict) -> service
        [x0,y0,x1,y1] bounding rect."""
        a = _plain_attr(a)
        if isinstance(a, dict):
            xs = [a[k] for k in ("x0", "x1", "x2", "x3") if isinstance(a.get(k), (int, float))]
            ys = [a[k] for k in ("y0", "y1", "y2", "y3") if isinstance(a.get(k), (int, float))]
            if xs and ys:
                return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        if isinstance(a, (list, tuple)) and len(a) >= 4:
            return [int(a[0]), int(a[1]), int(a[2]), int(a[3])]
        return None

    @staticmethod
    def _wall_to_line(w) -> list[int] | None:
        """Camera virtual wall (Line object) -> service [x0,y0,x1,y1].

        Deliberately NOT _area_to_rect: a wall is a SEGMENT, and min/max-ing its
        endpoints into a bounding box silently moves the wall (a line from
        (100,200)->(50,300) would come back as (50,200)->(100,300))."""
        w = _plain_attr(w)
        if isinstance(w, dict) and all(
            isinstance(w.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")
        ):
            return [int(w["x0"]), int(w["y0"]), int(w["x1"]), int(w["y1"])]
        if isinstance(w, (list, tuple)) and len(w) >= 4:
            return [int(w[0]), int(w[1]), int(w[2]), int(w[3])]
        return None

    def _current_zones(self) -> tuple[list, list, list]:
        """Existing (walls, no-go, no-mop) from the map, in the service's
        [x0,y0,x1,y1] format, so a write preserves the user's own zones.

        Raises ZoneReadError if the camera reports an entry we can't parse.
        vacuum_set_restricted_zone REPLACES the whole list, so writing a partial
        read would silently DELETE the user's zones — refusing to write is always
        the safer failure."""
        def conv(raw, fn, label):
            out = []
            for item in (raw or []):
                val = fn(item)
                if val is None:
                    raise ZoneReadError(f"unparsable {label} entry: {item!r}")
                out.append(val)
            return out

        walls = conv(self._map_attr("virtual_walls"), self._wall_to_line, "virtual wall")
        zones = conv(self._map_attr("no_go_areas"), self._area_to_rect, "no-go area")
        mops = conv(self._map_attr("no_mopping_areas"), self._area_to_rect, "no-mop area")
        return walls, zones, mops

    async def _write_zones(self, walls: list, zones: list, no_mops: list) -> None:
        await self._svc("dreame_vacuum", "vacuum_set_restricted_zone",
                        {"entity_id": self._vacuum_entity,
                         "walls": walls, "zones": zones, "no_mops": no_mops})

    @staticmethod
    def _gap_too_narrow(a: list, b: list, clearance: int) -> int | None:
        """Gap (mm) between two rects if they'd form a channel narrower than
        `clearance`, else None. Only counts where they actually face each other
        (their other axis overlaps) — that's what makes it a channel."""
        ax0, ax1 = sorted((a[0], a[2]))
        ay0, ay1 = sorted((a[1], a[3]))
        bx0, bx1 = sorted((b[0], b[2]))
        by0, by1 = sorted((b[1], b[3]))
        if min(ax1, bx1) > max(ax0, bx0):                 # overlap in x -> vertical gap
            gap = max(ay0, by0) - min(ay1, by1)
            if 0 <= gap < clearance:
                return int(gap)
        if min(ay1, by1) > max(ay0, by0):                 # overlap in y -> horizontal gap
            gap = max(ax0, bx0) - min(ax1, bx1)
            if 0 <= gap < clearance:
                return int(gap)
        return None

    async def _add_temp_nogo(self, run: dict, x: int, y: int) -> bool:
        """Temporarily steer the robot around a PROVEN blocker for the rest of
        this run. Returns True if the box was actually placed.

        Only ever called once a spot has blocked the robot repeatedly within one
        run (see BLOCK_PATTERN_COUNT) — i.e. it's demonstrably parked there today,
        not a one-off moment. The box is removed again in _finalize_run, because
        it's a fact about TODAY, not about the room: only trap_learner may propose
        a permanent no-go, and only after a spot recurs across DISTINCT runs.

        Refuses to place a box that would pinch a sub-robot-width channel against
        an existing zone — a no-go is a WALL, and two walls close together trap
        the robot just as effectively as furniture (live 2026-07-17)."""
        box = [x - NOGO_HALF_MM, y - NOGO_HALF_MM, x + NOGO_HALF_MM, y + NOGO_HALF_MM]
        try:
            walls, zones, no_mops = self._current_zones()
        except ZoneReadError as exc:
            _LOGGER.warning("work-around: zone read failed, not writing: %s", exc)
            return False
        for z in zones:
            gap = self._gap_too_narrow(box, z, ROBOT_CLEARANCE_MM)
            if gap is not None:
                _LOGGER.warning(
                    "work-around: NOT boxing (%s,%s) — it would leave a %s mm "
                    "channel against existing zone %s (robot needs %s mm)",
                    x, y, gap, z, ROBOT_CLEARANCE_MM,
                )
                return False
        if not run.get("map_backed_up"):
            try:
                await self._svc("dreame_vacuum", "vacuum_backup_map",
                                {"entity_id": self._vacuum_entity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("work-around: map backup failed: %s", exc)
            run["map_backed_up"] = True
        await self._write_zones(walls, zones + [box], no_mops)
        run.setdefault("temp_nogos", []).append(box)
        _LOGGER.info("work-around: boxed repeated blocker at (%s,%s) for this run", x, y)
        return True

    @staticmethod
    def _inside(box: list, x: int, y: int) -> bool:
        return min(box[0], box[2]) <= x <= max(box[0], box[2]) and \
               min(box[1], box[3]) <= y <= max(box[1], box[3])

    async def _clear_temp_nogos(self, run: dict) -> None:
        boxes = run.get("temp_nogos") or []
        if not boxes:
            return
        try:
            walls, zones, no_mops = self._current_zones()
            keep = [z for z in zones if z not in boxes]
            await self._write_zones(walls, keep, no_mops)
        except Exception as exc:  # noqa: BLE001
            # Includes ZoneReadError — leaving a temp box on the map is a far
            # smaller harm than writing a partial list and wiping real zones.
            _LOGGER.warning("auto-recover: clearing temp no-go failed: %s", exc)
            return
        run["temp_nogos"] = []

    async def _silence_voice(self, run: dict) -> None:
        """Mute the robot's speaker for the duration of an engine-driven maneuver,
        remembering the prior volume on the run so it can be restored. Stops it
        announcing 'unable to reach…' on repeat while WE are re-routing it; normal
        cleaning keeps its voice. Best-effort — a model without the volume number
        entity just skips it."""
        if not bool(self._opt(OPT_QUIET_RECOVERY, DEFAULT_QUIET_RECOVERY)):
            return
        if run.get("saved_volume") is not None:
            return  # already muted this maneuver
        ent = entity_of("number", self._prefix, SUF_VOLUME)
        raw = self._sval(ent)
        try:
            cur = int(float(raw)) if raw is not None else None
        except (TypeError, ValueError):
            cur = None
        if cur is None or cur == 0:
            return  # can't read it, or already silent — nothing to save/restore
        run["saved_volume"] = cur
        await self._svc("number", "set_value", {"entity_id": ent, "value": 0})

    async def _restore_voice(self, run: dict) -> None:
        """Restore the volume muted by _silence_voice (no-op if we never muted)."""
        saved = run.get("saved_volume")
        if saved is None:
            return
        run["saved_volume"] = None
        ent = entity_of("number", self._prefix, SUF_VOLUME)
        await self._svc("number", "set_value", {"entity_id": ent, "value": saved})

    async def _reverse_out(self, steps: int = REVERSE_OUT_STEPS,
                           velocity: int = REVERSE_OUT_VELOCITY) -> int:
        """Back the robot straight out the way it came — the primitive that
        reliably frees a wedge when return_to_base alone keeps ramming the
        blocked path forward. Verified live 2026-07-13: a `route` error at a rug
        lip cleared after a few reverse nudges. No vacuuming, minimal battery.

        Returns how far (mm) the robot actually moved. ~0 mm means the reverse
        achieved nothing — the tell-tale of a beaching (wheels off the floor),
        where every command returns 0 mm (proven live 2026-07-15)."""
        before = self._vacuum_position()
        for _ in range(max(1, steps)):
            try:
                await self._svc("dreame_vacuum", "vacuum_remote_control_move_step",
                                {"entity_id": self._vacuum_entity,
                                 "rotation": 0, "velocity": velocity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("auto-recover: reverse step failed: %s", exc)
                break
            await asyncio.sleep(1.0)
        after = self._vacuum_position()
        if before and after:
            return int(math.hypot(after[0] - before[0], after[1] - before[1]))
        return 0

    async def _handle_beached(self, run: dict, now: datetime, cause: str = "beached") -> bool:
        """The robot has stopped somewhere it can't get out of on its own. No
        manoeuvre helps, so alert once and hold. ``cause`` tailors the message so
        we don't mislead:

          * "beached"  — high-centred, wheels off the floor: needs a manual lift.
          * "stuck"    — reverse-out achieved nothing but there's no drop sensor:
                         it's wedged and can't free itself; ask for a look, don't
                         claim "wheels off the floor" (that wording over-stated it).
          * "hardware" — a wheel-motor/hardware fault: something tangled in a wheel
                         or a motor overload; ask for a physical check.
          * "tangle"   — wrapped in a cable/cord/cloth: one reverse didn't free it,
                         and reversing more just drags it; ask to free it by hand.

        Runs regardless of the auto-recover option — there's nothing to auto-fix.
        """
        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        msgs = {
            "beached": ("🆘 Vacuum needs a hand",
                        f"It's beached near {where} — climbed onto something and lifted its "
                        "wheels off the floor, so it can't free itself. Please lift it onto "
                        "flat floor; it'll carry on once it's back down.",
                        f"beached near {where} — needs a manual lift"),
            "stuck": ("⚠️ Vacuum may be stuck",
                      f"It's stuck near {where} and couldn't free itself (it tried to back out "
                      "but didn't move). Worth a quick look.",
                      f"stuck near {where} — needs a check"),
            "hardware": ("🔧 Vacuum needs a hand",
                         f"It stopped with a wheel-motor error near {where}. Usually something's "
                         "tangled around a wheel (hair/thread) or it jammed — please check the "
                         "wheels; it'll carry on once it's clear.",
                         f"wheel-motor error near {where} — check the wheels"),
            "tangle": ("🪢 Vacuum is tangled",
                       f"It's caught on something near {where} — most likely a cable, cord or "
                       "cloth wrapped around a brush or wheel. It can't reverse out of this one, "
                       "so please free it and pop it back on the dock; it'll carry on from there.",
                       f"tangled near {where} — please free it by hand"),
        }
        title, body, status_reason = msgs.get(cause, msgs["beached"])
        # Surface the ACTUAL robot error code, not just the friendly summary — a
        # recurring hardware fault (e.g. a right-wheel error) is only visible if
        # the raw code is in the alert the user actually reads.
        raw = self._error_text()
        if raw:
            body = f"{body}\n\nReported error: {raw}"
            status_reason = f"{status_reason} [{raw}]"
        if run.get("notified_beached"):
            self._set_status("error", status_reason)
            return True
        run["notified_beached"] = True
        run["errored"] = True
        self._help_pending = True   # so we can send an "all clear" once it's home
        pos = self._vacuum_position()
        await self.tracker.async_set_active_run(run)
        await self._log_stuck({
            "ts": now.isoformat(),
            "room": where,
            "x": pos[0] if pos else None,
            "y": pos[1] if pos else None,
            "error": (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "drop"),
            "kind": run.get("kind"),
            "attempt": 0,
            "run_id": run.get("started"),
            "beached": (cause == "beached"),
        })
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._queue_help(now, title, body, actions=self._rescue_actions())
        self._set_status("error", status_reason)
        _LOGGER.info("needs-hand (%s) near %s at %s", cause, where, pos)
        return True

    async def _handle_station(self, run: dict, now: datetime) -> bool:
        """A dock/station setup fault — the robot can't get ready to clean, almost
        always 'mop install failed' (it couldn't mount the mop pads). No manoeuvre
        helps and the mop rooms can't run until the station is fixed BY HAND, so:
        tell the user plainly, DROP the robot's task (else the firmware auto-resumes
        the doomed mop job and 'blocks' cleaning again next time — live 2026-08-13),
        and END the run WITHOUT queuing an auto-retry. The rooms stay pending for
        the weekly catch-up, which won't hammer a broken mop the way resume does."""
        if run.get("notified_station"):
            return True
        run["notified_station"] = True
        run["errored"] = True
        self._help_pending = True   # so an "all clear" fires once it's sorted
        raw = self._error_text() or "mop install failed"
        segs = [str(s) for s in run.get("segments", [])]
        remaining = ", ".join(run.get("seg_names", {}).get(s, f"Room {s}")
                              for s in segs) or "the mop rooms"
        # Drop the robot's paused/errored task so its firmware can't auto-resume a
        # mop job that will just fail again and re-block cleaning.
        await self._svc("vacuum", "stop", {"entity_id": self._vacuum_entity})
        await self._log_stuck({
            "ts": now.isoformat(), "room": "dock", "x": None, "y": None,
            "error": f"station ({raw})", "kind": run.get("kind"),
            "attempt": 0, "run_id": run.get("started"), "beached": False,
        })
        # End the run without an auto-retry: clear the resume queue (don't hammer a
        # broken mop when the house next empties) and clear active_run. The rooms
        # are simply left un-cleaned/pending → the weekly catch-up gets them once
        # the station's fixed.
        await self.tracker.async_set_resume(None)
        await self.tracker.async_set_last_run({
            "kind": run.get("kind"), "finished": now.isoformat(), "interrupted": True,
            "cleaned": [], "remaining": [run.get("seg_names", {}).get(s, s) for s in segs],
        })
        await self.tracker.async_set_active_run(None)
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._notify(
                "🧩 Vacuum can't set up at the dock",
                f"It couldn't mount its mop pads, so {remaining} won't get mopped. "
                "Check the mop pads are seated properly in the dock's tray and that "
                "the tray is clear (something jammed?) — then they'll be picked up on "
                f"the weekly catch-up.\n\nReported error: {raw}",
                high_priority=True,
            )
        self._set_status("error", f"can't set up at the dock — {raw}")
        return True

    # ------------------------------------------------- edge clean (wall strips)
    def _room_box(self, seg):
        """The robot map's bounding box for a room segment (x0,y0,x1,y1 mm), or
        None if the map doesn't carry it yet. Same coord system as clean_zone."""
        rooms = _plain_attr(self._map_attr("rooms"))
        if not isinstance(rooms, dict):
            return None
        keys = [seg, str(seg)]                     # keys may be int or str in-process
        try:
            keys.append(int(seg))
        except (TypeError, ValueError):
            pass
        r = None
        for k in keys:
            if k in rooms:
                r = _plain_attr(rooms[k])
                break
        if not isinstance(r, dict):
            return None
        try:
            return (int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]))
        except (KeyError, TypeError, ValueError):
            return None

    async def _set_select_safe(self, entity: str, option: str, tries: int = 3):
        """Best-effort select_option; returns the PREVIOUS value (to restore) or
        None. The Dreame cloud throws INTERMITTENT 500s on these, so retry a few
        times — but never let a persistent failure break the edge run; carry on
        with whatever the robot has (worst case: auto-reclean stays on and each
        room gets filled, still an edge pass, just not edges-only)."""
        st = self.hass.states.get(entity)
        prev = st.state if st else None
        for attempt in range(max(1, tries)):
            try:
                await self._svc("select", "select_option", {"entity_id": entity, "option": option})
                return prev
            except Exception as exc:  # noqa: BLE001
                _LOGGER.info("edge: set %s=%s failed (try %d/%d): %s",
                             entity, option, attempt + 1, tries, exc)
                await asyncio.sleep(2)
        return prev

    async def _set_switch_safe(self, entity: str, on: bool, tries: int = 4) -> bool | None:
        """Flip a Dreame switch to `on`, VERIFYING it lands (retry a few times) and
        returning its PREVIOUS on/off (to restore) or None. Switches apply reliably —
        unlike the suction/mode SELECTS, which the cloud 500s — which is why the edge
        run turns off `customized_cleaning` (a switch) so its per-call Quiet suction
        actually takes (live 2026-08-15: per-call suction was ignored while customized
        cleaning was on, so the pass ran at each room's saved Turbo). The retry+verify
        matters on RESTORE: right after the cut the switch is briefly 'unavailable', so
        a single turn_on silently no-ops and leaves customized cleaning off."""
        st = self.hass.states.get(entity)
        prev = (st.state == "on") if st and st.state in ("on", "off") else None
        want = "on" if on else "off"
        for attempt in range(max(1, tries)):
            cur = self.hass.states.get(entity)
            if cur and cur.state == want:
                return prev
            try:
                await self._svc("switch", "turn_on" if on else "turn_off", {"entity_id": entity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.info("edge: switch %s -> %s failed (try %d/%d): %s",
                             entity, on, attempt + 1, tries, exc)
            await asyncio.sleep(2)
        return prev

    async def _start_edge_run(self, segments, *, manual: bool) -> bool:
        """Begin a dedicated edge clean over `segments` — a native per-room segment
        clean at NORMAL power, cut to the next room once its perimeter lap is done
        (by time). Chains room-to-room without docking; docks only to charge (then
        resumes) or when all rooms are done. Returns True if a run started.

        No quiet / customized-cleaning juggling: per-call Quiet is ignored by this
        robot (it uses the global suction, whose select the cloud 500s), so a
        reliable quiet-from-HA isn't possible — set Quiet in the app if wanted."""
        segs = [str(s) for s in segments if self._room_box(s) is not None]
        if not segs:
            _LOGGER.info("edge: no rooms with map geometry to edge-clean")
            return False
        if self.tracker.active_run is not None or self.tracker.edge_run is not None:
            return False
        passes = max(1, int(self._opt(OPT_EDGE_PASSES, DEFAULT_EDGE_PASSES)))
        run = {
            "segments": segs, "done": [], "current": None,
            "started": dt_util.now().isoformat(), "dispatched_at": None,
            "seen_cleaning": False, "manual": bool(manual),
            "passes": passes, "laps_done": 0,
        }
        await self.tracker.async_set_edge_run(run)
        self._set_status("running", f"edge clean — {len(segs)} room(s)")
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._notify("🧭 Edge clean started",
                               f"Cleaning the wall edges of {len(segs)} room(s), one at a time.")
        _LOGGER.info("edge run started: %s", segs)
        return True

    async def _check_edge_run(self, now: datetime, away_ok: bool) -> None:
        """Drive the edge run room-by-room — dispatch one room's native segment
        clean (the robot walls-follows the REAL outline first), cut to the next
        room once its perimeter lap is done, then the next. Sequenced across ticks
        because each clean_segment REPLACES the previous task. Presence-gated."""
        edge = self.tracker.edge_run
        if edge is None or self._vacuum_state() is None:
            return
        if (not away_ok and not edge.get("manual")
                and bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))):
            self._set_status("waiting", "edge clean paused — someone's home")
            return
        if self._error_active():
            self._set_status("error", "edge clean paused — robot needs a hand")
            return
        st = self.hass.states.get(self._vacuum_entity)
        vstate = self._vacuum_state() or ""
        busy = (vstate in ("cleaning", "returning")
                or bool(st and (st.attributes.get("zone_cleaning")
                                or st.attributes.get("washing") or st.attributes.get("drying")
                                or st.attributes.get("returning_to_wash")
                                or st.attributes.get("washing_paused"))))
        cleaning_now = (vstate == "cleaning" and bool(st and st.attributes.get("segment_cleaning")))
        # Nothing dispatched yet → start the first/next room NOW, even if the robot is
        # busy at the dock (washing/drying after a clean can take hours) — the
        # clean_segment interrupts it. Without this a manual edge run would just wait.
        if edge.get("current") is None:
            await self._edge_next_or_finish(edge, now)
            return
        # Track when the robot is/ isn't actually cleaning this room — the debounce that
        # keeps a transient interruption (mop-wash trip, post-error blip) from looking
        # like the room is 'done'.
        if cleaning_now:
            edge["seen_cleaning"] = True
            edge["stopped_since"] = None
        elif edge.get("seen_cleaning") and not edge.get("stopped_since"):
            edge["stopped_since"] = now.isoformat()
        # Primary advance: the lap's cleaning-time reached the estimate (works whether
        # the robot is mid-room-busy or has just idled having done enough).
        if edge.get("seen_cleaning") and await self._edge_lap_done(edge, now):
            return
        if busy:                                   # cleaning / returning / washing / drying
            await self.tracker.async_set_edge_run(edge)
            return
        # Robot IDLE mid-lap. Decide wait vs advance.
        box = self._room_box(edge.get("current"))
        _, edge_secs = self._edge_lap_budget(box, edge.get("current")) if box else (0, 0)
        ct_secs = (self._cleaning_time_min() or 0) * 60
        battery = st.attributes.get("battery") if st else None
        if (box is not None and ct_secs < edge_secs and self._robot_charging()
                and (battery is None or float(battery) < EDGE_RESUME_BATTERY)):
            # Docked to CHARGE before the lap finished → wait (resume_cleaning brings it
            # back). Releases at EDGE_RESUME_BATTERY so a finished room can't stall.
            self._set_status("waiting", "edge clean — charging, will resume")
            return
        disp = _parse_iso(edge.get("dispatched_at"))
        if not edge.get("seen_cleaning"):
            if disp and (now - disp).total_seconds() < EDGE_START_SECONDS:
                return                              # still on its way to the room
            await self._edge_advance(edge, now)     # never started in time → skip on
            return
        # It cleaned, then stopped, and the lap's time-cut did NOT fire (else we'd have
        # advanced above). With ct-based timing the robot can't finish a room before its
        # perimeter time, so a SUSTAINED idle here means the run was stopped EXTERNALLY —
        # the user docked it, or a persistent error. Don't march on to the next room:
        # CANCEL. A mop-wash / post-error blip resumes within the debounce (clearing
        # stopped_since), so it won't trip this.
        stopped = _parse_iso(edge.get("stopped_since"))
        if stopped is None or (now - stopped).total_seconds() < EDGE_FINISH_GRACE:
            self._set_status("running", "edge clean — waiting for the robot")
            await self.tracker.async_set_edge_run(edge)
            return
        await self._cancel_edge_run(edge, "stopped")

    def _cleaning_time_min(self) -> float | None:
        """The robot's own current-task cleaning time (minutes) — counts ACTUAL
        cleaning and pauses while charging, so it's the right clock for the lap cut
        (a charge break doesn't advance it) and charge-and-continue just works."""
        st = self.hass.states.get(self._vacuum_entity)
        val = st.attributes.get("cleaning_time") if st else None
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def _robot_charging(self) -> bool:
        st = self.hass.states.get(self._vacuum_entity)
        return bool(st and st.attributes.get("charging"))

    async def _observe_room_areas(self) -> None:
        """READ-ONLY: watch the room the robot is cleaning and fold each finished
        room's real cleaned area into the per-room learned estimate — this is where
        the edge cut learns each room's size, from NORMAL runs. It only reads state,
        so it can never disturb the run logic. Skipped during an edge run (those
        rooms are deliberately cut short, so their area isn't the full-room size)."""
        if (not bool(self._opt(OPT_EDGE_LEARN, DEFAULT_EDGE_LEARN))
                or self.tracker.edge_run is not None):
            if self._area_obs:
                self._area_obs = None
            return
        st = self.hass.states.get(self._vacuum_entity)
        seg = None
        if (st is not None and (self._vacuum_state() or "") == "cleaning"
                and st.attributes.get("segment_cleaning")):
            seg = st.attributes.get("current_segment")
        area = self._cleaned_area()
        obs = self._area_obs
        if seg is None or area is None:                # not cleaning a segment -> close out
            if obs:
                await self._finalise_room_obs(obs)
                self._area_obs = None
            return
        seg = str(seg)
        if obs is None or obs.get("seg") != seg:       # moved rooms -> bank previous, start new
            if obs:
                await self._finalise_room_obs(obs)
            self._area_obs = {"seg": seg, "base": area, "peak": area}
            return
        if area < obs["base"]:                         # counter reset mid-room -> re-baseline
            obs["base"] = area
        obs["peak"] = max(obs["peak"], area)

    async def _finalise_room_obs(self, obs: dict) -> None:
        """Fold a just-finished room observation into the learned estimate — unless
        the robot is in an error state (a stuck/partial run would poison it)."""
        if self._error_active():
            return
        seg = obs.get("seg")
        box = self._room_box(seg) if seg is not None else None
        if box is None:
            return
        sample = float(obs.get("peak", 0)) - float(obs.get("base", 0))
        box_area = abs((box[2] - box[0]) * (box[3] - box[1])) / 1_000_000.0   # mm² -> m²
        lo, hi = max(1.0, box_area * 0.15), box_area * 1.10
        entry = fold_room_area(self.tracker.room_learn.get(seg), sample, lo, hi)
        if entry and entry != self.tracker.room_learn.get(seg):
            await self.tracker.async_set_room_learn(seg, entry)
            _LOGGER.debug("learn: room %s area sample %.1fm2 -> %s", seg, sample, entry)

    def _edge_lap_budget(self, box, seg=None) -> tuple[float, float]:
        """(early-cut area m², lap seconds) for one perimeter lap. The SECONDS are the
        PRIMARY cut — a lap takes ~perimeter/rate (a fixed, reclean-immune geometric
        estimate; cleaned_area is corrupted by auto_recleaning + too coarse to time or
        rate-learn from reliably). The area is only a secondary early-cut for the case
        the robot covers the whole perimeter band fast."""
        perim_mm = 2 * ((box[2] - box[0]) + (box[3] - box[1]))
        band_area = (perim_mm / 1000.0) * (EDGE_ROBOT_BAND_MM / 1000.0)
        early_area = band_area * EDGE_LAP_AREA_MULT
        edge_secs = max(EDGE_LAP_MIN_FLOOR, perim_mm / EDGE_LAP_RATE_MM_S)
        return early_area, edge_secs

    async def _edge_lap_done(self, edge: dict, now: datetime) -> bool:
        """True (and advances) once the current room's perimeter lap is done. PRIMARY
        signal is the robot's own cleaning_time reaching the lap estimate — it counts
        ACTUAL cleaning and pauses while charging, so it's reclean-immune AND makes
        charge-and-continue free. A secondary early-cut fires if the robot covers the
        whole perimeter band fast."""
        cur = edge.get("current")
        box = self._room_box(cur) if cur is not None else None
        if box is None or not edge.get("seen_cleaning"):
            return False
        early_area, edge_secs = self._edge_lap_budget(box, cur)
        ct_secs = (self._cleaning_time_min() or 0) * 60.0     # actual cleaning; pauses on charge
        if ct_secs < EDGE_LAP_MIN_FLOOR:               # too soon — let the lap get going
            return False
        if ct_secs >= edge_secs:                       # PRIMARY: time-based lap done
            _LOGGER.info("edge: %s lap done by time (%ds >= %ds) — next room",
                         cur, int(ct_secs), int(edge_secs))
            return await self._edge_advance(edge, now)
        area = self._cleaned_area()                    # SECONDARY: covered the band fast
        if area is not None:
            a0 = edge.get("area0")
            if a0 is None or area < a0:
                edge["area0"] = a0 = area
                await self.tracker.async_set_edge_run(edge)
            if (area - a0) >= early_area:
                _LOGGER.info("edge: %s covered band early (%.1fm2 in %ds) — next room",
                             cur, area - a0, int(ct_secs))
                return await self._edge_advance(edge, now)
        return False

    async def _edge_advance(self, edge: dict, now: datetime) -> bool:
        """A perimeter lap of the current room just finished. Do ANOTHER lap of the
        SAME room if more passes are wanted (opt-in ``edge_passes`` — loop the edges
        to get them properly clean), otherwise mark it done and move to the next
        room (or finish)."""
        cur = edge.get("current")
        if cur is not None:
            laps = int(edge.get("laps_done", 0)) + 1
            passes = max(1, int(edge.get("passes", 1)))
            if laps < passes:                          # re-run the same room's edges
                edge["laps_done"] = laps
                await self._edge_dispatch(edge, cur, now, extra=f" (pass {laps + 1}/{passes})")
                return True
            edge["done"].append(cur)
        edge["current"] = None
        edge["seen_cleaning"] = False
        edge["area0"] = None
        edge["laps_done"] = 0
        await self._edge_next_or_finish(edge, now)
        return True

    async def _edge_dispatch(self, edge: dict, seg, now: datetime, extra: str = "") -> None:
        """Dispatch one perimeter lap of `seg` (a native segment clean, NORMAL power),
        and reset the per-lap trackers. Shared by first-dispatch, next-room and re-pass.
        A new clean_segment redirects the robot straight from a cut room to the next —
        no trip to the dock between."""
        edge["current"] = seg
        edge["dispatched_at"] = now.isoformat()
        edge["seen_cleaning"] = False
        edge["stopped_since"] = None
        edge["area0"] = None
        await self.tracker.async_set_edge_run(edge)
        await self._send_clean_segment([int(seg)])
        self._set_status("running", f"edge clean — {self._room_name(seg)}{extra}")
        _LOGGER.info("edge: perimeter lap of %s%s", seg, extra)

    async def _edge_next_or_finish(self, edge: dict, now: datetime) -> None:
        """Dispatch the next room's native segment clean, or finalize if none remain.
        A new clean_segment replaces the current task, so this also redirects the
        robot straight from a cut room to the next — no trip to the dock between."""
        while edge["segments"]:
            seg = edge["segments"].pop(0)
            if not str(seg).isdigit():
                continue
            # NB: don't skip on a missing room_box here — the room was validated at
            # start, and the map camera transiently drops its 'rooms' attribute during
            # a state transition (live 2026-08-18: Lounge was dropped from the chain
            # that way). Dispatch it; the lap cut tolerates a momentary None box.
            edge["laps_done"] = 0
            await self._edge_dispatch(edge, seg, now)
            return
        await self._finish_edge_run(edge, now)

    async def _cancel_edge_run(self, edge: dict, reason: str) -> None:
        """Abandon the whole edge run (not just the current room) — e.g. the user
        docked the robot mid-run, or it errored out. Sends it home and clears the run
        so it does NOT march on to the next room (live 2026-08-18: a manual dock got
        overridden and the robot went back out)."""
        await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
        # Mark today done so a SCHEDULED run doesn't immediately re-fire and fight the
        # user's stop; a manual run just ends.
        await self.tracker.async_set_last_edge(dt_util.now().date().isoformat())
        await self.tracker.async_set_edge_run(None)
        self._set_status("idle", f"edge clean stopped ({reason})")
        _LOGGER.info("edge run cancelled (%s) — did %s, was on %s",
                     reason, edge.get("done"), edge.get("current"))

    async def _finish_edge_run(self, edge: dict, now: datetime) -> None:
        # All rooms done → dock for good. (No settings to restore: the run doesn't
        # change customized-cleaning / suction any more — it uses normal power.)
        await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
        await self.tracker.async_set_last_edge(now.date().isoformat())
        await self.tracker.async_set_edge_run(None)
        self._set_status("idle", "edge clean finished")
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._notify("🧭 Edge clean done",
                               f"Ran the wall edges of {len(edge.get('done', []))} room(s).")
        _LOGGER.info("edge run done: %s", edge.get("done"))

    async def _apply_pending_edge_restore(self) -> None:
        """Put back the settings an edge run changed (customized_cleaning back ON,
        auto_recleaning to its old value) — but only once the robot has re-docked,
        since those can't be set while it's busy/returning. Retries across ticks
        until it lands, then clears the pending marker. Runs every tick regardless
        of scheduler-enabled state so a half-finished edge run never strands it."""
        pend = self.tracker.edge_restore
        if not pend:
            return
        if (self._vacuum_state() or "") in ("cleaning", "returning"):
            return                                     # still on its way home
        st = self.hass.states.get(self._vacuum_entity)
        if st and (st.attributes.get("washing") or st.attributes.get("drying")):
            return                                     # busy at the dock — try next tick
        ent = entity_of("switch", self._prefix, "customized_cleaning")
        cur = self.hass.states.get(ent)
        if cur is None or cur.state == "unavailable":
            return                                     # switch not settable yet — retry
        if pend.get("customized_cleaning") is not False and cur.state != "on":
            await self._set_switch_safe(ent, True)
            cur = self.hass.states.get(ent)
            if not (cur and cur.state == "on"):
                return                                 # didn't take — leave pending, retry
        if pend.get("auto_recleaning"):
            await self._set_select_safe(
                entity_of("select", self._prefix, "auto_recleaning"), pend["auto_recleaning"])
        await self.tracker.async_set_edge_restore(None)
        _LOGGER.info("edge: settings restored after re-dock")

    def _edge_schedule_due(self, now: datetime, now_min: int) -> bool:
        """True when the dedicated edge schedule should fire now — enabled, its
        every-N-days cadence is due, and it's at/after the configured time."""
        if not bool(self._opt(OPT_EDGE_ENABLED, DEFAULT_EDGE_ENABLED)):
            return False
        every = int(self._opt(OPT_EDGE_EVERY_DAYS, DEFAULT_EDGE_EVERY_DAYS))
        if not edge_due(self.tracker.last_edge, every, now.date()):
            return False
        return now_min >= clean_window.to_minutes(self._opt(OPT_EDGE_TIME, DEFAULT_EDGE_TIME))

    async def async_edge_clean(self, segments=None) -> None:
        """Manual entry point (service): edge-clean the given room segments, or
        every enabled room if none given."""
        if segments:
            segs = [str(s) for s in segments]
        else:
            segs = all_enabled_segments(self._rooms())
        async with self._eval_lock:
            await self._start_edge_run(segs, manual=True)

    def _is_cleaning_now(self) -> bool:
        """The robot is actively cleaning again (progress resumed) — not merely
        'returning' or 'paused'. Used to decide a held alert can be cancelled."""
        if (self._vacuum_state() or "").lower() == "cleaning":
            return True
        status = (self._sval(entity_of("sensor", self._prefix, SUF_STATUS)) or "").lower()
        task = (self._sval(entity_of("sensor", self._prefix, SUF_TASK_STATUS)) or "").lower()
        return (any(w in task for w in _CLEANING_ACTIVE_WORDS)
                or any(w in status for w in _CLEANING_ACTIVE_WORDS))

    async def _queue_help(self, now: datetime, title: str, message: str, *,
                          actions: list[dict] | None = None, image: str | None = None) -> None:
        """Hold a 'needs help' alert for its recovery-grace window instead of
        sending it now (see HELP_GRACE_SECONDS). _flush_help sends it if the robot
        is still stuck when the window elapses, or drops it silently if it's
        cleaning again by then. A later trip in the same window updates the CONTENT
        (so 'recovering' → 'beached' shows the true final state) but keeps the
        original timer, so a flapping error can't postpone the alert forever."""
        at = self._pending_alert["at"] if self._pending_alert else now.isoformat()
        first = self._pending_alert is None
        self._pending_alert = {
            "title": title, "message": message, "actions": actions, "image": image,
            "at": at,
        }
        if first:
            _LOGGER.info("held help alert (%ss grace): %s", HELP_GRACE_SECONDS, title)

    async def _flush_help(self, now: datetime) -> None:
        """Resolve a held 'needs help' alert: cancel it if the robot recovered
        (cleaning again, no error), else send it once the grace window elapses."""
        p = self._pending_alert
        if not p:
            return
        if self._is_cleaning_now() and not self._error_active():
            self._pending_alert = None
            self._help_pending = False   # nothing was shown → no out-of-nowhere 'all clear'
            _LOGGER.info("held help alert cancelled — robot recovered: %s", p["title"])
            return
        at = _parse_iso(p.get("at"))
        if at is not None and (now - at).total_seconds() < HELP_GRACE_SECONDS:
            return  # still within grace — give recovery a chance
        self._pending_alert = None
        await self._notify(p["title"], p["message"], high_priority=True,
                           actions=p.get("actions"), image=p.get("image"))
        _LOGGER.info("held help alert sent after grace: %s", p["title"])

    async def _maybe_all_clear(self, now: datetime) -> None:
        """After a 'needs help' alert, tell the user ONCE when the robot has
        sorted itself out and is back on the dock — so a self-recovered rescue
        doesn't leave them thinking it still needs help (live 2026-08-03: a
        wheel-motor alert fired, the robot freed itself and docked, and nothing
        ever said 'all clear')."""
        if not self._help_pending or self._error_active():
            return
        if not self._at_dock():
            return                      # hasn't made it home yet
        self._help_pending = False
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            await self._notify(
                "✅ Vacuum all clear",
                "It sorted itself out and is back on the dock — no action needed.",
            )
        _LOGGER.info("all-clear: robot recovered and docked after a help alert")

    async def _watch_no_progress(self, now: datetime) -> None:
        """Catch a robot that's trying to get to the dock but CAN'T — circling,
        repositioning, or Blocked on the way home — and ask for a hand.

        Only watches the RETURN trip. While the robot is actively CLEANING it moves
        for ages without the productivity metrics climbing — an auto-reclean pass
        re-covers already-counted floor (cleaned m² + task% sit flat), and those
        integer counters plateau in slow sections — so watching mid-clean false-fired
        'can't get home' during normal long mop cleans (live 2026-08-14, 3x). The
        'can't get home' alert doesn't even apply mid-clean, so we gate to homing.

        `_homing` is sticky: set once the robot heads for the dock, held THROUGH a
        reposition (which briefly flips it back to 'cleaning'), and cleared only
        when it actually docks — so the original case (sent home, can't get back
        after a reposition — 08-11) still fires, while a normal clean never arms it.

        Productive during the return = it netted STUCK_MIN_ESCAPE_MM closer to the
        dock (or its task%/cleaned-m² still happened to climb) since the last
        productive moment; the distance anchor resets fresh each time."""
        pos = self._vacuum_position()
        if pos is None:
            return
        if self._at_dock(pos):
            self._progress_ref = None          # home -> reset
            self._homing = False
            return
        st = self.hass.states.get(self._vacuum_entity)
        if (st and st.attributes.get("returning")) or (self._vacuum_state() or "") == "returning":
            self._homing = True                # sticky until it docks
        if not self._homing:
            # Actively cleaning, not heading home — don't watch (see docstring).
            self._progress_ref = None
            return
        dock = self._charger_position()
        dist = (math.hypot(pos[0] - dock[0], pos[1] - dock[1])
                if dock is not None else None)
        area = self._cleaned_area()
        prog = self._clean_progress()
        ref, productive = evaluate_progress(
            self._progress_ref, now.isoformat(), prog, area, dist, STUCK_MIN_ESCAPE_MM)
        self._progress_ref = ref
        if productive:
            return
        # Another rescue alert already covers this episode? Don't double-notify.
        if self._help_pending or ref.get("notified"):
            return
        since = _parse_iso(ref.get("since"))
        stalled = (now - since).total_seconds() if since else 0
        if stalled < NO_PROGRESS_SECONDS:
            return
        ref["notified"] = True
        self._help_pending = True
        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        err = self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "no error"
        await self._log_stuck({
            "ts": now.isoformat(), "room": where, "x": pos[0], "y": pos[1],
            "error": f"no_progress ({err})", "kind": "any", "attempt": 0,
            "run_id": None, "beached": False,
        })
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            mins = int(stalled // 60)
            await self._queue_help(
                now,
                "🛟 Vacuum can't get home",
                f"It's been near {where} for {mins} min moving about but not getting any "
                "closer to the dock — it may be circling, repositioning, or nudged up "
                "against something. Worth a look; it probably needs a hand.",
                actions=self._rescue_actions(),
            )
        _LOGGER.info("no-progress: %ss near %s (dist=%s, err=%s)", int(stalled), where, dist, err)

    async def _check_consumables(self, now: datetime) -> None:
        """Warn once when a wear-part (filter, brush, mop pad, sensors, detergent,
        silver-ion) drops to/below the configured remaining-life %, with buttons to
        reset its counter (once replaced) or dismiss. Throttled — these change over
        hours, not ticks — and deduped via a persisted per-part flag that clears
        itself once the part is replaced (its life climbs back above threshold)."""
        if not bool(self._opt(OPT_CONSUMABLE_ALERT, DEFAULT_CONSUMABLE_ALERT)):
            return
        last = self._consumables_checked_at
        if last is not None and (now - last).total_seconds() < CONSUMABLE_CHECK_INTERVAL:
            return
        threshold = int(self._opt(OPT_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD))
        readings = self._consumable_readings()
        if all(v is None for v in readings.values()):
            return   # robot not reporting yet (e.g. mid cloud-reconnect) — retry next
                     # tick; do NOT arm the throttle, or the first alert is delayed.
        self._consumables_checked_at = now
        alerted = dict(self.tracker.consumable_alerted)
        due, new_alerted = evaluate_consumables(readings, threshold, alerted)
        if new_alerted != alerted:
            await self.tracker.async_set_consumable_alerted(new_alerted)
        for c in due:
            pct = readings.get(c["key"])
            await self._notify(
                f"{c['emoji']} {c['name']} running low",
                f"The {c['name'].lower()} is at {pct}% of its life. Replace it soon; "
                "once you have, tap “Reset counter” so the robot starts a fresh count.",
                actions=[
                    {"action": self._action_id(f"RESETCONS_{c['key']}"), "title": "Reset counter"},
                    {"action": self._action_id(f"DISMISSCONS_{c['key']}"), "title": "Dismiss"},
                ],
            )
            _LOGGER.info("consumable low: %s at %s%% (<= %s%%)", c["key"], pct, threshold)

    async def _handle_blocked(self, run: dict, now: datetime) -> bool:
        """Something is in its way. Do the SMALLEST thing that works: resume.

        The robot re-plans around the obstruction and gets on with the job. We do
        not reverse it, wall the spot off, or send it home — a block is a moment
        (a toy, a chair, someone's feet), not a property of the room, and one
        obstruction must never end the whole clean. Only if it keeps happening do
        we stop nudging and ask for help."""
        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        n = int(run.get("block_resumes", 0))
        if n >= MAX_BLOCK_RESUMES:
            if not run.get("notified_blocked"):
                run["notified_blocked"] = True
                run["errored"] = True
                await self.tracker.async_set_active_run(run)
                if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                    # Show the user WHAT stopped it. The robot photographed the
                    # thing; a picture ends the guesswork instantly. NOTE the
                    # obstacle sits ahead on its path, NOT under it — so we name
                    # the obstacle's own room/position, never the robot's.
                    snap = self._obstacle_snapshot(self._vacuum_position())
                    if snap:
                        what, url, dist = snap
                        msg = (f"Something's in its way and it can't find a route. "
                               f"Nearest thing it photographed: {what}, about "
                               f"{dist / 1000:.1f} m away. Move it and it'll carry on.")
                    else:
                        url = None
                        msg = (f"Something's been in its way near {where} {n} times, "
                               "so it can't get on with the clean. Worth a look.")
                    await self._queue_help(
                        now,
                        "⚠️ Vacuum blocked — here's what it saw",
                        msg,
                        actions=self._rescue_actions(),
                        image=url,
                    )
            self._set_status("error", f"blocked repeatedly near {where}")
            return True

        run["block_resumes"] = n + 1
        run["errored"] = True
        await self.tracker.async_set_active_run(run)
        pos = self._vacuum_position()
        await self._log_stuck({
            "ts": now.isoformat(), "room": where,
            "x": pos[0] if pos else None, "y": pos[1] if pos else None,
            "error": (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "blocked"),
            "kind": run.get("kind"), "attempt": n + 1,
            "run_id": run.get("started"), "beached": False,
        })
        await self._resume_run(run)
        self._set_status("running", f"something was in its way near {where} — carrying on")
        _LOGGER.info("blocked near %s at %s — resumed (%s/%s)", where, pos, n + 1, MAX_BLOCK_RESUMES)
        return True

    async def _maybe_auto_recover(self, run: dict, now: datetime) -> bool:
        """Unstick a wedged robot and CARRY ON cleaning, rather than docking.
        Returns True if it handled this tick."""
        # Dock/station fault (e.g. "mop install failed") — the robot can't get
        # ready to clean and no manoeuvre helps. Tell the user and END the run so
        # it can't wedge the scheduler; handle this before the reverse-out paths.
        if self._station_error():
            return await self._handle_station(run, now)
        # Beaching first, before the auto-recover gate: a lifted/high-centred
        # robot needs a hand no matter the option — reverse-out can't help it.
        if self._beached_error():
            return await self._handle_beached(run, now)
        # A wheel-motor / hardware fault can't be reversed out of — a nudge won't
        # help and would mis-read as "beached". Ask for a physical check instead.
        if self._hardware_error():
            return await self._handle_beached(run, now, cause="hardware")
        # Merely obstructed? Just resume — the least intervention that works.
        if self._blocked_error():
            return await self._handle_blocked(run, now)

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
                                  and not run.get("door_retry_home")
                                  and bool(self._opt(OPT_RETURN_ON_ARRIVAL, DEFAULT_RETURN_ON_ARRIVAL))
                                  and bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))
                                  and self._presence_home() is True))
                if home_block:
                    await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
                    self._set_status("returning", "recovered — someone home, docking")
                else:
                    # Resume the clean — but only the run's OWN segments, never a
                    # bare vacuum.start (which whole-houses once the task ended).
                    await self._resume_run(run)
                    self._set_status("running", "recovered — carrying on, steering clear of the stuck spot")
                await self._restore_voice(run)
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
            await self._restore_voice(run)
            run["recovering"] = False
            await self.tracker.async_set_active_run(run)
            return False

        if not self._recoverable_error():
            return False
        # Cable/cord/cloth tangle: we gave it ONE gentle reverse already (in case
        # it could back off a loose cord). It's still erroring, so stop — a second
        # reverse just drags the tangle tighter (and dragging registers as
        # 'movement', so the reverse-moved-nothing check never fires). Ask for a
        # hand instead (live 2026-08-09: bedroom cables, three reverses only
        # dragged it and the user had to carry it back to the dock).
        if self._tangle_error() and int(run.get("recover_count", 0)) >= TANGLE_MAX_REVERSE:
            if not run.get("notified_beached"):
                run["recovering"] = False
                await self._restore_voice(run)   # end our maneuver, give its voice back
                await self.tracker.async_set_active_run(run)
                await self._handle_beached(run, now, cause="tangle")
            return True
        if int(run.get("recover_count", 0)) >= MAX_RECOVER_ATTEMPTS:
            # Give up — but at least try to bring it home once before falling
            # through to the error notify (nothing else ever docks it).
            if not run.get("gave_up_dock"):
                run["gave_up_dock"] = True
                await self.tracker.async_set_active_run(run)
                await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            return False

        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        pos = self._vacuum_position()   # the TRAP — walled off after it backs away
        run["recover_count"] = int(run.get("recover_count", 0)) + 1
        run["recovering"] = True
        run["recover_started"] = now.isoformat()
        run["errored"] = True
        await self.tracker.async_set_active_run(run)
        # Telemetry for the learning engine: where it wedged + the error + attempt
        # number. Pure capped logging, no behaviour -- the recurring-trap learner
        # clusters these to decide when a spot has earned a permanent no-go.
        await self._log_stuck({
            "ts": now.isoformat(),
            "room": where,
            "x": pos[0] if pos else None,
            "y": pos[1] if pos else None,
            "error": (self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "unknown"),
            "kind": run.get("kind"),
            "attempt": run["recover_count"],
            "run_id": run.get("started"),
            "beached": False,
        })
        # Reverse out first — back off the trap the way it came in. return_to_base
        # alone kept ramming the blocked path forward (live 2026-07-13: a route
        # error at a rug lip); a straight reverse retraces the entry route and
        # frees it. Then hand off to return_to_base, which continues the robot's
        # own reverse-out recovery and re-localises (verified 2026-07-07 + 07-13).
        await self._silence_voice(run)   # hush it while WE drive the reverse-out
        moved = await self._reverse_out()
        # We deliberately DON'T wall the spot off any more.
        #
        # A block is a MOMENT, not a property of the room. What stopped it is
        # usually a toy, a chair pushed out, a pair of feet — writing that into
        # the map turns a temporary fact into a permanent wall, and walls don't
        # expire. Live 2026-07-17 the temp box pinched the robot against an
        # existing zone and stranded it 15 min; separately a no-go placed on one
        # night's evidence blocked a whole run for 18 min. Both times the fix was
        # to REMOVE a wall and press resume — never to add one. The path planner
        # routes around obstacles better than we can guess at them.
        #
        # Only trap_learner ever proposes a PERMANENT no-go: after a spot has
        # stuck across several DISTINCT runs (so it really is a property of the
        # room, not today's clutter), and then only as a suggestion to approve.
        await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
        # A reverse-out that moved ~nothing achieved nothing. One dud can be
        # timing; repeated duds mean it's beached (wheels off the floor) even
        # though the error text didn't say so — escalate to a hand-needed alert
        # rather than looping uselessly (learned live 2026-07-15).
        run["stuck_no_move"] = (int(run.get("stuck_no_move", 0)) + 1
                                if moved < STUCK_MIN_ESCAPE_MM else 0)
        await self.tracker.async_set_active_run(run)
        if run["stuck_no_move"] >= STUCK_NO_MOVE_LIMIT:
            run["recovering"] = False
            await self._restore_voice(run)   # our maneuver's over; give it its voice back
            # Reverse-out achieved nothing, but the error wasn't a drop sensor —
            # it's wedged, not lifted. Say "stuck", not "beached/wheels off floor".
            await self._handle_beached(run, now, cause="stuck")
            return True
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            raw = self._error_text()
            body = f"Got stuck near {where} — backing it out and letting it re-plan to carry on."
            if raw:
                body += f"\n\nReported error: {raw}"
            await self._queue_help(
                now,
                "🛟 Vacuum recovering",
                body,
                actions=self._rescue_actions(),
            )
        _LOGGER.info("auto-recover: attempt %s near %s at %s (moved %s mm, error %s)",
                     run["recover_count"], where, pos, moved, self._error_text())
        return True

    def _charger_position(self) -> tuple[int, int] | None:
        pos = _plain_attr(self._map_attr("charger_position"))
        if isinstance(pos, dict) and pos.get("x") is not None and pos.get("y") is not None:
            return int(pos["x"]), int(pos["y"])
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            return int(pos[0]), int(pos[1])
        return None

    def _at_dock(self, pos: tuple[int, int] | None = None) -> bool:
        """Is the robot physically AT its dock? MEASURED, never inferred from the
        state name.

        `idle` means "doing nothing" — NOT "at the dock". A robot idle in the
        middle of the floor is stranded, and treating idle as parked is what made
        the silent-stuck watchdog skip a genuinely stranded robot while the status
        line cheerfully claimed it was "paused at the dock" 8 m away (live
        2026-07-17)."""
        if pos is None:
            pos = self._vacuum_position()
        dock = self._charger_position()
        if pos is not None and dock is not None:
            return math.hypot(pos[0] - dock[0], pos[1] - dock[1]) <= DOCK_RADIUS_MM
        # No coordinates to measure with — fall back to the robot's own claim.
        return (self._vacuum_state() or "") == "docked"

    def _home_or_servicing(self) -> bool:
        """True when the robot is on the dock or busy AT the station — washing or
        drying its mop pads. A wash/dry cycle reports vacuum_state 'cleaning' even
        though the robot never left the dock, so treat it as 'home'. Without this,
        the dock-enforcement branches read that wash-cycle 'cleaning' as an escape
        and fire return_to_base on every blip, looping for the whole wash (live
        2026-08-08: a ~3-min return_to_base loop that only stopped when the wash
        finished)."""
        st = self.hass.states.get(self._vacuum_entity)
        if st is None:
            return False
        a = st.attributes
        return bool(a.get("docked") or a.get("washing") or a.get("drying"))

    def _escaped_cleaning(self) -> bool:
        """The robot is genuinely out on the floor cleaning — not sitting on the
        dock reporting 'cleaning' because it's washing/drying its pads. This is the
        real 'it escaped while someone's home' test the dock-enforcement uses."""
        return (self._vacuum_state() or "") == "cleaning" and not self._home_or_servicing()

    def _manual_intent_stale(self, run: dict, now: datetime) -> bool:
        """Has a manual "clean now" outlived the intent behind it?

        Only true once it actually docked to recharge AND the run has been going
        for hours. Merely having parked is NOT enough — a run that stopped briefly
        because it got stuck is still the run the user asked for 8 minutes ago,
        and treating that as stale made the engine drag the robot home mid-test
        while the user stood watching it (live 2026-07-17)."""
        if not run.get("was_parked"):
            return False
        started = _parse_iso(run.get("started"))
        if started is None:
            return False
        return (now - started).total_seconds() >= MANUAL_INTENT_STALE_SECONDS

    def _where_parked(self) -> str:
        """Honest wording for the status line. Saying 'paused at the dock' while
        the robot sits stranded mid-floor actively misleads (live 2026-07-17)."""
        return "at the dock" if self._at_dock() else "away from the dock — it may be stuck"

    async def _watch_stranded(self, now: datetime) -> None:
        """Alert when the robot is left sitting away from its dock with NO run in
        flight — the duty-of-care gap. _watch_silent_stuck only runs while a run
        is active, so the moment a run finalises (or is interrupted and sent
        home) nothing checks whether the robot ever actually made it back. Live
        2026-07-15: the run finalised at 21:00, the robot then stalled at a no-go
        en route to the dock and sat out all night, silently."""
        state = self._vacuum_state() or ""
        if self._robot_busy():
            self._stranded_pos = None
            self._stranded_since = None
            self._stranded_notified = False
            return
        pos = self._vacuum_position()
        if pos is None:
            return
        if self._at_dock(pos):                       # measured, not state-name guessed
            self._stranded_pos = None
            self._stranded_since = None
            self._stranded_notified = False
            return
        last = self._stranded_pos
        if last is None or math.hypot(pos[0] - last[0], pos[1] - last[1]) >= STUCK_MIN_ESCAPE_MM:
            self._stranded_pos = pos                 # still moving -> re-arm
            self._stranded_since = now
            self._stranded_notified = False
            return
        if self._stranded_notified or self._stranded_since is None:
            return
        stalled = (now - self._stranded_since).total_seconds()
        if stalled < STRANDED_SECONDS:
            return
        self._stranded_notified = True
        self._help_pending = True   # tell them "all clear" if it makes it home
        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        err = self._sval(entity_of("sensor", self._prefix, SUF_ERROR)) or "no error"
        await self._log_stuck({
            "ts": now.isoformat(), "room": where, "x": pos[0], "y": pos[1],
            "error": f"stranded ({err})", "kind": "idle", "attempt": 0,
            "run_id": None, "beached": False,
        })
        await self._queue_help(
            now,
            "⚠️ Vacuum stranded away from its dock",
            f"It's been sat near {where} for {int(stalled // 60)} min with no clean "
            f"running, and hasn't made it back to the dock (state: {state}, error: "
            f"{err}). It probably needs a hand.",
            actions=self._rescue_actions(),
        )
        _LOGGER.info("stranded near %s at %s for %ss (state=%s, error=%s)",
                     where, pos, int(stalled), state, err)

    async def _watch_silent_stuck(self, run: dict, now: datetime) -> None:
        """Detect a robot that's stopped somewhere away from its dock during a run
        and hasn't errored — the silent stuck ('unable to reach', a quiet
        high-centre, a give-up mid-return) that the error-driven recovery can't
        see. Deliberately does NOT care what the vacuum state says: cleaning,
        returning, paused and idle all count, because the only question that
        matters is "is it away from the dock and not moving?". Detect + log +
        notify once; re-arms the moment the robot moves again."""
        pos = self._vacuum_position()
        if pos is None:
            return
        last = run.get("last_pos")
        moved_far = (not isinstance(last, (list, tuple)) or len(last) < 2
                     or math.hypot(pos[0] - last[0], pos[1] - last[1]) >= STUCK_MIN_ESCAPE_MM)
        if moved_far:
            run["last_pos"] = [pos[0], pos[1]]
            run["last_moved_at"] = now.isoformat()
            run.pop("silent_stuck_notified", None)   # moved again -> re-arm
            await self.tracker.async_set_active_run(run)
            return
        # Stationary. Decide "is it safely home?" by MEASURING the distance to the
        # dock — never by the state name. `idle` means "doing nothing", NOT "at the
        # dock": a robot idle in the middle of the floor is the stranded case, and
        # gating on _PARKED_STATES here silently skipped exactly that (live
        # 2026-07-17: stranded at the couch in `idle`, no alert, run held open
        # claiming "paused at the dock" 8 m away from it).
        if self._at_dock(pos):
            return
        if self._error_active() or run.get("silent_stuck_notified"):
            return  # errors go through auto-recover / beach handling instead
        moved_at = _parse_iso(run.get("last_moved_at"))
        stalled = (now - moved_at).total_seconds() if moved_at else 0
        if stalled < SILENT_STUCK_SECONDS:
            return
        run["silent_stuck_notified"] = True
        self._help_pending = True   # so the "all clear" fires when it's home again
        await self.tracker.async_set_active_run(run)
        where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
        await self._log_stuck({
            "ts": now.isoformat(), "room": where,
            "x": pos[0], "y": pos[1], "error": "no_progress",
            "kind": run.get("kind"), "attempt": 0,
            "run_id": run.get("started"), "beached": False,
        })
        if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
            mins = int(stalled // 60)
            await self._queue_help(
                now,
                "⚠️ Vacuum may be stuck",
                f"It's sat near {where} for {mins} min without moving, but reported no "
                "error. It may be wedged somewhere it can't drive out of — worth a look.",
                actions=self._rescue_actions(),
            )
        _LOGGER.info("silent-stuck: no movement near %s for %ss", where, int(stalled))

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

    def _cleaned_area(self) -> float | None:
        """m² cleaned so far in the current task (the robot's own live counter),
        or None if unavailable. Used as the 'is it being productive' signal."""
        st = self.hass.states.get(self._vacuum_entity)
        val = st.attributes.get("cleaned_area") if st else None
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def _clean_progress(self) -> float | None:
        """The robot's own 0-100 task-progress %, or None if unavailable. Finer
        grained than cleaned_area (which is integer m²), so slow-but-real cleaning
        still reads as productive and doesn't trip the no-progress watchdog."""
        st = self.hass.states.get(self._vacuum_entity)
        val = st.attributes.get("cleaning_progress") if st else None
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def _consumable_readings(self) -> dict:
        """{consumable key: remaining-life % (int) or None} from the robot's own
        attributes. None where the sensor is missing/unavailable, so a part is
        never falsely flagged as worn out on a transient read."""
        st = self.hass.states.get(self._vacuum_entity)
        attrs = st.attributes if st else {}
        out: dict = {}
        for c in CONSUMABLES:
            raw = attrs.get(c["life_attr"])
            try:
                out[c["key"]] = int(float(raw)) if raw is not None else None
            except (TypeError, ValueError):
                out[c["key"]] = None
        return out

    def _presence_home(self) -> bool | None:
        """True if anyone home, False if all away, None if undeterminable.

        A presence entity that hasn't reported for longer than
        ``presence_stale_min`` (opt-in, 0 = off) is treated as STALE and ignored —
        a wedged phone tracker stuck at 'home' would otherwise silently block
        cleaning forever. If every entity is unavailable or stale, presence stays
        None (undeterminable), which the away-gate treats as 'not away' (fail-safe)."""
        ents = self._presence_entities()
        if not ents:
            return None
        stale_min = self._opt_int(OPT_PRESENCE_STALE_MIN, DEFAULT_PRESENCE_STALE_MIN)
        now = dt_util.now()
        seen = []
        for ent in ents:
            st = self.hass.states.get(ent)
            if st is None or str(st.state).lower() in _UNAVAILABLE:
                continue
            if stale_min > 0:
                last = getattr(st, "last_reported", None) or st.last_updated
                age = (now - last).total_seconds() if last is not None else None
                if tracker_stale(age, stale_min):
                    _LOGGER.debug("presence: ignoring stale %s (no report for %.0f min)",
                                  ent, (age or 0) / 60)
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

    def _door_sensors(self) -> list[str]:
        """Every distinct door/contact entity mapped to a room (for the watch
        list). Order-stable, de-duplicated."""
        seen: list[str] = []
        for cfg in self._rooms().values():
            ent = cfg.get(ROOM_DOOR_SENSOR) if isinstance(cfg, dict) else None
            if ent and ent not in seen:
                seen.append(ent)
        return seen

    # A wedge at the EDGE of a room whose door is shut is a doorway-catch, not a
    # permanent trap — the robot bumps the closed door from the adjoining room.
    # We key it on POSITION + the room's map box (not on which room the sensor is
    # assigned to), so it works whether the door sensor sits on that room or its
    # neighbour. Fed into the stuck log as `door_closed` so the trap-learner never
    # turns it into a no-go (which would wall the doorway shut for good).
    _DOOR_EDGE_MM = 500

    def _stuck_at_shut_door(self, x, y) -> bool:
        try:
            px, py = int(x), int(y)
        except (TypeError, ValueError):
            return False
        for seg in self._rooms():
            ds = self._door_state(seg)
            if ds is None or str(ds).lower() not in ("off", "closed", "false"):
                continue
            box = self._room_box(seg)
            if not box or len(box) < 4:
                continue
            rx0, ry0 = min(box[0], box[2]), min(box[1], box[3])
            rx1, ry1 = max(box[0], box[2]), max(box[1], box[3])
            m = self._DOOR_EDGE_MM
            if (rx0 - m) <= px <= (rx1 + m) and (ry0 - m) <= py <= (ry1 + m):
                return True
        return False

    async def _log_stuck(self, payload: dict) -> None:
        """Log a stuck event, tagging whether it happened at a shut room's doorway
        so the trap-learner can keep transient door-catches out of no-go suggestions."""
        payload["door_closed"] = self._stuck_at_shut_door(payload.get("x"), payload.get("y"))
        await self.tracker.async_log_stuck(payload)

    def _room_boxes_by_name(self) -> dict:
        """Each room's NAME -> its map bounding box, for clamping a learned no-go to
        the room it's in (keyed by name to match the stuck event's `room` field)."""
        out: dict = {}
        for seg in self._rooms():
            box = self._room_box(seg)
            if box:
                out[self._room_name(seg)] = box
        return out

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
        # Finish any deferred edge-settings restore FIRST — even when the scheduler is
        # disabled — so a completed/interrupted edge run never leaves customized
        # cleaning off (which would silently break normal per-room runs).
        await self._apply_pending_edge_restore()

        # Passively learn each room's real cleaned area (read-only) — feeds the edge
        # cut's self-tuning. Runs regardless of enabled state; harmless if learning off.
        try:
            await self._observe_room_areas()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("room-area observe failed: %s", exc)

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

        # 2a2) Resolve any held "needs help" alert BEFORE the all-clear: if the
        # robot is cleaning again it's cancelled silently, else it's sent once the
        # grace window elapses. Runs every tick, run in flight or not.
        try:
            await self._flush_help(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("held-alert flush failed: %s", exc)

        # 2b) Recovered and docked after a "needs help" alert? Send the all-clear
        # (runs every tick, whether or not a run is in flight).
        try:
            await self._maybe_all_clear(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("all-clear check failed: %s", exc)

        # 2c) Moving but getting nowhere (circling / repositioning / Blocked while
        # returning)? Ask for a hand — catches what the movement watchdogs miss,
        # for manual/native runs too. Runs every tick, before the active-run branch.
        try:
            await self._watch_no_progress(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("no-progress watch failed: %s", exc)

        # 2d) Wear-part life check (filter/brush/mop/sensors/detergent) — throttled
        # internally, alerts once per part with reset/dismiss buttons.
        try:
            await self._check_consumables(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("consumable check failed: %s", exc)

        # 3) A run is in flight → verify/await it; never dispatch concurrently.
        if self.tracker.active_run is not None:
            await self._check_active_run(now, away_ok)
            return

        # 3a) A dedicated edge clean in flight → drive it room-by-room.
        if self.tracker.edge_run is not None:
            await self._check_edge_run(now, away_ok)
            return

        # No run in flight — but the robot may still be stranded out there from a
        # finished/interrupted one. Nothing else watches for that.
        try:
            await self._watch_stranded(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("stranded watch failed: %s", exc)

        # Pre-run tidy heads-up (once/day, at the user's chosen trigger) — best-
        # effort, never let it block the schedule.
        try:
            await self._maybe_prerun_announce(now, weekday, now_min)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("pre-run announce failed: %s", exc)

        # 3a2) Extended-away "holiday": once the house is clean and everyone's been
        #     gone a while, stop re-cleaning an already-clean empty house until they
        #     return (or the weekly reset re-fills pending). Safe here — it only holds
        #     when nothing is pending, so there's no half-done/suspended run to strand.
        if self._holiday_hold(now):
            self._set_status("idle", "holiday — house already clean, cleaning paused")
            return

        # 3b) An interrupted run waiting for the house to empty again?
        if self.tracker.resume and bool(self._opt(OPT_RESUME_WHEN_AWAY, DEFAULT_RESUME_WHEN_AWAY)):
            if await self._try_resume(now, now_min, away_ok):
                return

        # 3c) A room skipped for a shut door whose door has now been open long
        #     enough to retry (opt-in; may run while home if the user allowed it).
        if await self._maybe_door_retry(now, now_min, away_ok):
            return

        # 3d) Dedicated edge clean due on its own cadence? Presence-gated like a run.
        if self._edge_schedule_due(now, now_min) and away_ok:
            if await self._start_edge_run(all_enabled_segments(self._rooms()), manual=False):
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
            slots_done=self.tracker.slots_done_today(today.isoformat()),
            opportunistic_catchup=bool(self._opt(OPT_OPPORTUNISTIC_CATCHUP, DEFAULT_OPPORTUNISTIC_CATCHUP)),
        )

        if decision.action == "idle":
            # Nothing due, OR the day/week is already handled — record that so we
            # don't re-scan every tick, and surface a friendly idle status.
            if decision.kind == "daily" and decision.reason == "nothing_due_today":
                await self.tracker.async_set_day_dispatched(today.isoformat())
            elif decision.kind == "catchup" and decision.reason == "week_already_complete":
                await self.tracker.async_set_catchup_dispatched(today.isoformat())
            await self._maybe_stale_nudge(now, away_ok)
            try:
                await self._sync_manual_clean(now)   # catch time-based staleness
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("manual-clean sync failed: %s", exc)
            self._set_status("idle", self._idle_reason(now, away_ok))
            return

        # 5) A dispatch is due — apply live gates (door / presence / window /
        #    battery / station) before actually sending the robot out.
        await self._attempt_dispatch(decision, now, now_min, away_ok)

    def _holiday_hold(self, now: datetime) -> bool:
        """True when extended-away 'holiday' should pause the scheduled clean:
        enabled, everyone away, gone >= holiday_after_days, and the house is already
        clean (nothing pending this week). See scheduler.holiday_hold for the rule."""
        if not bool(self._opt(OPT_HOLIDAY_ENABLED, DEFAULT_HOLIDAY_ENABLED)):
            return False
        if self._presence_home() is not False:      # someone home, or presence unknown
            return False
        away = _parse_iso(self.tracker.away_since)
        if away is None:
            return False
        house_clean = not pending_rooms(self._rooms(), self.tracker.cleaned)
        return holiday_hold(
            True, (now - away).days,
            self._opt_int(OPT_HOLIDAY_AFTER_DAYS, DEFAULT_HOLIDAY_AFTER_DAYS),
            house_clean,
        )

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
            retry_on = bool(self._opt(OPT_DOOR_RETRY_ENABLED, DEFAULT_DOOR_RETRY_ENABLED))
            if retry_on:
                # Remember them so the door-retry watcher can send the robot back
                # once the door has been open long enough (see _maybe_door_retry).
                await self.tracker.async_mark_door_deferred(blocked, today_iso)
            if bool(self._opt(OPT_NOTIFY_SKIPPED, DEFAULT_NOTIFY_SKIPPED)):
                names = ", ".join(self._room_name(s) for s in blocked)
                if retry_on:
                    mins = self._opt_int(OPT_DOOR_RETRY_MIN, DEFAULT_DOOR_RETRY_MIN)
                    tail = f"Will retry once the door's been open for {mins} min."
                else:
                    tail = "Will retry on the catch-up day."
                await self._notify("Vacuum: rooms skipped", f"Door closed — skipped {names}. {tail}")

        if not reachable:
            await self._mark_dispatched(decision, today_iso)
            self._set_status("idle", "all due rooms unreachable (doors closed)")
            return

        # d) All clear — go. A per-room-times ("slot") dispatch forces sweep-only
        #    on the slots the user tagged "vacuum only".
        await self._dispatch_clean(
            reachable, decision.kind, now,
            sweep_only=set(decision.sweep_only) & set(reachable),
        )
        await self._mark_dispatched(decision, today_iso)

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

    async def _mark_dispatched(self, decision, today_iso: str) -> None:
        if decision.kind == "catchup":
            await self.tracker.async_set_catchup_dispatched(today_iso)
        elif decision.kind == "slot":
            # Extra-times run: mark just the fired slot-keys, NOT the whole day —
            # a room's later slots (and the global daily fire) must still run.
            await self.tracker.async_mark_slots(today_iso, decision.slot_keys)
        else:
            await self.tracker.async_set_day_dispatched(today_iso)

    async def _maybe_door_retry(self, now: datetime, now_min: int, away_ok: bool) -> bool:
        """Retry a room skipped earlier today for a shut door, once that door has
        been open for the configured time. Opt-in. Honours the away gate unless
        the user allowed retrying while home — in which case the open-for-N-min
        timer is the sole proof the room is clear (the shower-door case). Returns
        True if it dispatched a retry."""
        if not bool(self._opt(OPT_DOOR_RETRY_ENABLED, DEFAULT_DOOR_RETRY_ENABLED)):
            return False
        deferred = self.tracker.door_deferred
        if not deferred:
            return False
        today_iso = now.date().isoformat()
        mins = self._opt_int(OPT_DOOR_RETRY_MIN, DEFAULT_DOOR_RETRY_MIN)
        while_home = bool(self._opt(OPT_DOOR_RETRY_WHILE_HOME, DEFAULT_DOOR_RETRY_WHILE_HOME))
        rooms = self._rooms()
        cleaned = self.tracker.cleaned

        ready: list[str] = []
        for seg, info in list(deferred.items()):
            # Drop stale (previous-day) or already-cleaned entries.
            if not isinstance(info, dict) or info.get("date") != today_iso:
                await self.tracker.async_clear_door_deferred(seg)
                continue
            if str(seg) in cleaned:
                await self.tracker.async_clear_door_deferred(seg)
                continue
            if int(info.get("retries", 0)) >= MAX_DOOR_RETRIES_PER_DAY:
                continue
            # Presence: away is always fine; while-home leans on the open-timer.
            if not away_ok and not while_home:
                continue
            ent = (rooms.get(seg, {}) or {}).get(ROOM_DOOR_SENSOR)
            st = self.hass.states.get(ent) if ent else None
            if st is None:
                continue
            if not door_open_long_enough(st.state, st.last_changed, now, mins):
                continue
            ready.append(seg)

        if not ready:
            return False

        # Window / battery / station preconditions, same as any fresh start.
        gates_ok, reason = self._start_gates_ok(now_min)
        if not gates_ok:
            self._set_status("waiting", reason)
            return False

        for seg in ready:
            await self.tracker.async_bump_door_retry(seg, today_iso)
        _LOGGER.info("door-retry: door open >= %s min -> re-cleaning %s", mins, ready)
        await self._dispatch_clean(ready, "daily", now)
        # Started under the while-home allowance? Flag the run so the
        # return-on-arrival guard doesn't immediately dock it for being home.
        if not away_ok:
            run = self.tracker.active_run
            if run is not None:
                run["door_retry_home"] = True
                await self.tracker.async_set_active_run(run)
        return True

    def _pick_seq_mode(self) -> str | None:
        """Best available global sweep-then-mop mode for this vacuum, or None if
        it exposes neither (run then just uses whatever global mode is set)."""
        st = self.hass.states.get(entity_of("select", self._prefix, SUF_CLEANING_MODE))
        options = list(st.attributes.get("options", [])) if st else []
        return next((m for m in SEQ_MOP_MODES if m in options), None)

    async def _send_clean_segment(self, int_segments: list[int],
                                  suction_level: int | None = None) -> None:
        """Start a segment clean, coping with the deep-'Sleeping' no-op: a robot
        in standby silently ignores clean_segment (seen live 2026-07-15), leaving
        the engine tracking a run that never began. Verify it actually started;
        if not, wake it with vacuum.locate and re-send once.

        ``suction_level`` (0-3) is baked into the service call — the edge run uses
        it to force a QUIET pass regardless of each room's saved (often Turbo)
        setting, WITHOUT touching the suction select, which the cloud 500s live."""
        data = {"entity_id": self._vacuum_entity, "segments": int_segments}
        if suction_level is not None:
            data["suction_level"] = suction_level
        await self._svc(DREAME_DOMAIN, SERVICE_CLEAN_SEGMENT, data)
        if await self._await_started(WAKE_VERIFY_SECONDS):
            return
        _LOGGER.info("clean_segment ignored (robot asleep?) — waking and retrying")
        await self._svc("vacuum", "locate", {"entity_id": self._vacuum_entity})
        await self._await_awake(WAKE_SETTLE_SECONDS)
        await self._svc(DREAME_DOMAIN, SERVICE_CLEAN_SEGMENT, data)
        await self._await_started(WAKE_VERIFY_SECONDS)

    async def _resume_run(self, run: dict | None) -> None:
        """Resume a run by re-dispatching ITS OWN segments — never a bare
        vacuum.start. On the Dreame, vacuum.start expands to a WHOLE-HOUSE clean
        the moment the segment task has ended (not merely paused). Live
        2026-07-20: a door-blocked Toilet [6] run's recovery vacuum.start launched
        all 15 rooms while the user was home. Re-issuing clean_segment keeps it to
        the rooms we actually dispatched, whether the task is paused or ended."""
        segs = [int(s) for s in (run or {}).get("segments", []) if str(s).isdigit()]
        if segs:
            await self._send_clean_segment(segs)
        else:
            # No segments to constrain to — do NOTHING rather than let a bare
            # vacuum.start clean the whole house unbidden.
            _LOGGER.warning("resume requested with no run segments — skipping (won't whole-house)")

    async def _await_started(self, timeout: float) -> bool:
        """Poll briefly for the robot to actually begin working (or error out)."""
        waited = 0.0
        while waited < timeout:
            if self._robot_busy() or self._error_active():
                return True
            await asyncio.sleep(WAKE_POLL_SECONDS)
            waited += WAKE_POLL_SECONDS
        return self._robot_busy() or self._error_active()

    async def _await_awake(self, timeout: float) -> None:
        """Wait (bounded) for the robot to leave deep 'Sleeping' after a locate,
        so the re-sent clean_segment lands on an awake robot."""
        waited = 0.0
        while waited < timeout:
            status = (self._sval(entity_of("sensor", self._prefix, SUF_STATUS)) or "").lower()
            if "sleep" not in status and self._vacuum_state() is not None:
                return
            await asyncio.sleep(WAKE_POLL_SECONDS)
            waited += WAKE_POLL_SECONDS

    async def _dispatch_clean(self, segments: list[str], kind: str, now: datetime,
                              quiet: bool = False, sweep_only=None) -> None:
        """Apply per-room settings (via customized cleaning) then start the
        segment clean. Every service call is best-effort so a model missing one
        of the optional per-room entities still cleans. ``quiet`` forces the
        configured quiet suction on every room (for cleaning while home).
        ``sweep_only`` is a set of segment-ids to clean sweep-only this run
        regardless of their mop cadence (a per-room "vacuum only" extra pass)."""
        sweep_only = set(sweep_only or ())
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
        # "Vacuum before mop" mops the whole house via the global sequential mode,
        # which DISCARDS the robot's native per-room modes. Skip it when we're
        # honoring native settings (the default) so a room you set to sweep-only
        # in the Dreame app is not force-mopped. Live 2026-08-03: wet pads dragged
        # onto the Main Room carpet and stalled the right wheel, because this
        # override mopped a room natively set to sweep.
        honor_native = bool(self._opt(OPT_HONOR_NATIVE, DEFAULT_HONOR_NATIVE))
        deep = (bool(self._opt(OPT_VACUUM_BEFORE_MOP, DEFAULT_VACUUM_BEFORE_MOP))
                and not quiet and not honor_native)

        if deep:
            # Vacuum-before-mop: sweep the whole area, THEN mop it (no smearing).
            # Uses the GLOBAL sequential mode, so per-room modes/suction don't
            # apply this run — the robot does one sweep-then-mop pass over all
            # target rooms. (Only reached when honor-native is OFF.)
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
            today_iso = now.date().isoformat()
            applied: dict = {}   # seg -> resolved {mode, will_mop} for the run history
            for seg in segments:
                cfg = rooms.get(seg, {})
                base_mode = cfg.get(ROOM_MODE) or default_mode
                # Per-room "mop every N sweep-days" cadence: on off-cadence days a
                # mopping room sweeps only; N<=1 (default) leaves the mode as-is.
                # The counter advances once per calendar day (see
                # advance_mop_cadence), so a resume/manual re-dispatch today keeps
                # the same decision.
                try:
                    mop_every = int(cfg.get(ROOM_MOP_EVERY, DEFAULT_ROOM_MOP_EVERY))
                except (TypeError, ValueError):
                    mop_every = DEFAULT_ROOM_MOP_EVERY
                prev = self.tracker.mop_counters.get(str(seg)) or {}
                count, mode, will_mop = advance_mop_cadence(
                    base_mode, mop_every, prev.get("count"), prev.get("date"), today_iso)
                if is_mopping_mode(base_mode) and mop_every > 1:
                    await self.tracker.async_set_mop_counter(seg, count, today_iso)
                # A per-room "vacuum only" extra-time slot forces sweep-only for
                # this pass, whatever the cadence resolved (composes with never-mop).
                if str(seg) in sweep_only:
                    mode, will_mop = SWEEP_ONLY_MODE, False
                # Remember what this room actually ran as (sweep vs mop), so the
                # run history is auditable — "" means we deferred to the robot's
                # native per-room mode. This is what makes never-mop / sweep-only
                # verifiable after the fact (carpet safety).
                applied[str(seg)] = {"mode": mode or "native", "will_mop": bool(will_mop)}
                suction = (quiet_suction if quiet and quiet_suction
                           else (cfg.get(ROOM_SUCTION) or default_suction))
                wetness = cfg.get(ROOM_WETNESS)
                if mode:
                    await self._select(room_entity("select", self._prefix, seg, "cleaning_mode"), mode)
                if suction:
                    await self._select(room_entity("select", self._prefix, seg, "suction_level"), suction)
                if will_mop and wetness not in (None, ""):
                    await self._svc("number", "set_value", {
                        "entity_id": room_entity("number", self._prefix, seg, "wetness_level"),
                        "value": wetness,
                    })
            # Stash the resolved per-room modes on the active run for the history.
            run = self.tracker.active_run
            if isinstance(run, dict):
                run["applied_modes"] = applied
                await self.tracker.async_set_active_run(run)

        int_segments = [int(s) for s in segments if str(s).isdigit()]
        await self._send_clean_segment(int_segments)
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

        # Safety net: a run that flagged an ERROR but whose robot has since made it
        # back to the dock — no active error, not cleaning, not washing, not mid-
        # recovery — must not sit open forever blocking the next dispatch. Finalise
        # it as interrupted so the un-done rooms defer to the catch-up. We require
        # the `errored` flag, so a HEALTHY suspended run (paused for presence,
        # waiting to resume when the house empties) is untouched — only an errored
        # one is cleared, since an errored task can't cleanly resume from its
        # breakpoint anyway. (Live 2026-08-12: a 'mop install failed' run went
        # suspended+errored and sat wedged ~9 h; SUSPEND_MAX_SECONDS is a 24 h
        # backstop, far too slow to unblock the next day's run.)
        st = self.hass.states.get(self._vacuum_entity)
        servicing = bool(st and (st.attributes.get("washing") or st.attributes.get("drying")))
        if (run.get("errored") and not run.get("recovering")
                and not self._error_active() and not servicing
                and self._at_dock()
                and (self._vacuum_state() or "") in ("docked", "idle", "paused")):
            _LOGGER.info("finalising orphaned errored run — robot docked and clear again")
            await self._finalize_run(run, now, interrupted=True)
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
                # Map-resume wants the firmware to continue the PAUSED breakpoint
                # (the un-cleaned area). Only a bare vacuum.start does that — but
                # if the paused task is gone, vacuum.start whole-houses, so guard
                # on the robot actually reporting a paused/segment task and
                # otherwise re-dispatch just our segments.
                va = self.hass.states.get(self._vacuum_entity)
                paused_task = bool(va and (va.attributes.get("cleaning_paused")
                                           or va.attributes.get("paused")
                                           or va.attributes.get("segment_cleaning")))
                if paused_task:
                    await self._svc("vacuum", "start", {"entity_id": self._vacuum_entity})
                else:
                    await self._resume_run(run)
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
                # what happened live 2026-07-08). But only if it has GENUINELY
                # escaped the dock — a mop-pad wash cycle also reads as 'cleaning'
                # and would loop return_to_base for the whole wash (live 2026-08-08).
                if self._escaped_cleaning():
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
                        await self._queue_help(
                            now,
                            "⚠️ Vacuum needs help",
                            f"The robot errored near {where} while paused for someone being home.",
                            actions=self._rescue_actions(),
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

        # Silent stuck: stopped away from the dock, not moving, not erroring.
        # Detect/alert (once) before the error + completion logic.
        if run.get("seen_active"):
            await self._watch_silent_stuck(run, now)

        # Overreach guard: the robot is cleaning rooms we never dispatched (a
        # stale whole-house task bleeding back in — live 2026-07-20). Rein it in:
        # send it home and finalise as interrupted, so it can't clean the house
        # unbidden. Mark interrupting once so we don't fight it every tick.
        if self._robot_overreaching(run) and not run.get("interrupting"):
            stray = sorted(self._robot_active_segments()
                           - {int(s) for s in run.get("segments", []) if str(s).isdigit()})
            stray_names = ", ".join(self._room_name(str(s)) for s in stray) or "another room"
            raw = self._error_text()
            _LOGGER.warning("robot overreaching: cleaning %s, dispatched %s (stray=%s, error=%s) — reining in",
                            self._robot_active_segments(), run.get("segments"), stray_names, raw)
            run["interrupting"] = True
            run["errored"] = True
            await self.tracker.async_set_active_run(run)
            await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            self._set_status("returning",
                             f"reining it in — it strayed into {stray_names}"
                             + (f" [{raw}]" if raw else ""))
            if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                body = (f"Its firmware resumed a stale task and it started cleaning "
                        f"{stray_names} — not on today's list. Sent it back to the dock.")
                if raw:
                    body += f"\n\nReported error: {raw}"
                await self._notify("⚠️ Vacuum overreached", body, high_priority=True)
            return

        # An 'interrupting' run was already sent home — but the firmware can
        # auto-resume the paused segment task on its own (seen live 2026-07-08).
        # Keep enforcing the dock while someone is home, mirroring the
        # suspended-branch enforcement; without this the presence check below
        # (which skips interrupting runs) never sends it back.
        if (run.get("interrupting") and self._escaped_cleaning()
                and self._presence_home() is True):
            await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
            self._set_status("returning", "someone home — sending back to the dock")
            return

        # Someone came home mid-run → retreat to the dock so we don't annoy them.
        # Manual runs opt out (the user tapped "clean now") — EXCEPT once that
        # intent has gone STALE: it docked to recharge and auto-resumed hours
        # later, by which point "clean now" no longer reflects what they want.
        if ((run.get("kind") != "manual" or self._manual_intent_stale(run, now))
                and not run.get("door_retry_home")
                and not run.get("interrupting")
                and not self._home_or_servicing()   # already docked/washing — nothing to send home
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
            # Is auto-recover actually going to deal with this, or are we about to
            # stay quiet waiting for a rescue that will never come? Recovery only
            # touches errors in _RECOVERABLE_ERROR_WORDS, so an UNRECOGNISED error
            # must alert immediately — never assume the word list covers it.
            # Live 2026-07-17: a `blocked` error fell through every layer in
            # silence (not a recover word -> auto-recover skipped it; an error was
            # active -> the silent-stuck watchdog skipped it; auto-recover was ON
            # with 0 attempts spent -> this gate assumed recovery had it). Same
            # shape as the `drop` hole two days earlier. The word list must gate
            # RECOVERY, never gate ALERTING.
            recover_done = (not bool(self._opt(OPT_AUTO_RECOVER, DEFAULT_AUTO_RECOVER))
                            or not self._recoverable_error()
                            or int(run.get("recover_count", 0)) >= MAX_RECOVER_ATTEMPTS)
            if recover_done and not run.get("notified_stuck"):
                run["notified_stuck"] = True
                changed = True
                if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                    where = self._sval(entity_of("sensor", self._prefix, "current_room")) or "somewhere"
                    await self._queue_help(
                        now,
                        "⚠️ Vacuum needs help",
                        f"The robot reported an error near {where} during the {run['kind']} clean.",
                        actions=self._rescue_actions(),
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
                    # Only a real DOCK visit is a recharge. Stopping mid-floor
                    # (stuck) is not, and must not age out a manual run's intent.
                    run["was_parked"] = self._at_dock()
                    await self.tracker.async_set_active_run(run)
                parked = _parse_iso(run.get("parked_since"))
                dwell = (now - parked).total_seconds() if parked else 0
                if dwell < INCOMPLETE_RECORD_DWELL_SECONDS or self._charging_mid_task():
                    self._set_status(
                        "running",
                        f"{run['kind']} paused {self._where_parked()} — waiting for the robot to resume",
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
            # Only a real DOCK visit counts (see above) — a mid-floor stop is a
            # stuck robot, not a recharge.
            run["was_parked"] = self._at_dock()
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
                    f"{run['kind']} paused {self._where_parked()} — waiting for the robot to resume",
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
        # Safety net: never leave the speaker muted if a run ended mid-maneuver.
        await self._restore_voice(run)

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
        applied_modes = run.get("applied_modes", {}) if isinstance(run, dict) else {}
        per_room: dict = {}
        for seg in cleaned:
            am = applied_modes.get(str(seg), {})
            # Prefer the mode actually applied this run (sweep vs mop); fall back to
            # the configured base mode for older/edge dispatches without it recorded.
            per_room[seg] = {
                "status": "cleaned",
                "mode": am.get("mode") or cfg_rooms.get(seg, {}).get(ROOM_MODE) or default_mode or None,
                "will_mop": am.get("will_mop"),
            }
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

        # Learn from any traps hit this run — surface a no-go suggestion for a
        # recurring spot, or a tidy reminder for loose-object beachings. Best-
        # effort: never let analytics break a completed run's finalisation.
        try:
            await self._maybe_surface_learned(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("trap-learner surfacing failed: %s", exc)
        try:
            await self._sync_manual_clean(now)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("manual-clean sync failed: %s", exc)

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

    async def _maybe_surface_learned(self, now: datetime) -> None:
        """After a run, run the recurring-trap learner over the stuck-event log
        and SUGGEST (never silently apply — writing to the robot is user-
        confirmed) a permanent no-go for any spot that has trapped the robot on
        enough separate runs. One-off / mobile hazards (a pet toy) instead raise
        a 'tidy the floor' reminder, since a no-go can't fix a thing that moves."""
        events = list(self.tracker.stuck_events or [])
        if not events:
            return
        try:
            _walls, zones, _mops = self._current_zones()
        except ZoneReadError:
            zones = []   # read-only here (dedup against existing zones); safe to skip
        report = trap_learner.analyze(events, zones, room_boxes=self._room_boxes_by_name())
        suggested = self.tracker.learned_suggested
        promoted = self.tracker.learned_promoted
        for s in report["nogo_suggestions"]:
            if s["key"] in suggested or s["key"] in promoted:
                continue
            await self.tracker.async_mark_suggested(s["key"], now.isoformat())
            if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                await self._notify(
                    "🧠 Recurring trap learned",
                    f"The robot has got stuck at {s['room']} on {s['runs']} separate "
                    "runs. Add a permanent no-go there so it stops trying? Apply it "
                    "from the add-on's Insights tab, or call the "
                    "dreame_scheduler.apply_learned_nogo service.",
                )
        # Tidy reminder — only while beachings are RECENT (a lifetime count would
        # nag forever off the never-expiring event log). Fires at most once/day,
        # and goes quiet once the robot hasn't been beached for a couple of days.
        advice = report.get("tidy_advice") or {}
        if advice.get("active") and self.tracker.tidy_reminded_on != now.date().isoformat():
            cutoff = now - timedelta(days=TIDY_RECENCY_DAYS)
            recent_beach = False
            for ev in reversed(events):
                ets = _parse_iso(ev.get("ts"))
                if ets and ets < cutoff:
                    break
                if ev.get("beached"):
                    recent_beach = True
                    break
            if recent_beach:
                await self.tracker.async_set_tidy_reminded_on(now.date().isoformat())
                if bool(self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)):
                    await self._notify("🧹 Clear the floor before cleans", advice.get("message", ""))

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

    async def _sync_manual_clean(self, now: datetime) -> None:
        """Maintain the 'clean by hand' to-do list: rooms the robot persistently
        CAN'T finish (unreachable — couch-blocked, always-shut door) so the user
        cleans them and the house is actually clean, rather than a room silently
        rotting on the pending list. Auto-clears a room the moment it next gets
        cleaned. Runs after every finalize; pure bookkeeping over data we keep."""
        if not bool(self._opt(OPT_MANUAL_CLEAN_ENABLED, DEFAULT_MANUAL_CLEAN_ENABLED)):
            if self.tracker.manual_clean:
                await self.tracker.async_set_manual_clean({})
                self._push()
            return
        stale_days = self._opt_int(OPT_MANUAL_CLEAN_STALE_DAYS, DEFAULT_MANUAL_CLEAN_STALE_DAYS)
        min_misses = self._opt_int(OPT_MANUAL_CLEAN_MIN_MISSES, DEFAULT_MANUAL_CLEAN_MIN_MISSES)
        history = list(self.tracker.history or [])
        unreachable = self.tracker.unreachable
        cleaned = self.tracker.cleaned
        prev = self.tracker.manual_clean
        targets: dict = {}
        for seg, cfg in self._rooms().items():
            if not isinstance(cfg, dict) or not cfg.get(ROOM_ENABLED) or not cfg.get(ROOM_DAYS):
                continue                                   # only SCHEDULED rooms
            # Persistent-failure evidence: the worse of the cross-week history
            # streak and this week's skip strikes.
            misses = max(hist_a.consecutive_fails(history, str(seg)),
                         int(unreachable.get(str(seg), 0)))
            if misses < min_misses:
                continue
            # How long since it was actually cleaned (history survives weekly
            # resets; fall back to this week's cleaned mark).
            last = hist_a.last_cleaned_ts(history, str(seg)) or cleaned.get(str(seg))
            last_dt = _parse_iso(last)
            days = (now - last_dt).days if last_dt else None
            if days is not None and days < stale_days:
                continue                                   # cleaned recently enough
            reason = (f"robot can't reach it — missed {misses}×"
                      + (f", last cleaned {days}d ago" if days is not None else ", never cleaned"))
            targets[str(seg)] = {
                "name": self._room_name(seg),
                "reason": reason,
                # keep the original "added" time so the task doesn't churn
                "added": (prev.get(str(seg), {}) or {}).get("added") or now.isoformat(),
            }
        if targets == prev:
            return
        new = [t for s, t in targets.items() if s not in prev]
        await self.tracker.async_set_manual_clean(targets)
        self._push()                                       # refresh the to-do entity
        if new and bool(self._opt(OPT_MANUAL_CLEAN_NOTIFY, DEFAULT_MANUAL_CLEAN_NOTIFY)):
            names = ", ".join(t["name"] for t in new)
            await self._notify(
                "🧹 Rooms the robot can't reach",
                f"Added to your clean-by-hand list: {names}. The robot keeps failing "
                "to get to them — give them a quick go by hand.",
            )
        _LOGGER.info("manual-clean list now: %s", list(targets.keys()))

    def _map_room_centroid(self, seg) -> tuple[int, int] | None:
        """Map-mm centre of a room segment, from the camera's room table."""
        rooms = _plain_attr(self._map_attr("rooms"))
        if not isinstance(rooms, dict):
            return None
        r = _plain_attr(rooms.get(str(seg)) or rooms.get(int(seg)) if str(seg).isdigit() else rooms.get(str(seg)))
        if isinstance(r, dict) and r.get("x") is not None and r.get("y") is not None:
            return int(r["x"]), int(r["y"])
        return None

    async def _await_settled(self, timeout: float) -> bool:
        """Wait (bounded) for the robot to first BEGIN moving, then come to rest —
        it's arrived, or got as close to the target as it can. Returns True if it
        actually moved, False if it never left (the goto was refused because the
        target is unreachable). The initial 'has it started' phase matters: a
        robot still at the dock reads as already-settled, so without it this
        returned instantly and the show-unreachable trip looked like a no-op."""
        start = self._vacuum_position()
        waited = 0.0

        # Phase 1 — wait for motion to begin (position moves, or it reports busy).
        moving = False
        while waited < min(timeout, MOVE_START_SECONDS):
            pos = self._vacuum_position()
            if self._robot_busy() or (
                pos is not None and start is not None
                and math.hypot(pos[0] - start[0], pos[1] - start[1]) >= STUCK_MIN_ESCAPE_MM
            ):
                moving = True
                break
            await asyncio.sleep(2.0)
            waited += 2.0
        if not moving:
            return False  # goto refused — robot never left the dock/spot

        # Phase 2 — it's under way; wait for it to come to rest.
        last = None
        while waited < timeout:
            pos = self._vacuum_position()
            if pos is not None and last is not None and \
                    math.hypot(pos[0] - last[0], pos[1] - last[1]) < STUCK_MIN_ESCAPE_MM:
                return True
            last = pos
            await asyncio.sleep(3.0)
            waited += 3.0
        return True

    def _approach_point(self, seg) -> tuple[int, int] | None:
        """A proven-REACHABLE point beside an unreachable room: the last place the
        robot physically stood when it gave up trying to reach it. vacuum_goto to
        the room's own centroid aborts (it's unreachable by definition), so the
        robot barely moves; the last stuck coordinate for this room is somewhere
        it actually reached, so a goto there succeeds and parks it right at the
        edge of the trouble spot — exactly what 'show me' wants to point at."""
        name = self._room_name(seg)
        best = None
        for ev in (self.tracker.stuck_events or []):
            if str(ev.get("room")) != str(name):
                continue
            x, y = ev.get("x"), ev.get("y")
            if x is None or y is None:
                continue
            best = (int(x), int(y))   # keep the most recent match
        return best

    async def async_show_unreachable(self, seg: str | None = None) -> None:
        """LAB: the robot quietly drives to the edge of a room it can't reach and
        marks the spot with its light, so you can see where a hand-clean is needed.

        It just GOES there — vacuum_goto is a cruise (navigation only, NO cleaning
        on the way) — and it's SILENT: the speaker is muted for the trip, so no
        beeps or announcements. The signal at the spot is VISUAL (the fill-light)
        plus a phone notification. It waits a moment there so you can spot it, then
        restores the volume and heads home. Experimental, opt-in, Labs-gated."""
        if not bool(self._opt(OPT_SHOW_UNREACHABLE, DEFAULT_SHOW_UNREACHABLE)):
            return
        async with self._eval_lock:
            if self.tracker.active_run is not None:
                self._set_status("running", "busy — can't show unreachable now")
                return
            targets = self.tracker.manual_clean
            seg = str(seg) if seg else next(iter(targets), None)
            if not seg or seg not in targets:
                return
            name = (targets[seg] or {}).get("name") or self._room_name(seg)
            # Aim at a point the robot can actually REACH (where it last got stuck
            # trying for this room), not the room's centroid — a goto to the
            # unreachable centroid aborts and the robot barely leaves the dock.
            target = self._approach_point(seg) or self._map_room_centroid(seg)
            if target is None:
                self._set_status("idle", f"can't work out where {name} is on the map")
                return
            vol_ent = entity_of("number", self._prefix, SUF_VOLUME)
            light = entity_of("switch", self._prefix, "fill_light")
            # Wake-guard: a deep-'Sleeping' robot silently ignores vacuum_goto the
            # same way it ignores clean_segment — locate to wake it first.
            status = (self._sval(entity_of("sensor", self._prefix, SUF_STATUS)) or "").lower()
            if "sleep" in status:
                await self._svc("vacuum", "locate", {"entity_id": self._vacuum_entity})
                await self._await_awake(WAKE_SETTLE_SECONDS)
            # Mute the speaker for the whole trip — keep it silent.
            saved_vol = None
            raw = self._sval(vol_ent)
            try:
                saved_vol = int(float(raw)) if raw is not None else None
            except (TypeError, ValueError):
                saved_vol = None
            if saved_vol:
                await self._svc("number", "set_value", {"entity_id": vol_ent, "value": 0})
            try:
                await self._svc(DREAME_DOMAIN, "vacuum_goto",
                                {"entity_id": self._vacuum_entity, "x": target[0], "y": target[1]})
                self._set_status("running", f"quietly going to show you: {name}")
                moved = await self._await_settled(150)
                # Signal at the spot — VISUAL only: fill-light on + a notification.
                if self.hass.states.get(light):
                    await self._svc("switch", "turn_on", {"entity_id": light})
                if moved:
                    await self._notify(
                        "🧹 Clean here for me",
                        f"I've driven to {name} and turned my light on where I'm parked — "
                        "that's the spot I can't reach. Can you give it a clean by hand?",
                        high_priority=True,
                    )
                else:
                    # The goto was refused — even the approach point wasn't reachable
                    # from here right now. Be honest rather than pretend it arrived.
                    await self._notify(
                        "🧹 Couldn’t drive to the spot",
                        f"I tried to show you where {name} is but couldn’t get moving — "
                        "the way there is blocked from where I am. It still needs a "
                        "clean by hand.",
                        high_priority=True,
                    )
                _LOGGER.info("show-unreachable: moved=%s at %s for %s",
                             moved, self._vacuum_position(), name)
                await asyncio.sleep(SHOW_DWELL_SECONDS)   # stay so you can spot it
            finally:
                if self.hass.states.get(light):
                    await self._svc("switch", "turn_off", {"entity_id": light})
                if saved_vol:
                    await self._svc("number", "set_value", {"entity_id": vol_ent, "value": saved_vol})
                await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})

    async def async_manual_room_done(self, seg: str) -> None:
        """User ticked a room off the 'clean by hand' list — credit it as cleaned
        (which also clears its skip strike) and drop it from the list."""
        seg = str(seg)
        await self.tracker.async_mark_cleaned([seg], dt_util.now().isoformat())
        mc = dict(self.tracker.manual_clean)
        if mc.pop(seg, None) is not None:
            await self.tracker.async_set_manual_clean(mc)
        self._push()
        _LOGGER.info("manual-clean: %s marked done by hand", seg)

    def _push(self) -> None:
        if self._notify_update:
            self._notify_update()

    async def _maybe_prerun_announce(self, now: datetime, weekday: int, now_min: int) -> None:
        """Fire the 'tidy the room before it cleans' heads-up once/day, at the
        trigger the user picked to fit their routine. The run itself is presence-
        gated, so the point is to reach someone while they're still HOME with time
        to clear loose pet toys / cables the robot can't map around."""
        if not bool(self._opt(OPT_PRERUN_ENABLED, DEFAULT_PRERUN_ENABLED)):
            return
        today_iso = now.date().isoformat()
        if self.tracker.prerun_announced == today_iso:
            return

        mode = str(self._opt(OPT_PRERUN_MODE, DEFAULT_PRERUN_MODE) or DEFAULT_PRERUN_MODE)
        daily = str(self._opt(OPT_DAILY_TIME, DEFAULT_DAILY_TIME))
        if mode == "lead":
            daily_min = clean_window.to_minutes(daily)
            fire_min = max(0, (daily_min or 0)
                           - self._opt_int(OPT_PRERUN_LEAD_MIN, DEFAULT_PRERUN_LEAD_MIN))
            target_weekday, tomorrow = weekday, False
        elif mode == "evening_before":
            fire_min = clean_window.to_minutes(self._opt(OPT_PRERUN_TIME, DEFAULT_PRERUN_TIME))
            target_weekday, tomorrow = (weekday + 1) % 7, True
        else:  # "morning"
            fire_min = clean_window.to_minutes(self._opt(OPT_PRERUN_TIME, DEFAULT_PRERUN_TIME))
            target_weekday, tomorrow = weekday, False
        if fire_min is None:
            return
        # Fire on the first tick at/after the trigger minute, within a 90-min
        # window (so a late HA start the same day still sends it); the once/day
        # guard stops repeats, and the date rolling over re-arms it next day.
        if not (fire_min <= now_min < fire_min + 90):
            return

        due = [s for s in rooms_due_today(self._rooms(), target_weekday)
               if s not in self.tracker.cleaned]
        # Mark announced regardless, so we don't re-scan every tick in the window.
        await self.tracker.async_set_prerun_announced(today_iso)
        if not due:
            return
        names = ", ".join(self._room_name(s) for s in due)
        require_away = bool(self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY))
        if tomorrow:
            title, when = "🧹 Vacuum cleans tomorrow", "tomorrow"
        else:
            title = "🧹 Vacuum cleans today"
            when = (f"today (from ~{daily}, once everyone's out)"
                    if require_away else f"today from ~{daily}")
        await self._notify(
            title,
            f"Scheduled to clean {names} {when}. A quick tidy — clear pet toys, "
            "cables and socks off the floor — keeps it from getting stuck.",
        )

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

    async def async_apply_learned_nogo(self, key: str | None = None) -> dict:
        """User-confirmed: turn recurring-trap suggestion(s) into a PERMANENT
        no-go on the robot's map. This WRITES to the robot, so it only runs when
        the user asks (service / Insights button), per the safety rule — the
        learner never applies one on its own. With no key, applies every pending
        suggestion. Returns a small summary for the GUI."""
        async with self._eval_lock:
            events = list(self.tracker.stuck_events or [])
            try:
                walls, zones, no_mops = self._current_zones()
            except ZoneReadError as exc:
                # Never write a partial zone list — the service replaces them all.
                _LOGGER.warning("apply_learned_nogo: aborting, zone read failed: %s", exc)
                return {"applied": 0, "rooms": [], "error": "could not read existing zones"}
            report = trap_learner.analyze(events, zones, room_boxes=self._room_boxes_by_name())
            promoted = self.tracker.learned_promoted
            todo = [s for s in report["nogo_suggestions"]
                    if s["key"] not in promoted and (key is None or s["key"] == key)]
            if not todo:
                return {"applied": 0, "rooms": []}
            try:
                await self._svc("dreame_vacuum", "vacuum_backup_map",
                                {"entity_id": self._vacuum_entity})
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("apply_learned_nogo: map backup failed: %s", exc)
            boxes = [s["box"] for s in todo]
            await self._write_zones(walls, zones + boxes, no_mops)
            now_iso = dt_util.now().isoformat()
            for s in todo:
                await self.tracker.async_mark_promoted(s["key"], now_iso)
            names = ", ".join(sorted({s["room"] for s in todo}))
            await self._notify(
                "✅ Learned no-go added",
                f"Walled off {len(todo)} recurring trap(s): {names}. The robot will "
                "now route around them.",
            )
            _LOGGER.info("apply_learned_nogo: added %d no-go(s): %s", len(todo), names)
            return {"applied": len(todo), "rooms": [s["room"] for s in todo]}

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
        """Next datetime the schedule fires: soonest upcoming day (today included
        if its time hasn't passed) with an enabled room due — at the global daily
        time for rooms on the daily schedule, or at a room's own explicit slot
        time for rooms with per-room ``times``. Shared by the report + the status
        snapshot so the GUI and the custom card agree."""
        rooms_cfg = self._rooms()
        try:
            hh, mm = (int(x) for x in str(self._opt(OPT_DAILY_TIME, DEFAULT_DAILY_TIME)).split(":", 1))
        except (ValueError, AttributeError):
            hh, mm = 9, 0
        daily_min = hh * 60 + mm
        now_min = now.hour * 60 + now.minute
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            wd = day.weekday()
            cutoff = now_min if offset == 0 else None   # today: only future times
            candidates: list[int] = []
            # Global daily fire — only if some enabled room WITHOUT its own times
            # is scheduled that weekday (slotted rooms run on their slots instead).
            has_daily = any(
                bool(c.get(ROOM_ENABLED)) and wd in (c.get(ROOM_DAYS) or [])
                and not has_explicit_times(c)
                for c in rooms_cfg.values() if isinstance(c, dict)
            )
            if has_daily and (cutoff is None or daily_min > cutoff):
                candidates.append(daily_min)
            # Per-room explicit slots scheduled that weekday.
            slot_min = next_slot_minute(rooms_cfg, wd, after_min=cutoff)
            if slot_min is not None:
                candidates.append(slot_min)
            if not candidates:
                continue
            run_min = min(candidates)
            return day.replace(hour=run_min // 60, minute=run_min % 60, second=0, microsecond=0)
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
            "consumables": self._consumables_snapshot(),
        }

    def _consumables_snapshot(self) -> list[dict]:
        """Wear-part life for the card / Report tab: name, %, and whether it's at
        or below the alert threshold — sorted lowest-first so the neediest shows."""
        threshold = int(self._opt(OPT_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD))
        readings = self._consumable_readings()
        out = []
        for c in CONSUMABLES:
            pct = readings.get(c["key"])
            if pct is None:
                continue
            out.append({"key": c["key"], "name": c["name"], "emoji": c["emoji"],
                        "percent": pct, "low": pct <= threshold,
                        "reset_entity": entity_of("button", self._prefix, c["reset"])})
        out.sort(key=lambda x: x["percent"])
        return out

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
            "holiday": self._holiday_hold(now),   # extended-away pause is active
            "manual_clean": dict(self.tracker.manual_clean),   # rooms to clean by hand
            "next_run": nxt.isoformat() if nxt else None,
            "next_run_day": WEEKDAYS[nxt.weekday()] if nxt else None,
            "next_run_time": nxt.strftime("%H:%M") if nxt else None,
        }

    # ------------------------------------------- actionable notification buttons
    def _action_id(self, name: str) -> str:
        """Notification action ids are matched GLOBALLY by the mobile app, so
        scope them to this entry — otherwise tapping 'Send home' on one robot's
        alert would drive every other robot in the house too."""
        return f"DREAME_{name}_{self.entry.entry_id}"

    def _rescue_actions(self) -> list[dict]:
        """Buttons for a needs-help alert, so the notification IS the rescue."""
        return [
            {"action": self._action_id("SEND_HOME"), "title": "Send home"},
            {"action": self._action_id("RESUME"), "title": "Resume clean"},
        ]

    @callback
    def _handle_notification_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if action == self._action_id("SEND_HOME"):
            self.hass.async_create_task(self._notification_send_home())
        elif action == self._action_id("RESUME"):
            self.hass.async_create_task(self._notification_resume())
            return
        for c in CONSUMABLES:
            if action == self._action_id(f"RESETCONS_{c['key']}"):
                self.hass.async_create_task(self._notification_reset_consumable(c["key"]))
                return
            if action == self._action_id(f"DISMISSCONS_{c['key']}"):
                # Nothing to do on the robot — the part's already flagged, so it
                # won't nag again until it's replaced (life climbs back up).
                _LOGGER.info("notification action: dismissed %s low alert", c["key"])
                return

    async def _notification_reset_consumable(self, key: str) -> None:
        """Reset a wear-part's life counter on the robot (the user replaced it),
        and clear our low flag so it can alert again next time it wears down."""
        c = CONSUMABLE_BY_KEY.get(key)
        if not c:
            return
        _LOGGER.info("notification action: reset consumable %s", key)
        await self._svc(
            "button", "press",
            {"entity_id": entity_of("button", self._prefix, c["reset"])},
        )
        flags = dict(self.tracker.consumable_alerted)
        if flags.pop(key, None) is not None:
            await self.tracker.async_set_consumable_alerted(flags)
        self._consumables_checked_at = None   # re-check promptly to confirm it reset

    async def _notification_send_home(self) -> None:
        _LOGGER.info("notification action: send home")
        await self._svc("vacuum", "return_to_base", {"entity_id": self._vacuum_entity})
        self._set_status("returning", "sending it home (tapped from the alert)")
        await self._tick()

    async def _notification_resume(self) -> None:
        _LOGGER.info("notification action: resume clean")
        # Resume the tracked run's own segments — never a bare vacuum.start.
        await self._resume_run(self.tracker.active_run)
        self._set_status("running", "resuming the clean (tapped from the alert)")
        await self._tick()

    def _set_status(self, state: str, reason: str) -> None:
        self._status = {"state": state, "reason": reason, "enabled": self.tracker.enabled}
        if self._notify_update:
            self._notify_update()

    def _obstacle_snapshot(self, pos: tuple[int, int] | None) -> tuple[str, str | None, int] | None:
        """The nearest thing the robot PHOTOGRAPHED to `pos`: (label, url, mm).

        The robot's AI already records what it sees — coordinates, a label AND a
        picture — and shows nobody. When it stops, that photo is the single most
        useful thing we can put in front of the user: not "it's blocked
        somewhere, go hunt", but a picture of the actual object and the room it's
        in. Live 2026-07-17 it had quietly photographed the two rope toys
        blocking the Entrance the entire time it was failing to get past them
        (and labelled one of them "Power Strip %46" — a frayed rope reads as a
        cable bundle to its classifier, which is exactly why it can SEE the thing
        and still have no idea to route around it)."""
        if pos is None:
            return None
        # index -> (label, url), keyed off the picture label's leading "N: "
        pics: dict[str, tuple[str, str]] = {}
        raw_pics = self._map_attr("obstacle_picture")
        if isinstance(raw_pics, dict):
            for label, url in raw_pics.items():
                idx = str(label).split(":", 1)[0].strip()
                if idx and url:
                    pics[idx] = (str(label), str(url))
        best = None
        raw = self._map_attr("obstacles")
        if isinstance(raw, dict):
            for key, val in raw.items():
                o = _plain_attr(val)          # Obstacle is an OBJECT in-process
                if not isinstance(o, dict):
                    continue
                try:
                    ox, oy = float(o["x"]), float(o["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                dist = int(math.hypot(ox - pos[0], oy - pos[1]))
                if dist > OBSTACLE_MATCH_MM:
                    continue
                if best is None or dist < best[2]:
                    label, url = pics.get(str(key), (None, None))
                    if not label:
                        label = str(o.get("type") or "something")
                        if o.get("room"):
                            label += f" ({o['room']})"
                    # only offer a photo the robot actually uploaded
                    if str(o.get("picture_status", "")).lower() not in ("uploaded", ""):
                        url = None
                    best = (label, url, dist)
        return best

    async def _notify(self, title: str, message: str, high_priority: bool = False,
                      actions: list[dict] | None = None, image: str | None = None) -> None:
        # high_priority asks the mobile app to bypass Android Doze / iOS batching
        # so a stuck/needs-help alert arrives now, not 30+ min later. Harmless on
        # persistent_notification, which just ignores the extra data.
        # `actions` puts real buttons on the push (Send home / Resume), so a
        # rescue alert can BE the rescue instead of sending you off to hunt for a
        # control in the app. Tapping one fires mobile_app_notification_action,
        # which _handle_notification_action turns into a command.
        data: dict = {}
        if high_priority:
            data.update({"ttl": 0, "priority": "high",
                         "push": {"interruption-level": "time-sensitive"}})
        if actions:
            data["actions"] = actions
        if image:
            # A picture of the actual thing in its way beats any wording we could
            # write. The mobile app resolves a relative /api/ path against HA.
            data["image"] = image
        extra = {"data": data} if data else {}
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


def _plain_attr(v):
    """Coerce a map-camera attribute to plain JSON types.

    The dreame map camera stores Point / Area / Line as OBJECTS in-process —
    they only turn into dicts when serialised out to REST / the frontend. So an
    isinstance(dict) check silently drops every one of them, which is why the
    engine read the robot's position as None on every stuck event, and read the
    user's zones back as an EMPTY list (a write then wipes them, since
    vacuum_set_restricted_zone replaces the whole list). Mirrors report._plain().
    """
    as_dict = getattr(v, "as_dict", None)
    if callable(as_dict):
        try:
            return as_dict()
        except Exception:  # noqa: BLE001
            return None
    return v


def _parse_area(value) -> float | None:
    """Best-effort parse of a cleaned-area value like '12 m2' or 12.5 -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else None
