# Dreame Scheduler

A configuration panel for the
[Dreame Scheduler](https://github.com/botts7/dreame-scheduler) Home Assistant
integration. Install the integration first — this add-on is the GUI for it.

## What it does

Opens as a sidebar panel (via ingress) where you can, without editing YAML:

- Set presence entities, the allowed cleaning window, battery/station guards,
  return-and-resume behaviour and notification targets.
- Build per-room weekday schedules with per-room mode / suction / mop wetness
  and an optional door sensor per room.
- See a **Report**: weekly per-room status, coverage thumbnails, flagged
  obstacles and a run history.
- Copy ready-made Lovelace cards filled in with your real entity ids.
- Optionally enable **Floor Plan Studio** (in General → Labs): draw and write
  robot no-go / no-mop zones and virtual walls, rename/split/merge rooms on the
  robot's map, and design a floor plan (2D and live 3D) with your HA devices
  placed on it.

## Install

1. Install the **Dreame Scheduler** integration (HACS) and add your vacuum.
2. Install this add-on, start it, and open **Dreame Scheduler** in the sidebar.

## Security

The add-on talks to Home Assistant with the Supervisor token, which stays in the
backend and is never sent to the browser. It requests only `homeassistant_api`
(no `hassio_api`). Its internal port only accepts requests from the ingress
gateway, so other add-ons can't reach it.

## Support

Issues: <https://github.com/botts7/dreame-scheduler/issues>
