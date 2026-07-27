"""Pure recurring-trap learner over the stuck-event log — no Home Assistant
imports, so it's fully unit-testable in isolation (like clean_window /
scheduler / history_analytics).

A stuck event (logged by the engine each time the robot wedges/beaches/blocks)::

    {
      "ts": "iso", "room": "Lounge Room", "x": 7300, "y": 7080,   # map mm
      "error": "forward_suffocate", "kind": "daily|catchup|manual",
      "attempt": 1, "run_id": "iso start of the run", "beached": false,
    }

The whole job is deciding WHICH stuck spots deserve a permanent no-go, and it
turns on two orthogonal questions — both learned from real 2026-07-17..20 runs:

1. Is it RECURRING? Cluster events by proximity and count DISTINCT RUNS. A spot
   that only trapped the robot once is a moment (a toy, a chair pushed out), not
   a property of the room — walling it fences off open floor forever.

2. Is the robot's POSITION actually the hazard? This is the subtle one. Errors
   split into two kinds, and only one is wallable:

   * PHYSICAL — forward_suffocate, drop/beached, wheel, bumper, tangle. The robot
     is ON the thing: a rug lip, a threshold, a low overhang. Its coordinates ARE
     the hazard, so a no-go there works.

   * PATH — route, blocked, obstacle-ahead, no_progress, stranded. The robot
     stopped CLEAR of the cause (the obstacle is ahead on its path, or it simply
     gave up). Its coordinates say NOTHING about where the cause is — walling
     them fences off empty carpet. Live proof: the couch recurred across 3 runs
     with `route` errors, but the robot always halted ~a robot-length away from
     it; a no-go on those stops would wall open floor. The fix for a recurring
     PATH block is a virtual wall / mapping the furniture, not a no-go.

So a spot only earns a no-go SUGGESTION (never auto-applied) when it BOTH recurs
across runs AND physically caught the robot on more than one of them. Everything
else routes to advice, not a wall: scattered one-off beachings → "tidy the floor"
(mobile objects a no-go can't fix); recurring PATH blocks → "unmapped obstacle
here" (a virtual wall / furniture job).
"""

from __future__ import annotations

# Events whose stuck points fall within this box half-width (mm) of a cluster's
# running centroid are "the same spot" (~40 cm — a robot's footprint).
CLUSTER_MM = 400
# A spot must have trapped the robot across at least this many DISTINCT runs to
# count as a property of the room rather than a moment.
MIN_DISTINCT_RUNS = 3
# ...and it must have PHYSICALLY caught the robot (not merely path-blocked) on at
# least this many of them, so we never wall a spot the robot only ever stopped
# *near*. Two separate physical catches is the evidence that it's a real trap.
MIN_PHYSICAL_RUNS = 2
# Padding around a cluster's spread for the proposed box. ~a robot radius, so the
# box covers the physical lip/edge the robot's CENTRE stops short of, not just
# the logged centre points.
BOX_PAD_MM = 250
# Clamp the box so a learned no-go never walls a whole room, and a tight cluster
# still gets a usable footprint.
MAX_BOX_HALF_MM = 900
MIN_BOX_HALF_MM = 250
# Loose-object (mobile) beaching events before we advise a floor tidy.
MOBILE_TRAP_MIN = 1

# Error substrings where the robot is physically ON the hazard (position == cause
# → wallable). `beached` is always physical (wheels off the floor).
_PHYSICAL_ERROR_WORDS = (
    "suffocate", "drop", "cliff", "lift", "tilt", "wheel", "bumper",
    "tangle", "edge", "high", "pick",
)
# Error substrings where the robot stopped CLEAR of the cause (position != cause
# → NOT wallable; the obstacle is ahead, or it simply gave up).
_PATH_ERROR_WORDS = (
    "route", "path", "block", "obstacle", "progress", "strand", "reach",
)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_key(ev: dict) -> str:
    """Identity of the run an event belongs to. Prefer run_id; fall back to the
    event date so pre-upgrade events (no run_id) de-dupe per day."""
    return str(ev.get("run_id") or str(ev.get("ts") or "")[:10] or id(ev))


def _is_physical(ev: dict) -> bool:
    """Robot is ON the hazard — position == cause, so a no-go there works."""
    if ev.get("beached"):
        return True
    err = str(ev.get("error") or "").lower()
    if any(w in err for w in _PATH_ERROR_WORDS):
        return False                      # path words win (e.g. "no_progress")
    return any(w in err for w in _PHYSICAL_ERROR_WORDS)


def _is_path(ev: dict) -> bool:
    """Robot stopped CLEAR of the cause — its position says nothing about where
    the obstacle is, so a no-go on it would wall open floor."""
    if ev.get("beached"):
        return False
    err = str(ev.get("error") or "").lower()
    return any(w in err for w in _PATH_ERROR_WORDS)


def _cluster(events: list) -> list:
    """Single-link cluster same-room events by proximity, running centroid."""
    clusters: list = []
    for ev in events:
        x, y = _num(ev.get("x")), _num(ev.get("y"))
        if x is None or y is None:
            continue
        for c in clusters:
            if abs(x - c["cx"]) <= CLUSTER_MM and abs(y - c["cy"]) <= CLUSTER_MM:
                c["events"].append(ev)
                n = len(c["events"])
                c["cx"] += (x - c["cx"]) / n
                c["cy"] += (y - c["cy"]) / n
                break
        else:
            clusters.append({"events": [ev], "cx": x, "cy": y})
    return clusters


def _box(events: list, cx: float, cy: float) -> list:
    """Padded, clamped [x0, y0, x1, y1] around the events' spread."""
    xs = [v for v in (_num(e.get("x")) for e in events) if v is not None]
    ys = [v for v in (_num(e.get("y")) for e in events) if v is not None]
    spread_x = (max(xs) - min(xs)) / 2 if xs else 0
    spread_y = (max(ys) - min(ys)) / 2 if ys else 0
    hx = max(MIN_BOX_HALF_MM, min(MAX_BOX_HALF_MM, spread_x + BOX_PAD_MM))
    hy = max(MIN_BOX_HALF_MM, min(MAX_BOX_HALF_MM, spread_y + BOX_PAD_MM))
    return [int(cx - hx), int(cy - hy), int(cx + hx), int(cy + hy)]


def _covered(cx: float, cy: float, zones) -> bool:
    for z in zones or []:
        if isinstance(z, (list, tuple)) and len(z) >= 4:
            x0, y0, x1, y1 = z[0], z[1], z[2], z[3]
            if min(x0, x1) <= cx <= max(x0, x1) and min(y0, y1) <= cy <= max(y0, y1):
                return True
    return False


def _key(room: str, cx: float, cy: float) -> str:
    return f"{room}@{int(round(cx / 100)) * 100},{int(round(cy / 100)) * 100}"


def _error_counts(events: list) -> dict:
    out: dict[str, int] = {}
    for e in events:
        k = "beached" if e.get("beached") else str(e.get("error") or "unknown")
        out[k] = out.get(k, 0) + 1
    return out


def analyze(stuck_events: list, existing_zones: list | None = None, *,
            min_runs: int = MIN_DISTINCT_RUNS,
            min_physical_runs: int = MIN_PHYSICAL_RUNS) -> dict:
    """Cluster the stuck-event log and split it into actionable buckets::

        {
          "nogo_suggestions": [ {key, room, x, y, runs, physical_runs, events,
                                 beached, covered, box, errors, points}, ... ],
          "path_blocks":      [ ...recurring PATH clusters: an unmapped obstacle,
                                 a virtual-wall / furniture job, NOT a no-go... ],
          "clusters":         [ ...every cluster, for the Insights map... ],
          "tidy_advice":      {"active": bool, "events": int, "message": str},
        }

    Model-agnostic: every input comes from the map camera any Tasshack Dreame
    exposes (coords in mm, error strings, existing zones). Nothing here is
    specific to one home or one robot.
    """
    existing_zones = existing_zones or []
    by_room: dict[str, list] = {}
    for ev in stuck_events or []:
        by_room.setdefault(str(ev.get("room") or "somewhere"), []).append(ev)

    nogo_suggestions: list = []
    path_blocks: list = []
    clusters_out: list = []
    mobile_events = 0

    for room, evs in by_room.items():
        for c in _cluster(evs):
            cev = c["events"]
            phys = [e for e in cev if _is_physical(e)]
            cx, cy = c["cx"], c["cy"]
            # Centre + box on the PHYSICAL points when we have them — those are
            # the real hazard; the path/no-progress points are where it drifted.
            anchor = phys or cev
            axs = [v for v in (_num(e.get("x")) for e in anchor) if v is not None]
            ays = [v for v in (_num(e.get("y")) for e in anchor) if v is not None]
            acx = sum(axs) / len(axs) if axs else cx
            acy = sum(ays) / len(ays) if ays else cy
            total_runs = len({_run_key(e) for e in cev})
            phys_runs = len({_run_key(e) for e in phys})
            info = {
                "key": _key(room, acx, acy),
                "room": room,
                "x": int(acx), "y": int(acy),
                "runs": total_runs,
                "physical_runs": phys_runs,
                "events": len(cev),
                "beached": any(bool(e.get("beached")) for e in cev),
                "covered": _covered(acx, acy, existing_zones),
                "box": _box(anchor, acx, acy),
                "errors": _error_counts(cev),
                "points": [{"x": int(_num(e.get("x"))), "y": int(_num(e.get("y"))),
                            "error": e.get("error"), "beached": bool(e.get("beached")),
                            "run": _run_key(e)}
                           for e in cev if _num(e.get("x")) is not None],
            }
            clusters_out.append(info)
            if info["covered"]:
                continue
            # A no-go is earned only when the spot RECURS across runs AND
            # PHYSICALLY caught the robot on more than one of them.
            if phys_runs >= min_physical_runs and total_runs >= min_runs:
                nogo_suggestions.append(info)
            elif phys_runs == 0 and total_runs >= min_runs:
                # Recurs, but the robot only ever stopped NEAR it (path blocks).
                # An unmapped obstacle — a virtual-wall / furniture job, never a
                # no-go on the (open-floor) stop points.
                path_blocks.append(info)
            elif info["beached"] and phys_runs < min_physical_runs:
                # One-off / scattered lift — a mobile object a no-go can't fix.
                mobile_events += len(cev)

    nogo_suggestions.sort(key=lambda s: (s["physical_runs"], s["runs"]), reverse=True)
    path_blocks.sort(key=lambda s: s["runs"], reverse=True)
    tidy_advice = {
        "active": mobile_events >= MOBILE_TRAP_MIN,
        "events": mobile_events,
        "message": (
            "The robot has been trapped by loose objects on the floor "
            f"{mobile_events} time(s). A quick tidy (pet toys, cables, socks) "
            "before a run prevents these — a no-go can't, since the object moves."
        ),
    }
    return {
        "nogo_suggestions": nogo_suggestions,
        "path_blocks": path_blocks,
        "clusters": clusters_out,
        "tidy_advice": tidy_advice,
    }
