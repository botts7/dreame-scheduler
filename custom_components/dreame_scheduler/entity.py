"""Base entity for Dreame Scheduler entities.

All platform entities share one HA device (per scheduled vacuum), the same
coordinator wiring, and a stable unique-id prefix derived from the config
entry.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SchedulerCoordinator


class SchedulerEntity(CoordinatorEntity[SchedulerCoordinator]):
    """Common base for every Dreame Scheduler entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SchedulerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def engine(self):
        return self.coordinator.engine

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name=self.coordinator.entry.title,
            manufacturer="Dreame Scheduler",
            model="Presence-aware room scheduler",
            configuration_url="https://github.com/botts7/dreame-scheduler",
        )
