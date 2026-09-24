# Changelog

All notable changes to the Dreame Scheduler integration and its companion
add-on are documented here. This project follows [Semantic Versioning](https://semver.org/).

## [0.5.3] — 2026-09-24

### Integration (`custom_components/dreame_scheduler`)
- **Edge clean temporarily disabled, pending a proper rework.** The current
  implementation cleans (and mops) each whole room instead of doing a true
  wall-edge pass — it relies on time-cutting a normal segment clean, which doesn't
  isolate the perimeter on a robot that fills back-and-forth. Rather than ship a
  misleading feature, edge clean (scheduled and manual) is now a no-op. A rework
  that zone-cleans the wall strips (with a sweep-only option) will re-enable it.
- The add-on's Edge clean card is marked disabled.

## [0.5.2] — 2026-09-24

### Integration (`custom_components/dreame_scheduler`)
- **Edge clean: fix run stopping after one small room.** The edge pass stops each
  room by a perimeter time estimate, but a room small enough to finish before that
  estimate had the robot dock with its task "completed" — which was mis-read as the
  user stopping the run, cancelling it after that one room. It now recognises a
  completed task as a finished lap and advances to the next room, so the run walks
  the whole house. Big rooms are still cut by time as before.

## [0.5.1] — 2026-09-23

### Add-on (`addon/`)
- **Never-mop parity.** The add-on's room editor gained the "Mop cadence" dropdown
  ("Never — sweep only" … "Every 7th clean"), so an all-rug room can be set to never
  mop from the add-on too, matching the integration's config screen. Also added the
  holiday-pause and stale-tracker fields that landed in 0.5.0.

## [0.5.0] — 2026-09-23

Presence-smart scheduling: clean more than once a day, catch up on any free day,
pause on holidays, never mop your rugs — plus fixes for the two things that bit us.

### Integration (`custom_components/dreame_scheduler`)
- **Stale presence-tracker watchdog (fix).** A presence entity that hasn't
  reported for longer than a configurable time is now treated as stale and
  ignored, so a phone tracker wedged at "home" can't silently block cleaning
  forever. Opt-in ("Ignore a tracker that hasn't reported for…", 0 = off) on the
  Presence step. This is the gap behind a real missed clean.
- **Auditable run history (fix).** The run history now records the cleaning mode
  actually used per room (sweep vs mop / native), not just the configured base
  mode — so you can verify after the fact that, e.g., a carpet room swept.
- **Holiday / extended-away pause.** New opt-in: when everyone has been away for a
  set number of days AND the whole house is already clean, cleaning pauses instead of
  re-cleaning an empty house every day (status: "holiday — house already clean"). The
  first clean after you leave still runs, the weekly whole-house guarantee still gives
  a once-a-week freshen, and normal cleaning resumes as soon as someone's home. Off by
  default; "Days away before pausing" is configurable. Requested by botts.
- **Opportunistic catch-up.** New opt-in "Catch up on any free (empty) day"
  option. When a day's clean keeps getting blocked (someone home), the pending
  rooms are cleaned the next time the house is empty on any day, instead of
  waiting for the single weekly catch-up day. Runs at most once a day, from the
  catch-up time, and only when the house is actually empty (same presence gate);
  today's normal schedule still takes priority, and the fixed catch-up day stays
  as the weekly backstop. Off by default. Requested on the HA community thread.
- **Clean a room more than once a day.** A room can now have its own list of
  extra clean times, so (for example) the kitchen gets a quick midday pass on
  top of its normal clean. Each time can be a full clean or a vacuum-only pass
  ("13:00 vac"). A room with times set runs on those instead of the global daily
  time; leave it blank and nothing changes. Every extra pass still goes through
  the same presence, cleaning-window and station guards as a normal run, and the
  weekly "rooms cleaned this week" count isn't inflated by the extra passes.
  Requested on the HA community thread (tomofdarkness).
- Per-room "never mop" (`mop_every = 0`): an all-rug room always sweeps, even if
  its native mode is mop. Selectable as "Never — sweep only" in the room's mop
  cadence dropdown. (Same thread.)

### Add-on (`addon/`)
- Per-room settings gained an **Extra times** editor (add a time, tick "vacuum
  only" per time) and the General/Presence tabs gained the opportunistic catch-up,
  holiday-pause, and stale-tracker options — mirroring the integration.

## [0.4.1] — 2026-08-29

HACS review fixes (PR [hacs/default#9098]).

### Integration (`custom_components/dreame_scheduler`)
- **Fewer false-alarm alerts.** "Needs help" notifications are now held for a
  90-second recovery grace before sending. If the robot is cleaning again by
  then (a resume/re-plan sorted it out), the alert is dropped silently; a
  genuine stuck that never resumes still alerts, just ~90s later. A later, more
  accurate trip in the same window (e.g. recovery failed → beached) updates the
  held message without resetting the timer.
- **`manifest.json`** now declares `integration_type: service` (it owns no
  hardware — it schedules on top of `dreame_vacuum`), instead of defaulting to
  `hub`.
- **`async_unload_entry`** now removes the domain-wide `dreame_scheduler.*`
  services when the last config entry is unloaded, so they no longer linger in
  the service picker after uninstall.

### Card (`www/dreame-scheduler-card.js`)
- Escape all freeform device/app text (room names, error strings, chip states,
  next-run fields) before it goes into `innerHTML`, so a value containing markup
  renders as text, not HTML.

### Docs
- README **Status** block updated to reflect the shipped state (was still
  reading v0.1.0 "verification in progress").

## [0.4.0] — 2026-08-15

Edge cleaning — a dedicated pass along your walls.

### Integration (`custom_components/dreame_scheduler`)
- **Edge clean.** A new run type that sweeps thin strips along each room's walls,
  room by room in one automatic pass (zone-cleaning sequenced across the schedule
  tick). Run it **manually** with the `dreame_scheduler.edge_clean` service
  (optional `segments`, else every room), or on a **dedicated schedule** — every
  N days at a set time, presence-gated like a normal run. The run turns
  auto-reclean off for its duration (best-effort) so it does the edges, not a full
  fill, and restores it after. Options: enable / every-N-days / time / strip width.
- **Note:** Phase 1 uses each room's map box, so it also touches the "invisible"
  room-boundary edges (open doorways / where the map splits rooms), not only
  physical walls. Best results with the robot in Vacuum mode and, on robots whose
  cloud rejects the auto-reclean change, Auto-reclean set off once in the Dreame app.

### Add-on
- New **🧭 Edge clean** card — schedule settings (enable / every N days / time /
  strip width) and an **"Edge clean now"** button.

## [0.3.4] — 2026-08-15

Stops false "can't get home" alerts during normal long cleans.

### Integration (`custom_components/dreame_scheduler`)
- **"Can't get home" watchdog now only watches the return trip.** It was firing
  mid-clean during long runs — an auto-reclean pass re-covers already-counted
  floor, so cleaned-m² and task-% sit flat even though the robot is cleaning fine,
  and the watchdog mistook that for "stuck, can't get home" (live 2026-08-14: 3
  false alerts during one mop clean). It now arms only once the robot is heading
  for the dock (sticky through a mid-return reposition, cleared when it docks), so
  a normal clean never trips it — while the real case (sent home, can't get back)
  still fires. Genuine mid-clean stalls are still caught by the silent-stuck
  (no-movement) watchdog and real error handling.

## [0.3.3] — 2026-08-13

Maintenance panel in the add-on with one-tap counter resets.

### Add-on
- New **Maintenance** card on the Report tab: shows the remaining life of every
  wear part (filter, brushes, mop pad, sensors, detergent, silver-ion) as a bar,
  with a **Reset** button on each — tap it after you've replaced a part and it
  presses the robot's own reset so the counter starts fresh. No more hunting for
  the notification button or the native entity.

### Integration (`custom_components/dreame_scheduler`)
- The report/robot data now includes each consumable's remaining-life % and its
  reset-button entity, so the add-on (and dashboards) can render the panel above.

## [0.3.2] — 2026-08-13

Stops a station fault from re-blocking cleaning the next day.

### Integration (`custom_components/dreame_scheduler`)
- **A station fault no longer comes back to haunt you.** When a dock fault ends a
  run ("mop install failed"), the scheduler now **stops the robot's task** so its
  firmware can't auto-resume the doomed mop job (Dreame firmware re-runs a paused
  task on its own — which then fails again and "blocks" cleaning the next day),
  and it **no longer queues an auto-retry**. The un-done rooms simply stay pending
  for the weekly catch-up, which picks them up once the station's fixed — instead
  of hammering a broken mop every time the house empties. Fixes a case where a
  jammed mop-pad mount left the same three rooms failing across days.

## [0.3.1] — 2026-08-12

Handles a dock/station fault (can't mount the mop pads) cleanly.

### Integration (`custom_components/dreame_scheduler`)
- **Station / mop-install alert.** A dock setup fault — most commonly
  **"mop install failed"** (the robot can't mount its mop pads) — now gets its
  own clear alert ("🧩 can't set up at the dock — check the mop pads are seated
  and the tray's clear"), with the real error code, instead of a generic "stuck".
  The robot can't fix this itself, so the run is **ended** (un-done rooms defer to
  the catch-up) rather than left retrying.
- **No more wedged runs.** A run that errored but whose robot has since returned
  to the dock (no active error, parked, not mid-recovery) is now finalised as
  interrupted instead of sitting open — previously an errored *suspended* run
  could block the next day's dispatch for up to 24 h. Healthy suspended runs
  (paused for presence, waiting to resume when empty) are unaffected. Fixes a live
  case where pebbles knocked off a plant were vacuumed up, jammed the mop-pad
  mount, and left the daily run stuck for hours.

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
