# Changelog

## 0.5.1

Paired with integration 0.5.x.

- **Per-room extra times** editor: add a time (time picker), tick "vacuum only"
  per time, so a room can clean more than once a day.
- **Mop cadence** dropdown on each room, including **Never — sweep only** for
  all-rug rooms.
- **General:** "Catch up on any free (empty) day" (opportunistic catch-up) and
  "Pause cleaning on an extended absence" (holiday) toggles.
- **Presence:** "Ignore a tracker that hasn't reported for…" (stale-tracker
  watchdog).

## 0.4.0

- New **🧭 Edge clean** card: run a dedicated edge pass now, or schedule one every
  N days. Paired integration 0.4.0 sweeps thin strips along each room's walls,
  room by room. (Beta / Phase 1 — see the card's note about "invisible" edges and
  the Auto-reclean setting.)

## 0.3.4

- Paired integration 0.3.4: the "can't get home" watchdog now only watches the
  return trip, so it no longer false-fires mid-clean during long runs (an
  auto-reclean pass made the progress counters look stalled). No add-on changes.

## 0.3.3

- New **Maintenance** card on the Report tab: remaining life of every wear part
  (filter, brushes, mop pad, sensors, detergent, silver-ion) with a one-tap
  **Reset** button per part (presses the robot's own reset once you've replaced
  it). Paired integration 0.3.3 provides the per-part life + reset entity.

## 0.3.2

- Paired integration 0.3.2: after a station fault ("mop install failed") the
  scheduler now stops the robot's task (so the firmware can't auto-resume the
  failing mop job) and doesn't auto-retry — the rooms wait for the weekly
  catch-up instead of re-blocking every day. No add-on-side changes.

## 0.3.1

- Paired integration 0.3.1: a dock/station fault (e.g. "mop install failed" — the
  robot can't mount its mop pads) now gets a clear "check the mop pads" alert and
  ends the run, instead of leaving it wedged. No add-on-side changes.

## 0.3.0

- New **"Alert when a wear-part runs low"** toggle and **threshold %** field
  (Notifications) — paired integration 0.3.0 warns when the filter, brushes, mop
  pad, sensors, detergent or silver-ion module runs low, with one-tap Reset/Dismiss.

## 0.2.3

- Paired integration 0.2.3: tangle-aware recovery — a cable/cord tangle now gets
  one gentle reverse and then a clear "🪢 please free it by hand" alert, instead
  of repeatedly reversing (which just drags the tangle around). No add-on-side
  changes.

## 0.2.2

- Paired integration 0.2.2 fixes a `return_to_base` loop that could fire
  repeatedly while the dock washed the robot's mop pads (a wash cycle reports as
  "cleaning" even though the robot never left the dock). No add-on-side changes.

## 0.2.1

- New **"Honor native per-room settings"** toggle (General) — on by default; the
  scheduler respects the per-room mode / mop / suction you set in the Dreame app
  (so a sweep-only carpet room is never force-mopped).
- Paired integration 0.2.1: honor-native default, plus clearer self-resolving
  stuck alerts — wheel-motor faults ask you to check the wheels, honest "stuck"
  vs "beached" wording, an "✅ all clear" follow-up once it's back on the dock,
  and a new "can't get home" watchdog that spots a robot circling / repositioning
  but not actually making progress back to the dock (manual runs included).

## 0.2.0

Companion release to the Dreame Scheduler **integration 0.2.0**.

**Add-on**
- Clean-by-hand list and one-tap trap-learner **Apply** in the GUI.
- **Send test notification** button, and a warning for rooms scheduled on no days.
- Branding: wordmark banner as the store logo; Roboto bundled locally so the UI
  font loads correctly through ingress.

**Paired integration 0.2.0** adds self-healing recovery (reverse-out unstick,
beaching detection, block-as-a-moment, obstacle-photo alerts, silent-stuck/
stranded watchdogs), a recurring-trap learner, the multi-day scheduling fix,
per-room **"mop every N sweeps"** cadence, and opt-in **door-retry**.

Full notes: https://github.com/botts7/dreame-scheduler/releases/tag/v0.2.0

## 0.1.0

First public release of the Dreame Scheduler add-on — ingress config GUI, a
Report tab (weekly per-room status, coverage thumbnails, obstacles, run
history), ready-to-paste Lovelace cards, and Floor Plan Studio (Labs, opt-in).
