# Roadmap

Tracked feature ideas, mostly from community requests. Each entry notes how it
fits the existing architecture so it can be picked up without re-discovery.

---

## 1. Per-room "never mop" (all-rug rooms) — **implemented (0.4.x, pending release)**

**Request:** rooms that are entirely rug shouldn't mop — it just wastes water.
(HA community thread, tomofdarkness.)

**Status:** core done. A per-room `mop_every = 0` now means *never mop* — the
room always sweeps, whatever its base/native mode.

- `scheduler.advance_mop_cadence`: `n <= 0 → (count, "sweeping", will_mop=False)`.
- Engine already applies the returned per-room mode via the `cleaning_mode`
  select in `_dispatch_clean`, so this drives the robot with no engine change —
  and it overrides a native "mop" setting for that room (intended, it's an
  explicit per-room opt-in).
- Config-flow per-room step: `mop_every` is now a labelled dropdown —
  *Never — sweep only / Every clean / Every 2nd … / Every 7th*. The old
  `int(... or 1)` that silently coerced `0 → 1` was fixed.
- Reuses the existing `mop_every` storage key — no schema/migration.

**Follow-up refinement (not yet done):** skip the empty-tank **water guard**
for a dispatch whose rooms all resolve to sweep-only this run (compute each
seg's `will_mop`; if none mop, don't block on `clean_water`). Edge case only —
a mixed home's water guard still correctly protects its mop rooms.

---

## 2. Multiple cleans per day, per room (vacuum-only extra passes) — **implemented (0.4.x, pending release)**

**Request:** e.g. the kitchen gets a few quick **vacuum-only** passes through the
day, not just one scheduled clean. (Same thread, tomofdarkness.)

**Status:** done as designed below.
- New per-room `times: [{"at": "HH:MM", "mop": bool}, …]` (`ROOM_TIMES`). Absent
  → the room follows the global `daily_time` (unchanged); set → it runs at each
  slot on its `days`, and `"mop": false` forces a sweep-only pass (composes with
  Feature 1's never-mop). `scheduler.room_times` normalises/sorts/de-dups.
- Slot-aware de-dup: `week_tracker.slots_done` holds today's fired slot-keys
  (`seg@HH:MM`); `choose_dispatch` returns a `kind="slot"` Decision for due slots
  (a room cleaned at 08:00 still fires at 13:00 — `cleaned_today` isn't consulted
  for slots). Slotted rooms are dropped from the global daily fire so they never
  double-run.
- Engine: `_dispatch_clean(sweep_only=…)` forces sweep-only for tagged slots;
  `_mark_dispatched` records slot-keys (not the whole day); `next_run` folds the
  next slot in. UI: per-room extra-times editor in BOTH surfaces — a repeatable
  time-row list in the add-on (JS), and a looped "add one time at a time" sub-step
  (`async_step_room_times`) in the HA config-flow. Both store `[{at, mop}]`.
- Tests: `tests/test_logic.py` covers `room_times`, `due_slots`, the slot branch
  of `choose_dispatch`, dedupe of two same-tick slots, and the text parser.

**Original design notes (kept for reference):**

**Today:** one global `daily_time`; `scheduler.choose_dispatch` fires the daily
run **once per day** (`day_dispatched_on != today`) for `rooms_due_today` minus
`cleaned_today`. A room cleans at most once/day.

**The one real design piece is slot-aware de-dup** (per-day → per-slot):

- **Schema:** optional per-room `times: [{"at": "HH:MM", "mop": bool}, …]`
  (new `ROOM_TIMES`). Absent/empty → the room uses the global `daily_time`
  (today's behaviour; fully backward-compatible). Set → the room runs at each
  listed time on its `days`, and a slot with `"mop": false` forces sweep-only
  (composes with Feature 1).
- **De-dup:** replace the single `day_dispatched_on` date with a **set of fired
  slot-keys for today** (e.g. `"13@HH:MM"`), persisted in `week_tracker`. A room
  cleaned at 08:00 must still fire at 13:00, so the "already ran" test becomes
  slot-aware — `cleaned_today` alone is insufficient.
- **`choose_dispatch`:** in addition to the daily fire, compute **due slots**
  (rooms whose slot time ≤ now and not yet fired today) and dispatch them. It
  can now fire multiple times per day.
- **Free wins:** presence-gate, clean-window and water-guard stay in the engine
  layer, so every extra pass is gated identically (a mid-day pass won't run
  while someone's home unless configured). The weekly "rooms cleaned this week"
  counter stays "≥ once" — extra passes don't inflate it.
- **UI:** per-room "Times" list (repeatable HH:MM + a per-slot "vacuum only"
  toggle); extend the card's next-run line to the next slot across rooms.

**Effort:** medium — trigger/de-dup (`choose_dispatch` + `week_tracker`
persistence: `day_dispatched_on` → `slots_done_today`) + config-flow UI.
Start only after the current working tree is committed, to avoid layering onto
files mid-change.

---

## 3. Opportunistic (rolling) catch-up — clean pending rooms on ANY empty day — **implemented (0.4.x, pending release)**

**Request:** some days always get blocked (someone's home), so the daily clean
never fires. Rather than waiting for the single weekly catch-up day, if the house
empties on another day with spare time, use that opportunity to knock out the
rooms still pending this week. (HA community-driven; botts.)

**Status:** done as designed below. `OPT_OPPORTUNISTIC_CATCHUP` (bool, default
off); `choose_dispatch` widens the catch-up gate to `(catchup_day) OR
opportunistic`, keyed off `catchup_dispatched` (once/day, from the catch-up
time). Presence gate keeps it to empty-house windows; daily/slots keep priority;
`pending_rooms` self-drains. Toggle in the General step + add-on. Tested in
`tests/test_logic.py` (off-day fires only when enabled, waits for the time,
once/day, nothing-pending idle, daily-wins priority).

**Today:** the whole-house catch-up only fires on ONE configured `catchup_day`.
A run repeatedly blocked by presence just accumulates missed rooms until then.

**Design — treat every away-window as a catch-up opportunity (opt-in):**

- **Schema:** `OPT_OPPORTUNISTIC_CATCHUP` (bool, default off) + reuse the
  existing `catchup_time_min` as the earliest time of day it may run. A separate
  `opportunistic_dispatched_on` date fires it at most once per day (pending set
  persists to the next opportunity/day, so no spamming).
- **`choose_dispatch`:** the catch-up branch already selects `pending_rooms`
  (rooms not done this week). Broaden its gate from `weekday == catchup_day` to
  `weekday == catchup_day OR opportunistic_enabled`, keyed off its own dispatched
  date. Priority stays: slot > daily > catch-up, so it only mops up leftovers.
- **Free wins:** the engine's presence gate already means it *only* runs when the
  house is empty — i.e. exactly "when it has the opportunity." Window/battery/
  station guards apply unchanged. Once a room is cleaned it leaves `pending_rooms`,
  so the set self-drains and can't loop. The fixed `catchup_day` stays as the
  weekly backstop/guarantee.
- **UI:** a single "Catch up on any free (empty) day, not just the catch-up day"
  toggle in the General options step + add-on. Optionally cap rooms-per-
  opportunity later if a full-house catch-up mid-day is too much; start simple.

**Effort:** small — one option + a one-line gate change in `choose_dispatch`
(plus a dispatched-date field) and a toggle. No new run type; reuses catch-up.

---

## 4. Holiday / extended-away — stop re-cleaning an already-clean empty house — **implemented (0.4.x, pending release)**

**Request:** if everyone's away for an extended period (holiday), an empty house
doesn't get dirty, so repeating the daily/opportunistic clean just wastes water,
consumables and mop-pad wear. Once the house is confirmed clean, pause cleaning
until people come back. (botts.)

**Status:** done (v1). `OPT_HOLIDAY_ENABLED` (default off) + `OPT_HOLIDAY_AFTER_DAYS`
(default 3). Pure `scheduler.holiday_hold(enabled, away_days, after_days, house_clean)`;
engine `_holiday_hold(now)` computes away-days from `away_since` + `house_clean` from an
empty `pending_rooms`, gated in the run tick before resume/dispatch. The weekly reset is
the maintenance floor (pending refills → hold releases → one whole-house pass → re-settles),
so no separate maintenance cadence in v1. Surfaced as a `holiday` status attribute + a
"holiday — house already clean, cleaning paused" reason. Toggle + days in the General step
and add-on. Tested in `tests/test_logic.py`. Future: optional sub-weekly maintenance pass
and a welcome-back clean on return.

**Today:** presence only gates *while-home vs away*. There's no notion of "away
for days"; the daily fire and opportunistic catch-up keep dispatching every day
the house is empty, even when nothing has been walked on since the last clean.

**Design — a "settled" pause after the house is clean during a long absence:**

- **Schema:** `OPT_HOLIDAY_ENABLED` (bool, default off) + `OPT_HOLIDAY_AFTER_DAYS`
  (int, e.g. 2) — how long everyone must be continuously away before holiday mode
  engages. Optional `OPT_HOLIDAY_MAINTAIN_DAYS` (int, 0 = never) for a light dust
  pass every N days so a weeks-long trip still gets an occasional freshen-up.
- **Logic (engine, around `_update_presence` + `choose_dispatch`):** we already
  track `away_since`. Holiday mode = away longer than `HOLIDAY_AFTER_DAYS` AND the
  week's rooms are all done (`pending_rooms` empty — the house is confirmed clean).
  While holiday mode holds, suppress the daily + opportunistic dispatch (status:
  "holiday — house already clean"), except the optional maintenance cadence.
- **Key nuance:** the FIRST clean after everyone leaves still runs (clears the mess
  from when they were home). Only *repeat* cleans of an already-clean empty house
  are suppressed. On return (presence home), holiday mode clears and the normal
  schedule resumes; optionally trigger a welcome-back clean on the next dispatch.
- **Free wins:** no new run type; it's a gate on top of the existing dispatch.
  `pending_rooms` already tells us "house clean". Reuses `away_since`.

**Effort:** small-to-medium — one gate (away-duration + all-clean → suppress) in
the dispatch path, two/three options + toggles, and a status string. Compose with
Feature 3 (opportunistic) so a long absence settles instead of cleaning daily.
