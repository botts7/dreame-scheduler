"""Sensor platform — scheduler status + weekly whole-house progress."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
        StatusSensor(coordinator),
        RoomsCleanedSensor(coordinator),
    ])


class StatusSensor(SchedulerEntity, SensorEntity):
    """Human-readable scheduler state, with the full decision context attached."""

    _attr_name = "Status"
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, coordinator: SchedulerCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        return (self.coordinator.data or {}).get("state", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {
            "reason": d.get("reason"),
            "enabled": d.get("enabled"),
            "week_start": d.get("week_start"),
            "week_summary": d.get("week_summary"),
            "cleaned_rooms": d.get("cleaned_rooms"),
            "pending_rooms": d.get("pending_rooms"),
            "missed_rooms": d.get("missed_rooms"),
            "unreachable": d.get("unreachable"),
            "last_run": d.get("last_run"),
            "active": d.get("active"),
            "robot": d.get("robot"),
            "presence_home": d.get("presence_home"),
            "presence_configured": d.get("presence_configured"),
            "next_run": d.get("next_run"),
            "next_run_day": d.get("next_run_day"),
            "next_run_time": d.get("next_run_time"),
        }


class RoomsCleanedSensor(SchedulerEntity, SensorEntity):
    """Rooms confirmed cleaned so far in the current tracking week."""

    _attr_name = "Rooms cleaned this week"
    _attr_icon = "mdi:home-floor-g"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SchedulerCoordinator) -> None:
        super().__init__(coordinator, "rooms_cleaned")

    @property
    def native_value(self) -> int:
        return int((self.coordinator.data or {}).get("rooms_cleaned", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        total = int(d.get("rooms_total", 0))
        done = int(d.get("rooms_cleaned", 0))
        return {
            "rooms_total": total,
            "pending_rooms": d.get("pending_rooms"),
            "percent_complete": round(100 * done / total) if total else 0,
        }
