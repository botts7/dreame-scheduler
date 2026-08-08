"""Constants for the Dreame Scheduler integration.

Model-agnostic: works with any vacuum exposed by the Tasshack ``dreame_vacuum``
integration. One config entry targets one vacuum (picked in the config flow);
add the integration again for a second robot.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dreame_scheduler"

# ---- config entry: data (identity, set once at setup) ----
CONF_VACUUM_ENTITY: Final = "vacuum_entity"   # e.g. "vacuum.dreamebot_l20_ultra"
CONF_PREFIX: Final = "prefix"                 # derived object_id, e.g. "dreamebot_l20_ultra"

# ---- config entry: options (editable in the options flow) ----
# Presence gate
OPT_REQUIRE_AWAY: Final = "require_away"
OPT_PRESENCE_ENTITIES: Final = "presence_entities"   # list[str] person./device_tracker./group.
OPT_AWAY_GRACE_MIN: Final = "away_grace_min"

# Allowed time window
OPT_WINDOW_ENABLED: Final = "window_enabled"
OPT_WINDOW_START: Final = "window_start"             # "HH:MM"
OPT_WINDOW_END: Final = "window_end"                 # "HH:MM"
OPT_WINDOW_OVERRUN: Final = "window_overrun"

# Daily dispatch
OPT_DAILY_TIME: Final = "daily_time"                 # "HH:MM" when today's rooms go out

# Guards
OPT_MIN_BATTERY: Final = "min_battery"
OPT_GUARD_DUSTBIN: Final = "guard_dustbin"
OPT_GUARD_WATER: Final = "guard_water"

# Weekly whole-house catch-up
OPT_CATCHUP_ENABLED: Final = "catchup_enabled"
OPT_CATCHUP_DAY: Final = "catchup_day"               # 0=Mon .. 6=Sun
OPT_CATCHUP_TIME: Final = "catchup_time"             # "HH:MM"
OPT_WEEK_START_DAY: Final = "week_start_day"         # 0=Mon .. 6=Sun

# Interrupt on arrival / resume when empty again
OPT_RETURN_ON_ARRIVAL: Final = "return_on_arrival"   # someone comes home -> dock
OPT_RESUME_WHEN_AWAY: Final = "resume_when_away"      # everyone leaves -> finish remaining
OPT_MAP_RESUME: Final = "map_resume"                 # (beta) resume un-cleaned area via vacuum.start

# Vacuum-before-mop (avoid smearing dust into mud)
OPT_VACUUM_BEFORE_MOP: Final = "vacuum_before_mop"    # sweep whole area, then mop (global sequential mode)
# Honor the robot's OWN native per-room settings (mode/mop/suction set in the
# Dreame app) instead of the scheduler imposing its own. On by default: the
# scheduler should say WHICH rooms + WHEN, and let the robot clean each the way
# you configured it natively (so a room you set to sweep-only stays sweep-only).
OPT_HONOR_NATIVE: Final = "honor_native"

# Auto-recovery: if the robot wedges (forward_suffocate etc.), wall off the spot
# with a temporary no-go, free it, and carry on the clean instead of docking.
OPT_AUTO_RECOVER: Final = "auto_recover"

# Silence the robot's own voice prompts WHILE the engine is driving a controlled
# maneuver (reverse-out / re-route), then restore. Stops it announcing "unable to
# reach specified area" etc. on repeat during our interventions; normal cleaning
# keeps its voice. Passive faults (a beaching) stay audible.
OPT_QUIET_RECOVERY: Final = "quiet_recovery"

# Stale-house nudge + quiet mode
OPT_STALE_NUDGE_ENABLED: Final = "stale_nudge_enabled"
OPT_STALE_AFTER_DAYS: Final = "stale_after_days"      # days since last clean before nudging
OPT_QUIET_SUCTION: Final = "quiet_suction"            # suction option used for quiet runs

# Pre-run "tidy the room" heads-up: notify BEFORE the robot goes out so loose
# pet toys / cables can be cleared (the only reliable defence against mobile
# obstacles it can't map — see trap_learner). Trigger fits the user's routine:
#   OPT_PRERUN_MODE = "morning"        -> at OPT_PRERUN_TIME, today's rooms
#                     "evening_before" -> at OPT_PRERUN_TIME, tomorrow's rooms
#                     "lead"           -> OPT_PRERUN_LEAD_MIN before the daily time
OPT_PRERUN_ENABLED: Final = "prerun_enabled"
OPT_PRERUN_MODE: Final = "prerun_mode"
OPT_PRERUN_TIME: Final = "prerun_time"               # "HH:MM" (morning / evening_before)
OPT_PRERUN_LEAD_MIN: Final = "prerun_lead_min"       # minutes before the daily time (lead)

# Notifications
OPT_NOTIFY_TARGETS: Final = "notify_targets"         # list[str] notify service names (no "notify." prefix)
OPT_NOTIFY_STUCK: Final = "notify_stuck"
OPT_NOTIFY_SKIPPED: Final = "notify_skipped"
OPT_NOTIFY_WEEKLY: Final = "notify_weekly"

# Defaults applied to rooms that don't override
OPT_DEFAULT_MODE: Final = "default_mode"             # cleaning-mode select option string
OPT_DEFAULT_SUCTION: Final = "default_suction"       # suction-level select option string

# Per-room schedule: dict keyed by segment-id string ->
#   {"enabled": bool, "days": [int...], "mode": str, "suction": str,
#    "wetness": int|None, "repeats": int, "door_sensor": str}
OPT_ROOMS: Final = "rooms"

# Per-room custom polygon shapes drawn in the Floor Plan editor (display-only,
# does not change the robot's own map): {seg_id: [[x, y], ...]} in map mm.
OPT_SHAPES: Final = "shapes"

# Map-image underlay transform for the Floor Plan editor (display-only):
# {"on": bool, "opacity": float, "x": mm, "y": mm, "scale": float, "rot": deg}.
OPT_MAP_IMAGE: Final = "map_image"

# HA entity overlays on the plan (bindings + placements) — populated by Phase 3.
OPT_OVERLAYS: Final = "overlays"

# Floor Plan Studio state (display-only; all written by the add-on GUI):
# per-edge wall overrides {seg:[[midX,midY,h],…]}, saved view rotation (deg),
# the Labs gate, and the uploaded reference-plan transform.
OPT_EDGEWALLS: Final = "edgewalls"
OPT_VIEWROT: Final = "viewrot"
OPT_STUDIO_ENABLED: Final = "studio_enabled"
OPT_USER_PLAN: Final = "user_plan"

# Manual-clean to-do: when the robot persistently CAN'T finish a scheduled room
# (unreachable — couch-blocked, always-shut door), surface it as a to-do task on
# an integration-owned to-do list so the user cleans it by hand and the house is
# actually clean. Auto-clears when the room next gets cleaned (robot or user).
# (Syncing to a user's OWN external to-do list is a later extension.)
OPT_MANUAL_CLEAN_ENABLED: Final = "manual_clean_enabled"
OPT_MANUAL_CLEAN_STALE_DAYS: Final = "manual_clean_stale_days"   # days since last clean
OPT_MANUAL_CLEAN_MIN_MISSES: Final = "manual_clean_min_misses"   # consecutive misses
OPT_MANUAL_CLEAN_NOTIFY: Final = "manual_clean_notify"           # also push a notification

# LAB (experimental): the robot physically shows you where it's stuck — drives to
# the edge of a room it can't reach, then signals "clean here for me" (a locate
# beep + fill-light + a notification). Opt-in; it sends the robot toward the
# blocked area, so it's Labs-gated.
OPT_SHOW_UNREACHABLE: Final = "show_unreachable"
OPT_DOOR_RETRY_ENABLED: Final = "door_retry_enabled"        # retry a door-skipped room once its door reopens
OPT_DOOR_RETRY_MIN: Final = "door_retry_min"                # minutes the door must be open before retrying
OPT_DOOR_RETRY_WHILE_HOME: Final = "door_retry_while_home"  # allow that retry even when someone's home

ROOM_ENABLED: Final = "enabled"
ROOM_DAYS: Final = "days"
ROOM_MODE: Final = "mode"
ROOM_SUCTION: Final = "suction"
ROOM_WETNESS: Final = "wetness"
ROOM_REPEATS: Final = "repeats"
ROOM_DOOR_SENSOR: Final = "door_sensor"
ROOM_MOP_EVERY: Final = "mop_every"     # mop every Nth sweep-day (1=every clean, 2=every 2nd, ...)

# ---- defaults ----
DEFAULT_REQUIRE_AWAY: Final = True
DEFAULT_AWAY_GRACE_MIN: Final = 10
DEFAULT_WINDOW_ENABLED: Final = True
DEFAULT_WINDOW_START: Final = "09:00"
DEFAULT_WINDOW_END: Final = "16:00"
DEFAULT_WINDOW_OVERRUN: Final = True
DEFAULT_DAILY_TIME: Final = "10:00"
DEFAULT_MIN_BATTERY: Final = 20
DEFAULT_GUARD_DUSTBIN: Final = True
DEFAULT_GUARD_WATER: Final = True
DEFAULT_CATCHUP_ENABLED: Final = True
DEFAULT_CATCHUP_DAY: Final = 5          # Saturday
DEFAULT_CATCHUP_TIME: Final = "10:00"
DEFAULT_WEEK_START_DAY: Final = 0       # Monday
DEFAULT_NOTIFY_STUCK: Final = True
DEFAULT_NOTIFY_SKIPPED: Final = True
DEFAULT_NOTIFY_WEEKLY: Final = True
DEFAULT_VACUUM_BEFORE_MOP: Final = False
DEFAULT_HONOR_NATIVE: Final = True      # respect the robot's own per-room settings by default
DEFAULT_ROOM_MOP_EVERY: Final = 1       # per-room mop cadence: 1 = mop on every clean (off)
DEFAULT_DOOR_RETRY_ENABLED: Final = False   # opt-in: don't retry door-skipped rooms early unless asked
DEFAULT_DOOR_RETRY_MIN: Final = 30          # door open this long => room likely clear, safe to retry
DEFAULT_DOOR_RETRY_WHILE_HOME: Final = False  # away-only by default; opt-in to trust the open-timer at home
DEFAULT_AUTO_RECOVER: Final = True
DEFAULT_QUIET_RECOVERY: Final = True
DEFAULT_MAP_RESUME: Final = False       # beta; default to safe whole-room resume
DEFAULT_RETURN_ON_ARRIVAL: Final = True
DEFAULT_RESUME_WHEN_AWAY: Final = True
DEFAULT_STALE_NUDGE_ENABLED: Final = True
DEFAULT_STALE_AFTER_DAYS: Final = 3
DEFAULT_MANUAL_CLEAN_ENABLED: Final = True
DEFAULT_MANUAL_CLEAN_STALE_DAYS: Final = 7
DEFAULT_MANUAL_CLEAN_MIN_MISSES: Final = 3
DEFAULT_MANUAL_CLEAN_NOTIFY: Final = False   # the task is enough; opt-in to also push
DEFAULT_SHOW_UNREACHABLE: Final = False      # experimental Labs feature, opt-in
DEFAULT_PRERUN_ENABLED: Final = False       # opt-in: no surprise notification for the community
DEFAULT_PRERUN_MODE: Final = "morning"      # "morning" | "evening_before" | "lead"
DEFAULT_PRERUN_TIME: Final = "07:30"
DEFAULT_PRERUN_LEAD_MIN: Final = 30
DEFAULT_REPEATS: Final = 1

# How often the engine re-evaluates (seconds). One minute is plenty for a
# schedule with minute-resolution trigger times and keeps load negligible.
TICK_SECONDS: Final = 60

# ---- derived entity-id templates (relative to CONF_PREFIX) ----
# The Tasshack integration names every entity <domain>.<prefix>_<suffix>, so we
# derive sibling entities from the chosen vacuum's object_id. Kept in one place
# so a future Tasshack rename is a one-file change.
def e(domain: str, prefix: str, suffix: str) -> str:
    """Build a dreame_vacuum sibling entity_id from the vacuum's prefix."""
    return f"{domain}.{prefix}_{suffix}"


# Suffixes we read/write on the target vacuum.
SUF_ERROR: Final = "error"
SUF_BATTERY: Final = "battery_level"
SUF_VOLUME: Final = "volume"                        # number: robot speaker volume 0-100
SUF_STATUS: Final = "status"
SUF_TASK_STATUS: Final = "task_status"
SUF_CLEANING_PROGRESS: Final = "cleaning_progress"
SUF_CURRENT_ROOM: Final = "current_room"
SUF_DUST_BAG: Final = "dust_bag_status"
SUF_CLEAN_WATER: Final = "clean_water_tank_status"
SUF_DIRTY_WATER: Final = "dirty_water_tank_status"
SUF_CLEANING_MODE: Final = "cleaning_mode"          # global select
SUF_SUCTION: Final = "suction_level"                # global select
SUF_CUSTOMIZED: Final = "customized_cleaning"       # switch
# Per-room selects/numbers: room_<seg>_<field>
SUF_ROOM_NAME: Final = "name"
SUF_ROOM_MODE: Final = "cleaning_mode"
SUF_ROOM_SUCTION: Final = "suction_level"
SUF_ROOM_WETNESS: Final = "wetness_level"


def room_entity(domain: str, prefix: str, seg: int | str, field: str) -> str:
    """e.g. room_entity('select', 'dreamebot_l20_ultra', 3, 'name')
    -> 'select.dreamebot_l20_ultra_room_3_name'."""
    return f"{domain}.{prefix}_room_{seg}_{field}"


# Dreame service used to start segment cleaning.
DREAME_DOMAIN: Final = "dreame_vacuum"
SERVICE_CLEAN_SEGMENT: Final = "vacuum_clean_segment"

# Max segment index the Tasshack integration exposes (room_1..room_15).
MAX_SEGMENTS: Final = 15

# TRUE sequential cleaning modes: sweep the WHOLE area first, THEN mop it (so the
# mop never drags over un-vacuumed dust) — the native "vacuum before mop". Tried
# in order; first one the model's global cleaning_mode select actually offers
# wins. Deliberately does NOT include 'sweeping_and_mopping' — that mops
# SIMULTANEOUSLY (smears), the opposite of what this is for. If a model offers no
# true-sequential mode, we simply don't force one (the run keeps the normal mode)
# rather than silently smear.
SEQ_MOP_MODES: Final = ("mopping_after_sweeping", "mop_after_sweep")

WEEKDAYS: Final = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]