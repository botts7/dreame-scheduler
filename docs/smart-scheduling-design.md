# Smart Scheduling — Adaptive Reachability Learning (design)

Status: **design** (not yet built). Target milestone: Insights v1.2.
Author: design draft, 2026-07-25.

## 1. Goal

The scheduler already knows *what it was asked to clean* and *what actually got
cleaned*. This feature closes the loop: it **observes when each room is actually
reachable and when the house is actually free**, learns the weekly pattern, and
**proactively suggests schedule changes** — "you clean the Toilet Tue/Thu but it's
only ever free on Saturdays; move it?".

It **suggests, never silently reschedules.** Every change is one-tap, opt-in, and
reversible. This matches the project's established philosophy (door-retry, mop
cadence, no-go learner are all advisory/opt-in).

## 2. Design principles

1. **Advisory only.** The engine proposes; the user disposes. No automatic edits.
2. **Opt-in.** Master toggle off by default. No surprise notifications for the community.
3. **Local + purge-proof.** Do **not** mine the HA recorder — its default retention
   is only 10 days, and querying it is expensive. Instead **sample our own
   observations daily** into a small rolling per-weekday tally in the tracker
   (the same way the run-history log already accumulates clean outcomes). This
   builds multi-week stats cheaply and survives recorder purges.
4. **Confidence-gated.** Never suggest on thin data. Require N weeks and a clear
   margin before speaking; dedup so each suggestion is offered once.
5. **Reuse proven patterns.** The no-go trap-learner already does
   *observe → cluster → suggest → one-tap apply* (`trap_learner` +
   `apply_learned_nogo`). Mirror that shape for schedule suggestions.

## 3. Signals (what it learns from)

All three are **sampled daily by us**, aggregated per weekday over a rolling
window (default 8 weeks), decaying old data. No recorder dependency.

| # | Signal | Source | Tells us |
|---|--------|--------|----------|
| **S1** | Clean outcome by weekday | run-history log (`history`, exists) | "Toilet succeeds on Saturdays, fails Tue/Thu" |
| **S2** | Door reachability by weekday | daily sample of each room's door sensor state during the clean window | "Bathroom door is open most Wednesdays" |
| **S3** | House-empty windows by weekday | daily sample of presence during the day | "House is reliably empty Tue/Thu 9–3" |
| **S4** | Blocked-reason by weekday (optional) | run-history skip reasons (exists) | "Blocked by door" clusters on certain days even with no HA sensor |

S1 already exists end-to-end (`weekday_stats` / `suggest_better_day`). S2/S3 are the
new sampling to add.

### Sampling model (the key architectural choice)

Add a once-per-day **observation sampler** in the engine (runs on the idle tick,
guarded to once/day/weekday). For "today" it records:

```
observation = {
  weekday, date,
  house_empty: bool | None,          # was the house empty during the window today?
  doors: { seg: "open"|"closed"|None } # each mapped door's state during the window
}
```

These append to a capped rolling log (`observations`, ~8 weeks) in the tracker.
Pure analytics then aggregate them into per-weekday reachability/empty rates — no
recorder calls, purge-proof, and cheap.

> **Decision #5:** sample a **few times across the clean window** (not a single
> instant) and take "open at any point" / "empty at any point" — more accurate.
> Keep it to a handful of samples/day to stay light.

## 4. What it suggests (outputs)

A ranked list of advisory **Suggestions**. Types:

| Type | Trigger | Example |
|------|---------|---------|
| `move_day` | S1: a *specific scheduled day* underperforms while another free day is reliable | single-day: "Move Toilet **Thu → Saturday**"; multi-day: keeps the good day, moves only the bad one |
| `add_reachable_day` | S2: a room's door is reliably open on a weekday it isn't scheduled | "Bathroom's door is open most **Wednesdays** — add Wednesday?" |
| `drop_unreachable_day` | S1+S2: scheduled day where it's ~never reachable/cleaned | "Study's never cleaned on Sundays (someone's always home) — drop it / move to Saturday" |
| `shift_window` | S3: house-empty window differs from the configured clean window | "House is empty **9–3 Tue/Thu**; your window is 9–4 — tighten?" |
| `move_catchup_day` | S3: catch-up day is when the house is fullest | "Catch-up is Saturday but Saturdays are busiest — move to **Sunday**?" |
| `manual_hint` | S1+S2: scheduled but genuinely never reachable for weeks | "Walk-in Robe never reachable — add to Clean-by-hand?" (ties into existing `manual_clean`) |

Each Suggestion object (pure):

```
{
  key: str,            # stable dedup id, e.g. "move_day:6"
  type: str,
  seg: str | None,
  current, proposed,   # e.g. days [1,3] -> [5]
  evidence: {weeks, samples, current_rate, proposed_rate},
  confidence: float,   # 0..1
  text: str,           # human-facing one-liner
}
```

### `move_day` — per-day, not per-room (decision #3)

The de-dup lesson from the multi-day scheduling fix applies here too: a room can
be scheduled on **several** weekdays, and only *some* of them may be bad. So
`move_day` operates **per scheduled day**, not per room:

- **Single-day room** (e.g. Toilet = [Thu]): if Thu is bad and Saturday is
  reliably free, propose **Thu → Sat**.
- **Multi-day room** (e.g. Lounge = [Mon, Thu]): evaluate each scheduled day on
  its own weekday stats. If **Mon** is fine but **Thu** underperforms, propose
  moving **only Thu** to the best free day — Mon stays. Never collapse a
  twice-a-week room into once a week.
- **Apply** = remove the bad weekday from the room's `days` and add the proposed
  one (in place), leaving all other scheduled days intact. Backed up + reversible.

Pure logic: `suggest_day_moves(history, seg, current_days) -> [(from_day, to_day)]`
— one entry per bad day that has a confidently-better free day. Generalises the
existing `suggest_better_day` from "one day for the room" to "per scheduled day".

## 5. Architecture

Keep the pure/glue split the project already uses (pure modules unit-tested with
no HA import; engine does the I/O).

```
observations (tracker)  ─┐
run-history log (exists) ─┼─►  insights.build_suggestions()  ─►  ranked Suggestions
door/presence samples   ─┘         (NEW, pure, tested)
                                          │
engine._maybe_surface_insights()  ◄───────┘   (glue: dedup, notify 🧠, low-freq tick)
        │
        ├─ notify [Apply] [Dismiss]
        └─ apply_insight service ─► rewrite entry.options (days/window/catchup) + backup + notify
```

### New / changed pieces

1. **`history_analytics.py`** (exists, pure) — add:
   - `reachable_weekdays(observations, seg, min_weeks, good_rate) -> set[int]`
   - `empty_weekday_windows(observations) -> per-weekday empty-rate`
   - keep `suggest_better_day` (S1).
2. **`insights.py`** (NEW, pure) — `build_suggestions(rooms_cfg, history, observations, window_cfg, catchup_cfg, opts) -> list[Suggestion]`. All thresholds/ranking/dedup keys here. Fully unit-tested.
3. **`engine.py`** (glue) —
   - `_maybe_sample_observations(now)` — once/day: record house-empty + door states for the window.
   - `_maybe_surface_insights(now)` — low-freq (once/day): build suggestions, dedup vs `insights_suggested`, notify with actions.
   - `_handle_notification_action` — extend to handle Apply/Dismiss taps.
4. **Apply service** — `dreame_scheduler.apply_insight {key}` → mutate `entry.options` (room days / window / catch-up day) with an options backup + confirmation notify. Mirrors `apply_learned_nogo`.
5. **`week_tracker.py`** — new state:
   - `observations: []` (capped rolling daily samples, ~8 weeks)
   - `insights_suggested: {key: ts}` (dedup / offered-once)
   - `insights_dismissed: {key: ts}` (user said no → cooldown)
6. **`report.py` / add-on GUI** — a "Suggestions" section listing pending insights with **Apply / Dismiss** buttons (mirrors the obstacles "+ No-go" row). `suggest_better_day` is already shown here — generalise it to the full suggestion list.
7. **`config_flow.py` + `const.py` + `en.json`** — options:
   - `OPT_INSIGHTS_ENABLED` (default **off**)
   - `OPT_INSIGHTS_MIN_WEEKS` (default **3**)
   - `OPT_INSIGHTS_NOTIFY` (default off — Report tab only unless opted in)
   - reuse existing notify targets.

## 6. Confidence & dedup rules

- **Window:** rolling **4 weeks**; samples older than that expire (stale). Weeks
  are **recency-weighted** so a changed routine wins within ~2–3 weeks instead of
  being dragged by month-old data.
- **Data floor:** need enough *fresh* samples for the weekdays involved, else stay
  silent. **Staleness guard:** if the only data is stale (no recent samples —
  e.g. the robot hasn't targeted the room lately), report "not enough recent
  data", never a stale nudge.
- **Margin:** only suggest when the proposed day is clearly better — e.g. proposed ≥ 75% reachable/clean **and** current < 50%. (`suggest_better_day` already encodes `min_attempts`/`good_rate`; reuse the same shape.)
- **One nudge per key:** once offered, record in `insights_suggested`; don't re-notify. Still visible in the Report tab.
- **Respect dismissal:** Dismiss → `insights_dismissed[key]=ts`, suppress for a long cooldown (e.g. 60 days) or until the evidence materially strengthens.
- **Rank + cap:** surface top-N (e.g. 3) by confidence to avoid a wall of nudges.

## 7. Surfaces

1. **Report tab (always, when enabled):** a Suggestions list — text + evidence + Apply/Dismiss. Passive, no nagging.
2. **Notification (opt-in `OPT_INSIGHTS_NOTIFY`):** the top new suggestion with `[Apply] [Dismiss]` actions (reuses `_handle_notification_action`).
3. **Apply:** one tap → `apply_insight` rewrites the schedule, backs up prior options, confirms. Fully reversible (the backup + the user can re-edit).

## 8. Config options (summary)

| Option | Default | Meaning |
|--------|---------|---------|
| `insights_enabled` | **off** | master opt-in for learning + suggestions |
| `insights_window_weeks` | 4 | rolling window; older samples expire (stale) |
| `insights_notify` | off | also push suggestions (else Report-tab only) |

(Recency-weight curve, stale threshold, and intra-window sample count stay as
tuned constants, not user options — keep the settings surface small.)

## 9. Build order (phases)

- **Phase 1 — clean-outcome suggestions (small; analytics exist).**
  Wire `suggest_better_day` (S1) into `insights.build_suggestions` (move_day /
  drop_unreachable_day / manual_hint), a Report "Suggestions" list, the
  `apply_insight` service, and dedup state. No new sampling needed — S1 rides the
  existing history log. **This alone delivers "the app tells you to change days."**
- **Phase 2 — door reachability (S2).** Add the daily door sampler +
  `reachable_weekdays` + `add_reachable_day` suggestions. Needs door sensors.
- **Phase 3 — presence windows (S3).** Add the house-empty sampler +
  `empty_weekday_windows` + `shift_window` / `move_catchup_day` suggestions.
- **Phase 4 — polish.** Confidence tuning, decay/seasonality, GUI polish, an
  "explain why" evidence view, notification opt-in.

Each phase is independently shippable and independently testable.

## 10. Edge cases & risks

- **Thin/sparse data** → suggest nothing (data floor). New install is silent for weeks by design.
- **Genuinely-never-reachable room** (always-shut door) → `manual_hint` into the existing Clean-by-hand list, *not* an endless day-shuffle.
- **Conflicting suggestions** → rank by confidence, cap surfaced count.
- **User keeps dismissing** → respect it (cooldown), never re-nag.
- **Schedule churn** → don't suggest a move the user just applied/reverted; cooldown per key.
- **DST / weekday math** → weekday from local `now`, consistent with the rest of the engine.
- **Recorder independence** → we sample ourselves, so retention settings are irrelevant.

## 11. Testing plan

- **Pure (`tests/test_logic.py`, no HA):**
  - `reachable_weekdays` / `empty_weekday_windows` over synthetic observation logs.
  - `insights.build_suggestions` — each suggestion type fires on the right pattern, stays silent under the data floor, dedups, respects dismissal.
  - Confidence/margin boundaries (just-below vs just-above threshold).
- **Live (Chrome DevTools on HA):** seed a few weeks of observations, confirm a
  suggestion surfaces, Apply rewrites `entry.options.rooms[*].days` (+ backup),
  Dismiss suppresses, no re-nag.

## 12. Decisions (resolved 2026-07-25)

1. **Rolling window = 4 weeks, recency-weighted + staleness-guarded.** Drop
   samples older than 4 weeks (they go *stale* and expire). Weight recent weeks
   more so the model adapts to a changed routine within a few weeks rather than
   being anchored by old data. **Staleness guard:** if a room/weekday has no
   *fresh* samples (e.g. the robot hasn't targeted it recently), mark its stats
   stale and do **not** base a suggestion on stale-only data — surface "not
   enough recent data" instead of a stale nudge.
2. **Never auto-apply.** Advisory only, always one-tap by the user. Locked.
3. **`move_day` moves the *bad day*, not the whole room** — see §4. For a
   single-day room it moves that day; for a room scheduled on several days it
   identifies the *underperforming* day(s) and moves only those to a better free
   day, leaving the good days untouched.
4. **Report tab first** for the Suggestions surface (per-room-panel apply later).
5. **Sample a few times across the window** (open/empty *at any point*) for
   accuracy, not a single window-start snapshot.

Remaining tunables (sensible defaults, not blocking): recency-weight curve,
exact stale threshold, number of intra-window samples.
