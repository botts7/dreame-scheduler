"""Dreame Scheduler — presence-aware, per-room scheduling for any Dreame robot.

A meta-integration: it doesn't talk to hardware, it orchestrates the entities
the Tasshack ``dreame_vacuum`` integration already exposes. One config entry per
vacuum; the engine runs the schedule itself (see engine.py).
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from homeassistant.exceptions import ConfigEntryNotReady

from .config_bridge import async_register_config_services
from .const import CONF_VACUUM_ENTITY, DOMAIN
from .coordinator import SchedulerCoordinator
from .report import async_register_report_service

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one scheduler (one vacuum) from a config entry."""
    coordinator = SchedulerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()   # seed data (defaults)
    try:
        await coordinator.engine.async_start()             # load tracker + start timer + first tick
    except Exception:                                      # noqa: BLE001
        # Never leave the interval + state listeners registered if the first
        # tick raised — that would orphan a timer ticking a half-built engine.
        await coordinator.engine.async_stop()
        raise ConfigEntryNotReady("Dreame Scheduler failed to start")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_register_services(hass)
    async_register_config_services(hass)   # get_config/set_config for the add-on GUI
    async_register_report_service(hass)    # get_report for the GUI Report tab
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so the engine re-reads changed options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: SchedulerCoordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.engine.async_stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once. With no ``vacuum`` given they fan out
    to every scheduler entry (single-robot users can ignore targeting); pass a
    vacuum entity_id to drive just that robot in a multi-vacuum home."""
    if hass.services.has_service(DOMAIN, "run_scheduled_now"):
        return

    def _engines(call: ServiceCall):
        want = call.data.get("vacuum")
        engines = []
        for c in hass.data.get(DOMAIN, {}).values():
            if want and c.entry.data.get(CONF_VACUUM_ENTITY) != want:
                continue
            engines.append(c.engine)
        return engines

    async def _run_scheduled(call: ServiceCall) -> None:
        for eng in _engines(call):
            await eng.async_run_scheduled_now(quiet=bool(call.data.get("quiet", False)))

    async def _run_catchup(call: ServiceCall) -> None:
        for eng in _engines(call):
            await eng.async_run_catchup_now(quiet=bool(call.data.get("quiet", False)))

    async def _reset_week(call: ServiceCall) -> None:
        for eng in _engines(call):
            await eng.async_reset_week()

    async def _clean_rooms(call: ServiceCall) -> None:
        segs = call.data.get("segments") or []
        for eng in _engines(call):
            await eng.async_clean_rooms(segs, quiet=bool(call.data.get("quiet", False)))

    quiet_schema = vol.Schema({
        vol.Optional("quiet", default=False): cv.boolean,
        vol.Optional("vacuum"): cv.string,
    })
    hass.services.async_register(DOMAIN, "run_scheduled_now", _run_scheduled, schema=quiet_schema)
    hass.services.async_register(DOMAIN, "run_catchup_now", _run_catchup, schema=quiet_schema)
    hass.services.async_register(DOMAIN, "reset_week", _reset_week,
                                 schema=vol.Schema({vol.Optional("vacuum"): cv.string}))
    hass.services.async_register(DOMAIN, "clean_rooms", _clean_rooms, schema=vol.Schema({
        vol.Required("segments"): list, vol.Optional("quiet", default=False): cv.boolean,
        vol.Optional("vacuum"): cv.string,
    }))
