"""Lightweight coordinator for the Dreame Scheduler.

Unlike a polling integration, this one doesn't fetch from a device — the
SchedulerEngine already runs the behaviour on its own timer. The coordinator
exists only to give the entity platforms a single, CoordinatorEntity-friendly
data object (the engine's status snapshot) and a way to refresh on demand: the
engine calls ``push()`` whenever its status changes.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .engine import SchedulerEngine
from .week_tracker import WeekTracker

_LOGGER = logging.getLogger(__name__)


class SchedulerCoordinator(DataUpdateCoordinator[dict]):
    """Holds the engine + tracker; surfaces engine.status_snapshot() as data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} ({entry.title})",
            config_entry=entry, update_interval=None,
        )
        self.entry = entry
        self.tracker = WeekTracker(hass, entry.entry_id)
        self.engine = SchedulerEngine(hass, entry, self.tracker)
        self.engine.set_update_callback(self.push)

    async def _async_update_data(self) -> dict:
        return self.engine.status_snapshot()

    @callback
    def push(self) -> None:
        """Called by the engine when its status changes → refresh entities."""
        self.async_set_updated_data(self.engine.status_snapshot())
