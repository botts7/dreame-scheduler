"""How an alert is packaged for the phone and for HA's notification list. Pure.

Live 2026-11-26 a "Vacuum blocked" alert never reached the phone. Two flaws:

* Urgent alerts were sent with ``ttl: 0``. To Firebase that means "deliver now
  or discard": a phone asleep or briefly offline at that moment never gets it.
  ``priority: high`` alone already wakes the phone out of Doze, so ttl stays
  unset and the push is retried until the phone is reachable.
* Every alert reused one persistent-notification id, so each new alert replaced
  the last in HA's notification list (the 18:45 "stepping aside" wiped out
  anything earlier). Each kind of alert now gets its own id: a repeat of the
  same alert still replaces itself, but different alerts no longer overwrite
  each other.
"""

from __future__ import annotations

import re

# How long Firebase keeps trying to deliver an urgent push to an unreachable
# phone. A stuck-robot alert is still worth getting a few hours late.
URGENT_TTL_SECONDS = 4 * 3600


def push_data(high_priority: bool, actions: list | None = None, image: str | None = None) -> dict:
    """The ``data`` block for a mobile_app notify call."""
    data: dict = {}
    if high_priority:
        data.update({"ttl": URGENT_TTL_SECONDS, "priority": "high",
                     "push": {"interruption-level": "time-sensitive"}})
    if actions:
        data["actions"] = actions
    if image:
        data["image"] = image
    return data


def notification_id(entry_id: str, title: str) -> str:
    """A persistent-notification id per kind of alert: the title without its
    explanatory tail after a dash, symbols dropped. The fault name after ':'
    is kept, so "Dust bag full" and "Water tank dry" don't overwrite each other."""
    head = re.split(r"\s[—\-]\s|—", str(title or ""), maxsplit=1)[0]
    slug = re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_") or "alert"
    return f"dreame_scheduler_{entry_id}_{slug[:40]}"
