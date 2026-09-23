"""Config + options flow for the Dreame Scheduler.

Setup: pick which Dreame vacuum to schedule. The rest is configured in the
options flow, split into General, Presence & notifications, and per-room steps.
Everything is discovered from the chosen vacuum at runtime (room names, the
vacuum's own cleaning-mode/suction options), so no model-specific hardcoding.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_PREFIX,
    CONF_VACUUM_ENTITY,
    DEFAULT_AWAY_GRACE_MIN,
    DEFAULT_PRESENCE_STALE_MIN,
    DEFAULT_DOOR_RETRY_ENABLED,
    DEFAULT_DOOR_RETRY_MIN,
    DEFAULT_DOOR_RETRY_WHILE_HOME,
    DEFAULT_CATCHUP_DAY,
    DEFAULT_CATCHUP_ENABLED,
    DEFAULT_CATCHUP_TIME,
    DEFAULT_OPPORTUNISTIC_CATCHUP,
    DEFAULT_HOLIDAY_ENABLED,
    DEFAULT_HOLIDAY_AFTER_DAYS,
    DEFAULT_EDGE_ENABLED,
    DEFAULT_EDGE_EVERY_DAYS,
    DEFAULT_EDGE_TIME,
    DEFAULT_EDGE_PASSES,
    DEFAULT_EDGE_LEARN,
    DEFAULT_DAILY_TIME,
    DEFAULT_GUARD_DUSTBIN,
    DEFAULT_GUARD_WATER,
    DEFAULT_MIN_BATTERY,
    DEFAULT_NOTIFY_SKIPPED,
    DEFAULT_NOTIFY_STUCK,
    DEFAULT_NOTIFY_WEEKLY,
    DEFAULT_CONSUMABLE_ALERT,
    DEFAULT_CONSUMABLE_THRESHOLD,
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
    OPT_DOOR_RETRY_ENABLED,
    OPT_DOOR_RETRY_MIN,
    OPT_DOOR_RETRY_WHILE_HOME,
    OPT_CATCHUP_DAY,
    OPT_CATCHUP_ENABLED,
    OPT_CATCHUP_TIME,
    OPT_OPPORTUNISTIC_CATCHUP,
    OPT_HOLIDAY_ENABLED,
    OPT_HOLIDAY_AFTER_DAYS,
    OPT_EDGE_ENABLED,
    OPT_EDGE_EVERY_DAYS,
    OPT_EDGE_TIME,
    OPT_EDGE_PASSES,
    OPT_EDGE_LEARN,
    OPT_DAILY_TIME,
    OPT_DEFAULT_MODE,
    OPT_DEFAULT_SUCTION,
    OPT_GUARD_DUSTBIN,
    OPT_GUARD_WATER,
    OPT_MIN_BATTERY,
    OPT_NOTIFY_SKIPPED,
    OPT_NOTIFY_STUCK,
    OPT_NOTIFY_TARGETS,
    OPT_NOTIFY_WEEKLY,
    OPT_CONSUMABLE_ALERT,
    OPT_CONSUMABLE_THRESHOLD,
    OPT_PRESENCE_ENTITIES,
    OPT_PRESENCE_STALE_MIN,
    OPT_QUIET_SUCTION,
    OPT_REQUIRE_AWAY,
    OPT_RESUME_WHEN_AWAY,
    OPT_RETURN_ON_ARRIVAL,
    OPT_ROOMS,
    OPT_STALE_AFTER_DAYS,
    OPT_STALE_NUDGE_ENABLED,
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
    ROOM_TIMES,
    ROOM_WETNESS,
    WEEKDAYS,
    room_entity,
)
from .scheduler import room_times as _norm_room_times

_WEEKDAY_OPTIONS = [
    selector.SelectOptionDict(value=str(i), label=WEEKDAYS[i]) for i in range(7)
]


def _prefix_from_entity(entity_id: str) -> str:
    """'vacuum.dreamebot_l20_ultra' -> 'dreamebot_l20_ultra'."""
    return entity_id.split(".", 1)[1] if "." in entity_id else entity_id


def _int_or(value, default: int) -> int:
    """int(value), preserving 0, falling back to default on None/blank/bad input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DreameSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup: choose the vacuum."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_VACUUM_ENTITY]
            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()
            prefix = _prefix_from_entity(entity_id)
            # The scheduler derives every sibling entity from the vacuum's
            # object_id prefix; if the vacuum was renamed, sensor.<prefix>_status
            # (and rooms) won't exist and the scheduler would silently do nothing.
            # Catch it here rather than after setup.
            if self.hass.states.get(f"sensor.{prefix}_status") is None:
                errors["base"] = "prefix_mismatch"
            else:
                state = self.hass.states.get(entity_id)
                name = ((state.name if state else None) or prefix.replace("_", " ").title()).strip()
                return self.async_create_entry(
                    title=f"{name} Scheduler",
                    data={CONF_VACUUM_ENTITY: entity_id, CONF_PREFIX: prefix},
                )

        schema = vol.Schema({
            vol.Required(CONF_VACUUM_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="vacuum", integration="dreame_vacuum")
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Menu-driven options: General, Presence & notifications, Rooms.

    HA 2025.12 provides ``self.config_entry`` automatically — we must not set it
    ourselves, so __init__ only initialises our own state.
    """

    def __init__(self) -> None:
        self._seg: str | None = None
        # Carried between the room-edit step and the looped extra-times sub-step.
        self._rooms_cfg: dict = {}
        self._room_entry: dict = {}
        self._room_times: list = []

    # -------- helpers --------
    @property
    def _prefix(self) -> str:
        return self.config_entry.data[CONF_PREFIX]

    def _opt(self, key, default):
        return self.config_entry.options.get(key, default)

    def _select_options(self, suffix: str) -> list[str]:
        """Live option list of one of the vacuum's own select entities."""
        st = self.hass.states.get(f"select.{self._prefix}_{suffix}")
        return list(st.attributes.get("options", [])) if st else []

    def _discover_rooms(self) -> dict[str, str]:
        """Map of segment-id -> room name for segments that exist on the map."""
        rooms: dict[str, str] = {}
        for n in range(1, MAX_SEGMENTS + 1):
            st = self.hass.states.get(room_entity("select", self._prefix, n, "name"))
            if st and str(st.state).lower() not in ("unknown", "unavailable", "", "none"):
                rooms[str(n)] = st.state
        return rooms

    def _save(self, updates: dict):
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **updates}
        )

    # -------- menu --------
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "presence", "rooms"],
        )

    # -------- general --------
    async def async_step_general(self, user_input=None):
        if user_input is not None:
            return self._save(user_input)

        suctions = self._select_options("suction_level")
        modes = self._select_options("cleaning_mode")
        mode_sel = selector.SelectSelector(selector.SelectSelectorConfig(
            options=modes, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)) \
            if modes else selector.TextSelector()
        suction_sel = selector.SelectSelector(selector.SelectSelectorConfig(
            options=suctions, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)) \
            if suctions else selector.TextSelector()

        schema = vol.Schema({
            vol.Optional(OPT_WINDOW_ENABLED, default=self._opt(OPT_WINDOW_ENABLED, DEFAULT_WINDOW_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_WINDOW_START, default=self._opt(OPT_WINDOW_START, DEFAULT_WINDOW_START)): selector.TimeSelector(),
            vol.Optional(OPT_WINDOW_END, default=self._opt(OPT_WINDOW_END, DEFAULT_WINDOW_END)): selector.TimeSelector(),
            vol.Optional(OPT_WINDOW_OVERRUN, default=self._opt(OPT_WINDOW_OVERRUN, DEFAULT_WINDOW_OVERRUN)): selector.BooleanSelector(),
            vol.Optional(OPT_DAILY_TIME, default=self._opt(OPT_DAILY_TIME, DEFAULT_DAILY_TIME)): selector.TimeSelector(),
            vol.Optional(OPT_MIN_BATTERY, default=self._opt(OPT_MIN_BATTERY, DEFAULT_MIN_BATTERY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)),
            vol.Optional(OPT_GUARD_DUSTBIN, default=self._opt(OPT_GUARD_DUSTBIN, DEFAULT_GUARD_DUSTBIN)): selector.BooleanSelector(),
            vol.Optional(OPT_GUARD_WATER, default=self._opt(OPT_GUARD_WATER, DEFAULT_GUARD_WATER)): selector.BooleanSelector(),
            vol.Optional(OPT_RETURN_ON_ARRIVAL, default=self._opt(OPT_RETURN_ON_ARRIVAL, DEFAULT_RETURN_ON_ARRIVAL)): selector.BooleanSelector(),
            vol.Optional(OPT_RESUME_WHEN_AWAY, default=self._opt(OPT_RESUME_WHEN_AWAY, DEFAULT_RESUME_WHEN_AWAY)): selector.BooleanSelector(),
            vol.Optional(OPT_CATCHUP_ENABLED, default=self._opt(OPT_CATCHUP_ENABLED, DEFAULT_CATCHUP_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_CATCHUP_DAY, default=str(self._opt(OPT_CATCHUP_DAY, DEFAULT_CATCHUP_DAY))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_WEEKDAY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(OPT_CATCHUP_TIME, default=self._opt(OPT_CATCHUP_TIME, DEFAULT_CATCHUP_TIME)): selector.TimeSelector(),
            vol.Optional(OPT_OPPORTUNISTIC_CATCHUP, default=self._opt(OPT_OPPORTUNISTIC_CATCHUP, DEFAULT_OPPORTUNISTIC_CATCHUP)): selector.BooleanSelector(),
            vol.Optional(OPT_HOLIDAY_ENABLED, default=self._opt(OPT_HOLIDAY_ENABLED, DEFAULT_HOLIDAY_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_HOLIDAY_AFTER_DAYS, default=self._opt(OPT_HOLIDAY_AFTER_DAYS, DEFAULT_HOLIDAY_AFTER_DAYS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_WEEK_START_DAY, default=str(self._opt(OPT_WEEK_START_DAY, DEFAULT_WEEK_START_DAY))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_WEEKDAY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(OPT_EDGE_ENABLED, default=self._opt(OPT_EDGE_ENABLED, DEFAULT_EDGE_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_EDGE_EVERY_DAYS, default=self._opt(OPT_EDGE_EVERY_DAYS, DEFAULT_EDGE_EVERY_DAYS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_EDGE_TIME, default=self._opt(OPT_EDGE_TIME, DEFAULT_EDGE_TIME)): selector.TimeSelector(),
            vol.Optional(OPT_EDGE_PASSES, default=self._opt(OPT_EDGE_PASSES, DEFAULT_EDGE_PASSES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=3, step=1, unit_of_measurement="passes", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_EDGE_LEARN, default=self._opt(OPT_EDGE_LEARN, DEFAULT_EDGE_LEARN)): selector.BooleanSelector(),
            vol.Optional(OPT_STALE_NUDGE_ENABLED, default=self._opt(OPT_STALE_NUDGE_ENABLED, DEFAULT_STALE_NUDGE_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_STALE_AFTER_DAYS, default=self._opt(OPT_STALE_AFTER_DAYS, DEFAULT_STALE_AFTER_DAYS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=30, step=1, unit_of_measurement="days", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_DEFAULT_MODE, default=self._opt(OPT_DEFAULT_MODE, modes[0] if modes else "")): mode_sel,
            vol.Optional(OPT_DEFAULT_SUCTION, default=self._opt(OPT_DEFAULT_SUCTION, suctions[0] if suctions else "")): suction_sel,
            vol.Optional(OPT_QUIET_SUCTION, default=self._opt(OPT_QUIET_SUCTION, suctions[0] if suctions else "")): suction_sel,
        })
        return self.async_show_form(step_id="general", data_schema=schema)

    # -------- presence & notifications --------
    async def async_step_presence(self, user_input=None):
        if user_input is not None:
            # NumberSelector returns floats; normalise to int.
            user_input[OPT_AWAY_GRACE_MIN] = int(user_input.get(OPT_AWAY_GRACE_MIN, DEFAULT_AWAY_GRACE_MIN))
            user_input[OPT_PRESENCE_STALE_MIN] = int(user_input.get(OPT_PRESENCE_STALE_MIN, DEFAULT_PRESENCE_STALE_MIN))
            user_input[OPT_DOOR_RETRY_MIN] = int(user_input.get(OPT_DOOR_RETRY_MIN, DEFAULT_DOOR_RETRY_MIN))
            user_input[OPT_CONSUMABLE_THRESHOLD] = int(user_input.get(OPT_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD))
            return self._save(user_input)

        try:  # 2025.x preferred API; fall back for older cores
            notify_services = sorted(self.hass.services.async_services_for_domain("notify"))
        except AttributeError:
            notify_services = sorted(self.hass.services.async_services().get("notify", {}).keys())
        notify_options = ["persistent_notification"] + list(notify_services)

        schema = vol.Schema({
            vol.Optional(OPT_REQUIRE_AWAY, default=self._opt(OPT_REQUIRE_AWAY, DEFAULT_REQUIRE_AWAY)): selector.BooleanSelector(),
            vol.Optional(OPT_PRESENCE_ENTITIES, default=self._opt(OPT_PRESENCE_ENTITIES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["person", "device_tracker", "group", "binary_sensor"], multiple=True)),
            vol.Optional(OPT_AWAY_GRACE_MIN, default=self._opt(OPT_AWAY_GRACE_MIN, DEFAULT_AWAY_GRACE_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_PRESENCE_STALE_MIN, default=self._opt(OPT_PRESENCE_STALE_MIN, DEFAULT_PRESENCE_STALE_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1440, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_DOOR_RETRY_ENABLED, default=self._opt(OPT_DOOR_RETRY_ENABLED, DEFAULT_DOOR_RETRY_ENABLED)): selector.BooleanSelector(),
            vol.Optional(OPT_DOOR_RETRY_MIN, default=self._opt(OPT_DOOR_RETRY_MIN, DEFAULT_DOOR_RETRY_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=180, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(OPT_DOOR_RETRY_WHILE_HOME, default=self._opt(OPT_DOOR_RETRY_WHILE_HOME, DEFAULT_DOOR_RETRY_WHILE_HOME)): selector.BooleanSelector(),
            vol.Optional(OPT_NOTIFY_TARGETS, default=self._opt(OPT_NOTIFY_TARGETS, ["persistent_notification"])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=notify_options, multiple=True, custom_value=True,
                                              mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(OPT_NOTIFY_STUCK, default=self._opt(OPT_NOTIFY_STUCK, DEFAULT_NOTIFY_STUCK)): selector.BooleanSelector(),
            vol.Optional(OPT_NOTIFY_SKIPPED, default=self._opt(OPT_NOTIFY_SKIPPED, DEFAULT_NOTIFY_SKIPPED)): selector.BooleanSelector(),
            vol.Optional(OPT_NOTIFY_WEEKLY, default=self._opt(OPT_NOTIFY_WEEKLY, DEFAULT_NOTIFY_WEEKLY)): selector.BooleanSelector(),
            vol.Optional(OPT_CONSUMABLE_ALERT, default=self._opt(OPT_CONSUMABLE_ALERT, DEFAULT_CONSUMABLE_ALERT)): selector.BooleanSelector(),
            vol.Optional(OPT_CONSUMABLE_THRESHOLD, default=self._opt(OPT_CONSUMABLE_THRESHOLD, DEFAULT_CONSUMABLE_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)),
        })
        return self.async_show_form(step_id="presence", data_schema=schema)

    # -------- rooms: pick one --------
    async def async_step_rooms(self, user_input=None):
        rooms = self._discover_rooms()
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            self._seg = user_input["room"]
            return await self.async_step_room_edit()

        options = [selector.SelectOptionDict(value=seg, label=f"{name} (#{seg})")
                   for seg, name in rooms.items()]
        schema = vol.Schema({
            vol.Required("room"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)),
        })
        return self.async_show_form(step_id="rooms", data_schema=schema)

    # -------- rooms: edit the chosen one --------
    async def async_step_room_edit(self, user_input=None):
        seg = self._seg
        rooms_cfg = dict(self._opt(OPT_ROOMS, {}) or {})
        cur = dict(rooms_cfg.get(seg, {}))

        if user_input is not None:
            entry = {
                ROOM_ENABLED: user_input[ROOM_ENABLED],
                ROOM_DAYS: [int(d) for d in user_input.get(ROOM_DAYS, [])],
                ROOM_MODE: user_input.get(ROOM_MODE, ""),
                ROOM_SUCTION: user_input.get(ROOM_SUCTION, ""),
                ROOM_REPEATS: int(user_input.get(ROOM_REPEATS, 1)),
                ROOM_DOOR_SENSOR: user_input.get(ROOM_DOOR_SENSOR, ""),
            }
            wet = user_input.get(ROOM_WETNESS)
            if wet not in (None, ""):
                entry[ROOM_WETNESS] = int(wet)
            # Mop cadence: 0 = never mop (all-rug room), 1 = mop every clean,
            # 2 = every 2nd day, ... (allow 0 through, don't coerce it to 1).
            try:
                entry[ROOM_MOP_EVERY] = max(0, int(user_input.get(ROOM_MOP_EVERY, 1)))
            except (TypeError, ValueError):
                entry[ROOM_MOP_EVERY] = 1
            # Extra clean times (multiple cleans per day) are edited in the next
            # step (a time picker you add one at a time). Carry the room's current
            # times in, and stash the half-built entry for that step to finish.
            self._rooms_cfg = rooms_cfg
            self._room_entry = entry
            self._room_times = list(cur.get(ROOM_TIMES) or [])
            return await self.async_step_room_times()

        modes = self._select_options("cleaning_mode")
        suctions = self._select_options("suction_level")
        mode_sel = selector.SelectSelector(selector.SelectSelectorConfig(
            options=[""] + modes, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN))
        suction_sel = selector.SelectSelector(selector.SelectSelectorConfig(
            options=[""] + suctions, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN))
        # Mop cadence as a labelled dropdown so "Never" (0) reads clearly for an
        # all-rug room, alongside "every clean" (1) and the every-Nth options.
        mop_every_sel = selector.SelectSelector(selector.SelectSelectorConfig(
            options=[
                {"value": "0", "label": "Never — sweep only (all-rug room)"},
                {"value": "1", "label": "Every clean"},
                {"value": "2", "label": "Every 2nd clean"},
                {"value": "3", "label": "Every 3rd clean"},
                {"value": "4", "label": "Every 4th clean"},
                {"value": "5", "label": "Every 5th clean"},
                {"value": "6", "label": "Every 6th clean"},
                {"value": "7", "label": "Every 7th clean"},
            ],
            mode=selector.SelectSelectorMode.DROPDOWN))

        fields: dict = {
            vol.Optional(ROOM_ENABLED, default=cur.get(ROOM_ENABLED, True)): selector.BooleanSelector(),
            vol.Optional(ROOM_DAYS, default=[str(d) for d in cur.get(ROOM_DAYS, [])]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_WEEKDAY_OPTIONS, multiple=True,
                                              mode=selector.SelectSelectorMode.LIST)),
            vol.Optional(ROOM_MODE, default=cur.get(ROOM_MODE, "")): mode_sel,
            vol.Optional(ROOM_SUCTION, default=cur.get(ROOM_SUCTION, "")): suction_sel,
            vol.Optional(ROOM_WETNESS, default=str(cur.get(ROOM_WETNESS, "") or "")): selector.TextSelector(),
            vol.Optional(ROOM_MOP_EVERY, default=str(_int_or(cur.get(ROOM_MOP_EVERY), 1))): mop_every_sel,
            vol.Optional(ROOM_REPEATS, default=cur.get(ROOM_REPEATS, 1)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=3, step=1, mode=selector.NumberSelectorMode.BOX)),
        }
        # EntitySelector rejects an empty-string default, so only prefill when set.
        door = cur.get(ROOM_DOOR_SENSOR)
        door_key = vol.Optional(ROOM_DOOR_SENSOR, default=door) if door else vol.Optional(ROOM_DOOR_SENSOR)
        fields[door_key] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"))

        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="room_edit", data_schema=schema,
            description_placeholders={"room": self._discover_rooms().get(seg, seg)},
        )

    # -------- rooms: edit the extra clean times (add one at a time) --------
    def _times_labels(self) -> list[dict]:
        """Current times as {value, label} for the remove picker — normalised
        (sorted, de-duped) so it matches what the scheduler will actually use."""
        return [
            {"value": at, "label": at + ("" if mop else " (vacuum only)")}
            for _m, at, mop in _norm_room_times({"times": self._room_times})
        ]

    async def async_step_room_times(self, user_input=None):
        """Looping sub-step: pick a time, optionally mark it vacuum-only, add it,
        and repeat until 'Save and finish'. Existing times can be removed. Empty =
        the room just uses the global daily time (today's behaviour)."""
        if user_input is not None:
            times = list(self._room_times)
            remove = set(user_input.get("remove", []) or [])
            if remove:
                times = [t for t in times if t.get("at") not in remove]
            add_t = user_input.get("add_time")
            if add_t:
                at = str(add_t)[:5]                       # TimeSelector gives HH:MM:SS
                mop = not bool(user_input.get("add_vac", False))
                times = [t for t in times if t.get("at") != at]
                times.append({"at": at, "mop": mop})
            # Normalise through the same helper the engine uses (sort/dedup/validate).
            self._room_times = [
                {"at": at, "mop": mop} for _m, at, mop in _norm_room_times({"times": times})
            ]
            if user_input.get("finish", True):
                entry = dict(self._room_entry)
                if self._room_times:
                    entry[ROOM_TIMES] = self._room_times
                else:
                    entry.pop(ROOM_TIMES, None)
                self._rooms_cfg[self._seg] = entry
                return self._save({OPT_ROOMS: self._rooms_cfg})
            return await self.async_step_room_times()      # loop to add another

        remove_opts = self._times_labels()
        fields: dict = {}
        if remove_opts:
            fields[vol.Optional("remove", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=remove_opts, multiple=True,
                                              mode=selector.SelectSelectorMode.LIST))
        fields[vol.Optional("add_time")] = selector.TimeSelector()
        fields[vol.Optional("add_vac", default=False)] = selector.BooleanSelector()
        fields[vol.Optional("finish", default=True)] = selector.BooleanSelector()
        current = ", ".join(o["label"] for o in remove_opts) or "none yet"
        return self.async_show_form(
            step_id="room_times", data_schema=vol.Schema(fields),
            description_placeholders={
                "room": self._discover_rooms().get(self._seg, self._seg),
                "current": current,
            },
        )