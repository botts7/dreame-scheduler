"""Consumable / maintenance-part life tracking — HA-import-free logic.

The robot reports a remaining-life percentage for each wearing part (filter,
brushes, mop pad, dirt sensors, detergent, silver-ion module). This module holds
the pure decision of *which* parts have just dropped to/below the alert
threshold and should be flagged — separate from the engine glue that reads the
robot's attributes, sends the notification, and presses the reset button.

Run the tests with: python tests/test_logic.py
"""
from __future__ import annotations

# Each wearing part: the map attribute that carries its remaining-life %, the
# dreame_vacuum reset-button suffix (button.<prefix>_reset_<reset>), a friendly
# name, and an emoji for the alert. life_attr / reset are the robot's own names.
CONSUMABLES: list[dict] = [
    {"key": "filter",      "name": "Filter",            "life_attr": "filter_left",       "reset": "reset_filter",     "emoji": "🌀"},
    {"key": "main_brush",  "name": "Main brush",         "life_attr": "main_brush_left",   "reset": "reset_main_brush", "emoji": "🧹"},
    {"key": "side_brush",  "name": "Side brush",         "life_attr": "side_brush_left",   "reset": "reset_side_brush", "emoji": "🧽"},
    {"key": "mop_pad",     "name": "Mop pad",            "life_attr": "mop_pad_left",      "reset": "reset_mop_pad",    "emoji": "🧼"},
    {"key": "sensor",      "name": "Sensors",            "life_attr": "sensor_dirty_left", "reset": "reset_sensor",     "emoji": "📡"},
    {"key": "detergent",   "name": "Detergent",          "life_attr": "detergent_left",    "reset": "reset_detergent",  "emoji": "🧴"},
    {"key": "silver_ion",  "name": "Silver-ion module",  "life_attr": "silver_ion_left",   "reset": "reset_silver_ion", "emoji": "💧"},
]

CONSUMABLE_BY_KEY: dict[str, dict] = {c["key"]: c for c in CONSUMABLES}


def evaluate_consumables(readings: dict, threshold: int, alerted: dict):
    """Decide which consumables have just crossed to/below ``threshold`` percent.

    ``readings``  : {key: remaining-life % or None}. A ``None`` (sensor
                    unavailable) is ignored — never an alert, never a flag change.
    ``threshold`` : alert at or below this remaining-life %.
    ``alerted``   : {key: True} for parts already flagged (so we alert once, not
                    every check).

    Returns ``(due, new_alerted)``:
      * ``due``         — the consumable defs (from CONSUMABLES) that are newly at
                          or below threshold and not already flagged.
      * ``new_alerted`` — updated flags: set for a part we're alerting on, and
                          CLEARED once a part climbs back above threshold (i.e.
                          it was replaced / its counter reset), so it can alert
                          again next time it wears down.
    """
    new_alerted = dict(alerted)
    due: list[dict] = []
    for c in CONSUMABLES:
        pct = readings.get(c["key"])
        if pct is None:
            continue
        if pct > threshold:
            new_alerted.pop(c["key"], None)     # back above -> replaced/reset
        elif not alerted.get(c["key"]):
            due.append(c)
            new_alerted[c["key"]] = True
    return due, new_alerted
