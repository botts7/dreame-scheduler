"""Switch platform — the scheduler master enable."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([SchedulerEnabledSwitch(coordinator)])


class SchedulerEnabledSwitch(SchedulerEntity, SwitchEntity):
    """Master on/off. Off freezes all scheduling without losing config/state."""

    _attr_name = "Scheduler enabled"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: SchedulerCoordinator) -> None:
        super().__init__(coordinator, "enabled")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.tracker.enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.engine.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.engine.async_set_enabled(False)
