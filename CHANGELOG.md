# Changelog

All notable changes to the Dreame Scheduler integration and its companion
add-on are documented here. This project follows [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-08-10

Maintenance / consumable alerts.

### Integration (`custom_components/dreame_scheduler`)
- **Wear-part low alerts.** The scheduler now watches the robot's own remaining-
  life counters for the filter, main & side brushes, mop pad, dirt sensors,
  detergent and silver-ion module, and sends a notification the first time any of
  them drops to/below a threshold (default 10%). The alert carries two buttons:
  **Reset counter** (once you've replaced the part — presses the robot's own reset
  so it starts a fresh count) and **Dismiss**. It alerts once per part and won't
  nag again until the part is replaced (its life climbs back above the threshold).
  New options: *Alert when a wear-part runs low* (on by default) and the
  *threshold %*. Current wear-part life is also surfaced on the robot-status
  attributes for dashboards.
- **Real error codes in alerts.** Fault alerts (needs-a-hand, recovering,
  overreach) now include the robot's actual reported error (e.g.
  `right_wheel_speed`), not just a friendly summary — so a recurring hardware
  fault is visible instead of hidden behind "a firmware hiccup". The overreach
  alert also names the room(s) the robot strayed into.
- **Wheel-speed faults treated as hardware.** A `wheel_speed` / `wheell_speed`
  error (drive wheel not turning at the commanded speed — slipping/jammed/wound)
  now asks you to check the wheel instead of a futile reverse-out, matching how
  `wheel_motor` is already handled.

### Add-on
- New **"Alert when a wear-part runs low"** toggle and **threshold %** field
  (Notifications).

## [0.2.3] — 2026-08-09

Smarter handling when the robot gets tangled in a cable or cord.

### Integration (`custom_components/dreame_scheduler`)
- **Tangle-aware recovery.** A cable / cord / cloth tangle (the robot reports it
  as a `suffocate`) used to run the full reverse-out recovery — up to three
  reverses. But you can't reverse out of a wrap: backing up just **drags the
  tangle around**, and because dragging registers as "movement" the
  reverse-moved-nothing check never caught it. Now a tangle gets **one** gentle
  reverse (in case it's a loose cord it can back off), a short grace period, and
  then a clear **"🪢 Vacuum is tangled — please free it by hand"** alert instead
  of grinding. Fixes a run where the robot wrapped itself in bedroom cables and
  had to be carried back to the dock.
- Fixed a stale recovery notification that said it had "walled off the spot" —
  the scheduler no longer walls off during recovery, so it now says it's backing
  out and letting the robot re-plan.

## [0.2.2] — 2026-08-08

Fixes a return-to-dock loop during mop-pad washing.

### Integration (`custom_components/dreame_scheduler`)
- **No more return_to_base loop while the dock washes the mop pads.** When a run
  was paused because someone was home, the "keep it docked" enforcement treated
  *any* `cleaning` state as the robot having escaped — but a mop-pad wash/dry
  cycle also reports `cleaning` while the robot sits on the dock. The scheduler
  would then fire `return_to_base` on every wash-cycle blip, looping for the
  whole wash. It now distinguishes a genuine escape from station servicing
  (docked / washing / drying), so a wash cycle is left alone. The return-on-
  arrival step is likewise skipped when the robot is already home/servicing.

## [0.2.1] — 2026-08-04

Honors the robot's own per-room settings, and clearer, self-resolving stuck alerts.

### Integration (`custom_components/dreame_scheduler`)
- **Honor native per-room settings (new default).** The scheduler now respects
  the mode / mop / suction you set per room in the Dreame app — a room you set to
  sweep-only stays sweep-only. **"Vacuum before mop"** (which force-mopped the
  whole house and discarded per-room modes) is skipped while this is on. Fixes wet
  mop pads being dragged onto a carpet the robot was natively set to *sweep*,
  which stalled the wheel motor. Turn honor-native off to restore vacuum-before-mop.
- **Wheel-motor / hardware faults** are no longer treated as a reversible wedge —
  a `*_wheel_motor` error now asks you to check the wheels (something tangled / a
  jam) instead of a futile reverse-out that then mis-reads as "beached".
- **Honest stuck wording.** "Beached — lift it onto flat floor" is only used when
  the drop sensor actually fired; otherwise it's "stuck — needs a check" (no more
  false "wheels off the floor").
- **"All clear" follow-up.** After a "needs help" alert, once the robot sorts
  itself out and gets back to the dock you get a "✅ all clear — no action needed",
  so a self-recovered rescue doesn't leave you worrying.
- **"Can't get home" watchdog (detect & adapt).** Catches a robot that's moving
  but getting nowhere — circling, repositioning in place, or Blocked while trying
  to return. Instead of only asking "did it move?" (which tiny nudges keep
  re-arming), it tracks real progress — the robot's own task-progress %, cleaned
  m², or netting closer to the dock — and if none improve for 8 minutes it flags
  "🛟 can't get home, needs a hand". Using progress % (not just whole m²) means a
  slow-but-real clean is never mistaken for a stall. Works for manual/native runs
  too, and clears itself with the "✅ all clear" once it docks.

### Add-on
- New **"Honor native per-room settings"** toggle (General), on by default.

## [0.2.0] — 2026-07-27

A big feature + reliability release: the scheduler now heals itself when the
robot gets stuck, keeps an honest list of rooms it can't reach, and gives you
finer control over how and when each room is cleaned.

### Integration (`custom_components/dreame_scheduler`)

**Self-healing recovery.** When the robot gets into trouble mid-clean the
scheduler tries to fix it and carry on instead of leaving it stranded:
- Reverses out of a wedge and resumes, re-routing around the spot; route/path
  errors are now treated as recoverable.
- Detects a beaching / high-centre (drive wheels off the floor) — where no
  command can help — and asks for a hand instead of grinding through futile
  retries.
- Treats an obstruction as a moment, not a wall: it resumes and lets the robot
  re-plan rather than fencing off good floor.
- Puts the robot's own photo of the obstacle in the alert so you can see what
  stopped it.
- Silent-stuck and stranded watchdogs catch a robot that stopped moving with no
  error, or was left away from the dock after a run, and notify you.
- Wakes a deep-'sleeping' robot that would otherwise ignore a dispatch.

**Recurring-trap learner.** Learns where the robot repeatedly gets stuck across
separate runs, tells fixed hazards (worth walling off) apart from path blocks
(not), and suggests a permanent no-go zone for one-tap approval — it never walls
off floor on its own.

**Clean-by-hand list.** Rooms the robot genuinely can't reach for weeks become a
Home Assistant to-do list, and clear themselves when the room is next cleaned.

**Scheduling.**
- Rooms scheduled on several weekdays now clean on **each** of those days — fixes
  multi-day rooms that were silently collapsing to a single weekly clean.
- **Mop every N sweeps** (per room): sweep every scheduled day and mop-after-
  sweep every 2nd/3rd/… day, so damp-mopping needn't happen every time.
- **Door-retry** (opt-in): a room skipped for a shut door is retried the same day
  once its door has been open long enough to be sure the room is free — away-only,
  or while-home if you trust the open-timer.

**Reliability.** Recovery resumes only the run's own rooms (never a stray whole-
house clean); the robot is reined in if it wanders into rooms it wasn't sent to;
a run that swept implausibly little area isn't credited as done; a manual "clean
now" that merely parks isn't hijacked; the stale-house nudge is gated to daytime;
and mojibake in notification text is fixed.

**Labs (opt-in).** "Show me where I'm stuck" — the robot drives to an unreachable
room and signals there so you can find the blockage.

### Add-on (Dreame Scheduler panel)
- Clean-by-hand list and one-tap trap-learner **Apply** in the GUI.
- **Send test notification** button, and a warning for rooms scheduled on no days.
- Branding: wordmark banner as the store logo; Roboto bundled locally so the UI
  font loads correctly through ingress.

### Project
- CI: `actions/checkout` v4 → v7 (clears the Node 20 deprecation).
- Added GitHub issue templates (bug report + feature request).

### Thanks
- **[dreacon34](https://www.reddit.com/user/dreacon34)** (Reddit) — for the idea of
  driving the schedule off HA sensors to detect room availability, and
  re-attempting a room later instead of skipping it (shaped the door-sensor skip,
  the opt-in door-retry, and the adaptive-scheduling direction).
- **DatRandomBoi ("Anton")** (HA Community) — for the per-room "mop every N
  sweeps" cadence.

## [0.1.1] — 2026-07-10

Branding and packaging release — no behaviour changes.

- Bundled brand icon and logo in `custom_components/dreame_scheduler/brand/`
  so Home Assistant and HACS display the Dreame Scheduler artwork directly
  (HA Brands Proxy API, 2026.3.0+; local images take priority over the CDN).
- README: wordmark banner, a Highlights row (presence / reporting /
  self-healing), privacy-blurred screenshots, and dynamic version + license
  badges that track releases.
- CI: HACS validation now runs with no ignored checks (brands satisfied by the
  bundled `brand/` folder) — required for HACS default-store inclusion.

## [0.1.0] — 2026-07-10

First public release.

### Integration (`custom_components/dreame_scheduler`)
- Presence-aware, per-room cleaning scheduler for any Dreame robot exposed by
  the [Tasshack `dreame_vacuum`](https://github.com/Tasshack/dreame-vacuum)
  integration. One config entry per robot.
- Clean only when nobody's home (with a grace delay), within an allowed time
  window, gated by battery and station-condition guards.
- Per-room weekday schedules with per-room cleaning mode / suction / mop wetness
  and an optional door sensor that skips a room when its door is shut.
- Weekly whole-house guarantee: tracks what's been cleaned since the start of
  the week and finishes the rest on your catch-up day.
- Return-and-resume when someone comes home mid-clean; stale-house nudge with a
  one-tap quiet option.
- Completion detected from the robot's own metrics (not the unreliable "task
  completed" text); blocked rooms come from the map's door/obstacle data and
  are retried.
- Services: `run_scheduled_now`, `run_catchup_now`, `reset_week`, `clean_rooms`,
  plus `get_config` / `set_config` / `get_report` for the add-on GUI. All accept
  an optional `vacuum` target for multi-robot homes.

### Add-on (Dreame Scheduler panel)
- Ingress panel to configure everything above without editing YAML, plus a
  Report tab (weekly per-room status, coverage thumbnails, obstacles, run
  history) and ready-to-paste Lovelace cards.
- **Floor Plan Studio** (Labs, off by default): draw walls / no-go / no-mop
  zones and write them to the robot; rename / split / merge / move-boundary /
  carve rooms on the robot's own map as one staged bulk change; auto-fit and
  weld room shapes; a live 3D view with per-wall height control; place and
  control HA devices on the plan; export to a standalone SVG + Lovelace YAML;
  upload your own floor plan as a tracing baseline.
