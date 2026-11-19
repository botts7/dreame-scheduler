"""Weekday profiles and the commands for a whole-home firmware task."""

from __future__ import annotations

import re

DEPARTURE = "whole_home_on_departure"
GUARDS = "whole_home_required_states"

# Order matters. The secondary CleanGenius selector is exposed after enabling it.
GENIUS = {
    "genius_routine_combined": ("routine_cleaning", "vacuum_and_mop"),
    "genius_routine_sequential": ("routine_cleaning", "mop_after_vacuum"),
    "genius_deep_combined": ("deep_cleaning", "vacuum_and_mop"),
    "genius_deep_sequential": ("deep_cleaning", "mop_after_vacuum"),
}
MODES = {
    "home_vacuum": "sweeping",
    "home_mop": "mopping",
    "home_combined": "sweeping_and_mopping",
    "home_sequential": "mopping_after_sweeping",
}
LABELS = {
    "rooms": "Scheduled rooms · Per-room settings",
    "deep": "Scheduled rooms · Vacuum before mopping",
    "home_vacuum": "Whole home · Vacuum only",
    "home_mop": "Whole home · Mop only",
    "home_combined": "Whole home · Vacuum and mop",
    "home_sequential": "Whole home · Vacuum then mop",
    "genius_routine_combined": "Whole home · CleanGenius Routine · Vacuum and mop",
    "genius_routine_sequential": "Whole home · CleanGenius Routine · Mop after vacuum",
    "genius_deep_combined": "Whole home · CleanGenius Deep · Vacuum and mop",
    "genius_deep_sequential": "Whole home · CleanGenius Deep · Mop after vacuum",
}


def whole_home(profile):
    return profile in GENIUS or profile in MODES


def required_states_valid(value):
    return isinstance(value, dict) and all(
        isinstance(k, str) and re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", k)
        and isinstance(v, str) and v.strip() and v.lower() not in ("unknown", "unavailable")
        for k, v in value.items()
    )


def settings(profile, has_entity):
    if profile in GENIUS:
        level, mode = GENIUS[profile]
        changes = []
        if has_entity("select.cleaning_route"):
            changes.append(("select.cleaning_route", "standard"))
        return changes + [("select.cleangenius", level), ("select.cleangenius_mode", mode)]
    changes = []
    if has_entity("select.cleangenius"):
        changes.append(("select.cleangenius", "off"))
    if has_entity("switch.customized_cleaning"):
        changes.append(("switch.customized_cleaning", "off"))
    return changes + [("select.cleaning_mode", MODES[profile])]


def scheduled_profile(passes, attempts):
    """Only a full-map record starting at dispatch can claim a native session."""
    for attempt in reversed(attempts):
        if any(p["scope"] == "Cleaning" and -2 <= p["start"] - attempt["start"] <= 90
               for p in passes):
            return attempt["profile"]
    return None
