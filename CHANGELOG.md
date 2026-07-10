# Changelog

All notable changes to the Dreame Scheduler integration and its companion
add-on are documented here. This project follows [Semantic Versioning](https://semver.org/).

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
