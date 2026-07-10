"""Config bridge services for the Dreame Scheduler add-on GUI.

Mirrors the wallbox_gateway get_config / set_config pattern: the companion
add-on (behind ingress) reads the current options to pre-fill its UI and writes
a partial options object back. The add-on backend calls these via the Core API
with its SUPERVISOR_TOKEN; the native options flow remains a fallback that
writes the same entry.options.

Registered once per HA start (guarded) from __init__.async_setup_entry.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

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
    DEFAULT_REQUIRE_AWAY,
    DEFAULT_RESUME_WHEN_AWAY,
    DEFAULT_RETURN_ON_ARRIVAL,
    DEFAULT_STALE_AFTER_DAYS,
    DEFAULT_STALE_NUDGE_ENABLED,
    DEFAULT_WEEK_START_DAY,
    DEFAULT_WINDOW_ENABLED,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_OVERRUN,
    DEFAULT_WINDOW_START,
    DOMAIN,
    MAX_SEGMENTS,
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
    OPT_VACUUM_BEFORE_MOP,
    OPT_AUTO_RECOVER,
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
    OPT_SHAPES,
    OPT_MAP_IMAGE,
    OPT_OVERLAYS,
    OPT_EDGEWALLS,
    OPT_VIEWROT,
    OPT_STUDIO_ENABLED,
    OPT_USER_PLAN,
    OPT_STALE_AFTER_DAYS,
    OPT_STALE_NUDGE_ENABLED,
    OPT_WEEK_START_DAY,
    OPT_WINDOW_ENABLED,
    OPT_WINDOW_END,
    OPT_WINDOW_OVERRUN,
    OPT_WINDOW_START,
    WEEKDAYS,
    room_entity,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = ("unknown", "unavailable", "", "none")

# Defaults surfaced to the GUI (so it can show + reset to them).
_DEFAULTS: dict = {
    OPT_REQUIRE_AWAY: DEFAULT_REQUIRE_AWAY,
    OPT_PRESENCE_ENTITIES: [],
    OPT_AWAY_GRACE_MIN: DEFAULT_AWAY_GRACE_MIN,
    OPT_WINDOW_ENABLED: DEFAULT_WINDOW_ENABLED,
    OPT_WINDOW_START: DEFAULT_WINDOW_START,
    OPT_WINDOW_END: DEFAULT_WINDOW_END,
    OPT_WINDOW_OVERRUN: DEFAULT_WINDOW_OVERRUN,
    OPT_DAILY_TIME: DEFAULT_DAILY_TIME,
    OPT_MIN_BATTERY: DEFAULT_MIN_BATTERY,
    OPT_GUARD_DUSTBIN: DEFAULT_GUARD_DUSTBIN,
    OPT_GUARD_WATER: DEFAULT_GUARD_WATER,
    OPT_RETURN_ON_ARRIVAL: DEFAULT_RETURN_ON_ARRIVAL,
    OPT_RESUME_WHEN_AWAY: DEFAULT_RESUME_WHEN_AWAY,
    OPT_MAP_RESUME: DEFAULT_MAP_RESUME,
    OPT_VACUUM_BEFORE_MOP: DEFAULT_VACUUM_BEFORE_MOP,
    OPT_AUTO_RECOVER: DEFAULT_AUTO_RECOVER,
    OPT_CATCHUP_ENABLED: DEFAULT_CATCHUP_ENABLED,
    OPT_CATCHUP_DAY: DEFAULT_CATCHUP_DAY,
    OPT_CATCHUP_TIME: DEFAULT_CATCHUP_TIME,
    OPT_WEEK_START_DAY: DEFAULT_WEEK_START_DAY,
    OPT_STALE_NUDGE_ENABLED: DEFAULT_STALE_NUDGE_ENABLED,
    OPT_STALE_AFTER_DAYS: DEFAULT_STALE_AFTER_DAYS,
    OPT_NOTIFY_TARGETS: ["persistent_notification"],
    OPT_NOTIFY_STUCK: DEFAULT_NOTIFY_STUCK,
    OPT_NOTIFY_SKIPPED: DEFAULT_NOTIFY_SKIPPED,
    OPT_NOTIFY_WEEKLY: DEFAULT_NOTIFY_WEEKLY,
    OPT_DEFAULT_MODE: "",
    OPT_DEFAULT_SUCTION: "",
    OPT_QUIET_SUCTION: "",
    OPT_ROOMS: {},
    OPT_SHAPES: {},
    OPT_MAP_IMAGE: {},
    OPT_OVERLAYS: {},
    OPT_EDGEWALLS: {},
    OPT_VIEWROT: 0,
    OPT_STUDIO_ENABLED: False,
    OPT_USER_PLAN: {},
}

# The only keys set_config may write into entry.options — a stray/hostile call
# can't inject arbitrary keys that then break the engine on reload.
_ALLOWED_KEYS = set(_DEFAULTS)

# Per-key value validation for set_config. A value that fails validation is
# dropped (logged), never written — so a malformed payload from the add-on (or
# any API caller) can't store a value that fails the engine's next setup tick
# and puts the entry into "Setup failed". Keys without an explicit validator
# (the free-form studio geometry: shapes/map_image/overlays/edgewalls/user_plan,
# and rooms) are accepted as-is; they're display-only and never coerced by the
# engine, and get_config round-trips them.
_HHMM = vol.All(str, vol.Match(r"^\d{1,2}:\d{2}$"))
_WEEKDAY = vol.All(vol.Coerce(int), vol.Range(min=0, max=6))
_str_list = [str]
_VALIDATORS: dict = {
    OPT_REQUIRE_AWAY: bool, OPT_WINDOW_ENABLED: bool, OPT_WINDOW_OVERRUN: bool,
    OPT_GUARD_DUSTBIN: bool, OPT_GUARD_WATER: bool, OPT_RETURN_ON_ARRIVAL: bool,
    OPT_RESUME_WHEN_AWAY: bool, OPT_MAP_RESUME: bool, OPT_VACUUM_BEFORE_MOP: bool,
    OPT_AUTO_RECOVER: bool, OPT_CATCHUP_ENABLED: bool, OPT_STALE_NUDGE_ENABLED: bool,
    OPT_NOTIFY_STUCK: bool, OPT_NOTIFY_SKIPPED: bool, OPT_NOTIFY_WEEKLY: bool,
    OPT_STUDIO_ENABLED: bool,
    OPT_WINDOW_START: _HHMM, OPT_WINDOW_END: _HHMM,
    OPT_DAILY_TIME: _HHMM, OPT_CATCHUP_TIME: _HHMM,
    OPT_CATCHUP_DAY: _WEEKDAY, OPT_WEEK_START_DAY: _WEEKDAY,
    OPT_VIEWROT: vol.All(vol.Coerce(int), vol.In((0, 90, 180, 270))),
    OPT_AWAY_GRACE_MIN: vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    OPT_MIN_BATTERY: vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    OPT_STALE_AFTER_DAYS: vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
    OPT_PRESENCE_ENTITIES: _str_list, OPT_NOTIFY_TARGETS: _str_list,
    OPT_DEFAULT_MODE: str, OPT_DEFAULT_SUCTION: str, OPT_QUIET_SUCTION: str,
    OPT_ROOMS: dict, OPT_SHAPES: dict, OPT_MAP_IMAGE: dict, OPT_OVERLAYS: dict,
    OPT_EDGEWALLS: dict, OPT_USER_PLAN: dict,
}


def async_register_config_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "get_config"):
        return

    def _find_entry(vacuum: str | None) -> ConfigEntry | None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if vacuum:
            for e in entries:
                if e.data.get(CONF_VACUUM_ENTITY) == vacuum:
                    return e
            return None
        return entries[0] if entries else None

    def _sval(entity_id: str) -> str | None:
        st = hass.states.get(entity_id)
        if st is None or str(st.state).lower() in _UNAVAILABLE:
            return None
        return st.state

    def _select_options(prefix: str, suffix: str) -> list[str]:
        st = hass.states.get(f"select.{prefix}_{suffix}")
        return list(st.attributes.get("options", [])) if st else []

    def _discover_rooms(prefix: str) -> dict[str, str]:
        rooms: dict[str, str] = {}
        for n in range(1, MAX_SEGMENTS + 1):
            name = _sval(room_entity("select", prefix, n, "name"))
            if name:
                rooms[str(n)] = name
        return rooms

    def _scheduler_entities(entry: ConfigEntry) -> dict[str, str]:
        """This scheduler's OWN entities, keyed by their stable unique-id suffix
        (status/rooms_cleaned/enabled/clean_now/... ), read from the registry so
        a generated dashboard uses the exact ids HA created — portable to any
        user's vacuum, not slug-guessed."""
        registry = er.async_get(hass)
        pfx = entry.entry_id + "_"
        out: dict[str, str] = {}
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            uid = ent.unique_id or ""
            key = uid[len(pfx):] if uid.startswith(pfx) else uid
            out[key] = ent.entity_id
        return out

    async def _async_get_config(call: ServiceCall) -> dict:
        entry = _find_entry(call.data.get("vacuum"))
        if entry is None:
            return {"found": False, "options": {}}
        prefix = entry.data[CONF_PREFIX]
        return {
            "found": True,
            "entry_id": entry.entry_id,
            "title": entry.title,
            "vacuum": entry.data.get(CONF_VACUUM_ENTITY),
            "prefix": prefix,
            "options": dict(entry.options),
            "defaults": _DEFAULTS,
            "rooms": _discover_rooms(prefix),
            "modes": _select_options(prefix, "cleaning_mode"),
            "suctions": _select_options(prefix, "suction_level"),
            "weekdays": WEEKDAYS,
            "scheduler_entities": _scheduler_entities(entry),
            "map_camera": f"camera.{prefix}_map",
        }

    async def _async_set_config(call: ServiceCall) -> None:
        entry = _find_entry(call.data.get("vacuum"))
        if entry is None:
            raise HomeAssistantError("No matching Dreame Scheduler entry")
        incoming = call.data.get("options") or {}
        clean: dict = {}
        for key, val in incoming.items():
            if key not in _ALLOWED_KEYS:
                _LOGGER.warning("set_config: ignoring unknown option key %r", key)
                continue
            validator = _VALIDATORS.get(key)
            if validator is not None:
                try:
                    val = vol.Schema(validator)(val)
                except vol.Invalid as exc:
                    _LOGGER.warning("set_config: dropping invalid value for %r: %s", key, exc)
                    continue
            clean[key] = val
        if not clean:
            return
        # entry.options is replaced wholesale with the merge; the update
        # listener (_async_options_updated) reloads the entry so the engine
        # re-reads the new config.
        hass.config_entries.async_update_entry(entry, options={**entry.options, **clean})

    hass.services.async_register(
        DOMAIN, "get_config", _async_get_config,
        schema=vol.Schema({vol.Optional("vacuum"): str}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "set_config", _async_set_config,
        schema=vol.Schema({
            vol.Required("options"): dict,
            vol.Optional("vacuum"): str,
        }),
    )
