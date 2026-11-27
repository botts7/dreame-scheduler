"""Base faults: which ones stop only mopping, and alerting once outside a run.

Run: python tests/test_fault_watch.py   (no Home Assistant install needed)
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


fw = load("fault_watch")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


# ---- what kind of fault ----
check("kind: dry clean tank (2026-11-06/20) only blocks mopping", fw.kind("water_tank_dry") == "water")
check("kind: dirty tank full is a water fault", fw.kind("dirty_water_tank_full") == "water")
check("kind: dust bag full (2026-11-22) is a base fault", fw.kind("dust_bag_full") == "station")
check("kind: no error", fw.kind("no_error") is None)
check("kind: missing", fw.kind(None) is None)
check("kind: a wheel jam isn't a base fault", fw.kind("right_wheel_speed") is None)
check("kind: case tolerant", fw.kind("Water_Tank_Dry") == "water")
check("fix: dust bag", "dust bag" in fw.fix("dust_bag_full"))
check("fix: dry tank", "Refill the clean water tank" in fw.fix("water_tank_dry"))
check("fix: unknown gets generic", "Dreame app" in fw.fix("something_new"))

# ---- alert once outside a run ----
send, st = fw.idle_alert(None, "dust_bag_full", 1000)
check("idle: first sighting alerts", send and st["fault"] == "dust_bag_full")
send, st2 = fw.idle_alert(st, "dust_bag_full", 1060)
check("idle: still there, no repeat", not send and st2["fault"] == "dust_bag_full")
send, st3 = fw.idle_alert(st2, "no_error", 1100)
check("idle: blink off starts the clear timer", not send and st3["cleared_at"] == 1100)
send, st4 = fw.idle_alert(st3, "dust_bag_full", 1200)
check("idle: back within the clear window, no repeat", not send and st4["cleared_at"] is None)
send, st5 = fw.idle_alert(st4, "no_error", 2000)
send, st6 = fw.idle_alert(st5, "no_error", 2000 + fw.CLEAR_AFTER_SECONDS)
check("idle: gone long enough forgets it", not send and st6 is None)
send, _ = fw.idle_alert(st6, "dust_bag_full", 9999)
check("idle: same fault after a real clear alerts again", send)
send, st7 = fw.idle_alert(st2, "water_tank_dry", 1300)
check("idle: a different fault alerts straight away", send and st7["fault"] == "water_tank_dry")
send, st8 = fw.idle_alert(None, "no_error", 1000)
check("idle: nothing to do with no fault", not send and st8 is None)
send, _ = fw.idle_alert(None, "right_wheel_speed", 1000)
check("idle: non-base faults are left to the stuck alerts", not send)

# ---- vacuum-only day ----
check("hold: active for the rest of the fault day", fw.mop_hold_active("2026-11-22", "2026-11-22"))
check("hold: mopping tried again the next day", not fw.mop_hold_active("2026-11-22", "2026-11-23"))
check("hold: none set", not fw.mop_hold_active(None, "2026-11-22"))

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
