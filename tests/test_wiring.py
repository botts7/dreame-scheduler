"""Static wiring checks: things the unit tests can't see because they never load
engine.py or week_tracker.py (both need Home Assistant).

Live 2026-11-22: engine.py called tracker.async_set_missed_watermark(), but the
edit adding it to week_tracker.py had silently not applied. Everything compiled
and every unit test passed; it failed at runtime on the box. This test reads the
source with ast and checks every `self.tracker.<name>` the engine uses exists.

Run: python tests/test_wiring.py   (no Home Assistant install needed)
"""
import ast
import os
import re
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")
fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


def read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as fh:
        return fh.read()


# What WeekTracker defines (methods, properties, plain attributes set in __init__).
tree = ast.parse(read("week_tracker.py"))
tracker = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WeekTracker")
defined = {n.name for n in tracker.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for node in ast.walk(tracker):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" \
            and isinstance(node.ctx, ast.Store):
        defined.add(node.attr)

# What the engine (and anything else in the package) uses on the tracker.
used = {}
for fname in sorted(os.listdir(BASE)):
    if not fname.endswith(".py"):
        continue
    for m in re.finditer(r"\.tracker\.([A-Za-z_][A-Za-z0-9_]*)", read(fname)):
        used.setdefault(m.group(1), set()).add(fname)

for name in sorted(used):
    check(f"tracker.{name} exists (used in {', '.join(sorted(used[name]))})", name in defined)

# Every module the engine imports from the package must exist.
for m in re.finditer(r"^from \.(\w+) import|^from \. import (\w+)", read("engine.py"), re.M):
    mod = m.group(1) or m.group(2)
    check(f"engine imports .{mod}, which exists", os.path.exists(os.path.join(BASE, mod + ".py")))

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
