"""Pause/resume decisions for a run: pure, no Home Assistant imports.

Three holes found live (Mon 2026-11-02):

* Resuming a suspended run with a bare ``vacuum.start`` on a robot that had
  errored ("blocked") made it clean the whole house: the firmware's paused task
  was no longer a clean breakpoint. Only a healthy, genuinely paused task may be
  continued that way; anything else re-dispatches our own rooms.
* The run's "needs help" flag was set by an error before it paused, so when the
  robot errored again while paused nobody was told (it sat blocked ~1.5 h).
* Dreame's task_status stayed ``room_cleaning`` for 6 h while the robot slept on
  the dock, and the run was held open the whole time. When the robot's own
  history record for this run says completed and the robot is parked, those
  task words are stale.
"""

from __future__ import annotations


def resume_with_start(paused_task: bool, error_active: bool, run_errored: bool) -> bool:
    """True when a bare vacuum.start may continue the paused breakpoint.

    Needs the robot to report a paused task, no live error, and no error during
    this run. Otherwise the caller re-dispatches the run's own segments."""
    return bool(paused_task) and not error_active and not run_errored


def stuck_flag_on_pause_change(notified: bool, error_active_now: bool) -> bool:
    """The ``notified_stuck`` value to carry across a suspend or a resume.

    Keep it only while the error that was alerted is still live, so a fresh
    error after the change gets its own alert, and a live one isn't repeated."""
    return bool(notified) and bool(error_active_now)


def task_words_stale(vacuum_parked: bool, status_active: bool,
                     record_completed: bool) -> bool:
    """True when task_status 'cleaning' words should be ignored.

    The robot is parked (docked/idle), its status shows no cleaning, and this
    run's history record says the task completed. A mid-task dock (mop wash,
    recharge) logs completed=False, so it is unaffected."""
    return bool(vacuum_parked) and not status_active and bool(record_completed)
