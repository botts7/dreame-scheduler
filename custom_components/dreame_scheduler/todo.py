"""To-do platform — an integration-owned "clean by hand" list.

The scheduler is honest about what the robot CAN do; this is honest about what
it can't. When a scheduled room stays unreachable-in-practice (couch-blocked, a
door that's always shut) the engine adds it here, so the user cleans it by hand
and the house is actually clean — rather than the room quietly rotting on the
pending list forever.

The integration provides its OWN to-do entity (every user gets it, no setup —
viewable in HA's built-in to-do panel / a to-do card). It auto-manages itself:
the engine adds a room when it crosses the unreachable threshold and removes it
the moment the room next gets cleaned — by the robot, OR by the user ticking the
task off (which credits the room as cleaned). Syncing to a user's OWN external
to-do list is a later extension.
"""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
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
    async_add_entities([ManualCleanTodo(coordinator)])


def _uid(seg: str) -> str:
    return f"seg_{seg}"


class ManualCleanTodo(SchedulerEntity, TodoListEntity):
    """The 'clean by hand' list — rooms the robot can't reach."""

    _attr_name = "Clean by hand"
    _attr_icon = "mdi:broom"
    # The only user action we honour is ticking an item done (= "I cleaned it").
    # No add/delete/move: the engine owns what's on the list.
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, coordinator: SchedulerCoordinator) -> None:
        super().__init__(coordinator, "manual_clean")

    @property
    def todo_items(self) -> list[TodoItem]:
        items: list[TodoItem] = []
        for seg, t in (self.engine.tracker.manual_clean or {}).items():
            name = t.get("name") or f"Room {seg}"
            reason = t.get("reason") or "the robot can't reach it"
            items.append(TodoItem(
                uid=_uid(seg),
                summary=f"Clean {name} by hand — {reason}",
                status=TodoItemStatus.NEEDS_ACTION,
            ))
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticking an item off means 'I cleaned this room by hand' — credit the
        room and drop it from the list (the engine handles both)."""
        if item.status == TodoItemStatus.COMPLETED and (item.uid or "").startswith("seg_"):
            await self.engine.async_manual_room_done(item.uid[len("seg_"):])
