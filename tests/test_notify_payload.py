"""Alert packaging: urgent pushes must not be "deliver now or drop" (ttl 0), and
different alerts must not overwrite each other in HA's notification list.

Run: python tests/test_notify_payload.py   (no Home Assistant install needed)
"""
import importlib.util
import os
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


npl = load("notify_payload")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


d = npl.push_data(True)
check("urgent: never ttl 0 (2026-11-26 blocked alert dropped)", d.get("ttl") != 0)
check("urgent: retried for hours", d.get("ttl") == npl.URGENT_TTL_SECONDS and d["ttl"] >= 3600)
check("urgent: still high priority (wakes the phone)", d.get("priority") == "high")
check("urgent: iOS time-sensitive", d.get("push", {}).get("interruption-level") == "time-sensitive")
check("normal: no extra data", npl.push_data(False) == {})
d2 = npl.push_data(True, actions=[{"action": "X"}], image="/api/x.jpg")
check("actions and image kept", d2["actions"] == [{"action": "X"}] and d2["image"] == "/api/x.jpg")

e = "abc"
blocked = npl.notification_id(e, "⚠️ Vacuum blocked — here's what it saw")
aside = npl.notification_id(e, "Vacuum stepping aside — someone's home")
check("ids: different alerts don't overwrite each other", blocked != aside)
check("ids: blocked alert id is readable", blocked == "dreame_scheduler_abc_vacuum_blocked")
check("ids: different faults don't overwrite each other",
      npl.notification_id(e, "Vacuum: Dust bag full") != npl.notification_id(e, "Vacuum: Water tank dry"))
check("ids: the same fault again replaces itself",
      npl.notification_id(e, "Vacuum: Dust bag full") == "dreame_scheduler_abc_vacuum_dust_bag_full")
check("ids: scoped to the entry", npl.notification_id("x", "A") != npl.notification_id("y", "A"))
check("ids: empty title still valid", npl.notification_id(e, "") == "dreame_scheduler_abc_alert")

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
