"""Button platform — manual scheduler actions (also targeted by the nudge)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SchedulerCoordinator
from .entity import SchedulerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SchedulerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        _Button(coordinator, "clean_now", "Clean now", "mdi:broom",
                lambda e: e.async_run_catchup_now(quiet=False)),
        _Button(coordinator, "clean_now_quiet", "Clean now (quiet)", "mdi:volume-low",
                lambda e: e.async_run_catchup_now(quiet=True)),
        _Button(coordinator, "run_today", "Run today's schedule now", "mdi:calendar-today",
                lambda e: e.async_run_scheduled_now(quiet=False)),
        _Button(coordinator, "reset_week", "Reset week counters", "mdi:restart",
                lambda e: e.async_reset_week()),
    ])


class _Button(SchedulerEntity, ButtonEntity):
    def __init__(self, coordinator, key, name, icon, action) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self._action(self.engine)
