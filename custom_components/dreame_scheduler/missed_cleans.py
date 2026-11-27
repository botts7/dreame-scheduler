"""Catch up on cleans that happened while Home Assistant wasn't watching.

Pure, no Home Assistant imports. The live follower (external_session) only sees
a clean while HA is up. A clean run from the Dreame app, the robot's button or
its own schedule while HA was restarting or offline leaves a record in the
robot's cleaning history but nothing in the scheduler.

The scheduler keeps a watermark: the timestamp of the newest robot record it
has accounted for (its own runs, followed outside cleans, catch-ups). Records
newer than that, seen while nothing is running, are missed cleans. Room
evidence comes from each record's saved cleaning map, never from area or the
rooms that happen to be scheduled:

* if the map lists the rooms it cleaned (some models do), those are used;
* otherwise (the L20 leaves that list empty) a room needs both: the clean was
  asked to do it, and the robot's recorded path spent real time in it. The path
  includes the trip to and from the dock, so "passed through" alone isn't
  enough; and the robot can be asked for a room it never reaches, so "asked
  for" alone isn't enough either. Live 2026-11-19: a Main Room clean's path
  also crossed Kitchen Dining (transit), and a 5-room run never entered
  Lounge Room2.

Credit: the approach of crediting from a record's saved map and its cleaned
segments, and the evidence rules below, come from UrbanErosion's work in
botts7/dreame-scheduler PR #5 and PR #6 (MIT). This version fetches the map
for each missed record on purpose rather than relying on what happens to be
cached, so it still works straight after an HA restart.
"""

from __future__ import annotations

from datetime import datetime

# Don't fetch more than this many saved maps from the Dreame cloud in one go.
MAX_PER_CATCHUP = 5
# A saved map that can't be fetched is retried this many times, this far apart,
# then the clean is skipped (no credit) so the cloud isn't asked forever.
MAX_FETCH_TRIES = 3
RETRY_SECONDS = 600
# A scheduler run split into more sessions than this (many recharges) falls back
# to the current-room sightings rather than fetching a map per session.
MAX_RUN_SESSIONS = 8

_FINISH_REASONS = ("task completed", "completed", "complete")


def records(history_attrs) -> list[tuple[int, dict]]:
    """(timestamp, record) for every cleaning record in the cleaning_history
    sensor's attributes, newest first (the order the integration indexes them)."""
    out = []
    for value in (history_attrs or {}).values():
        if isinstance(value, dict) and "timestamp" in value:
            try:
                out.append((int(value["timestamp"]), value))
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda r: r[0], reverse=True)
    return out


def history_index(all_records: list[tuple[int, dict]], ts: int) -> int | None:
    """1-based index of the record with timestamp ``ts`` (1 = newest), as the
    integration's history_map(index) expects. None if it isn't there."""
    for i, (rts, _) in enumerate(all_records, start=1):
        if rts == ts:
            return i
    return None


def pending(all_records: list[tuple[int, dict]], watermark: int | None,
            limit: int = MAX_PER_CATCHUP) -> list[tuple[int, dict]]:
    """Records newer than ``watermark`` that are worth fetching a map for,
    oldest first. Only completed, uninterrupted records qualify; the rest can
    never earn credit, so there's no point asking the cloud for their map."""
    if watermark is None:
        return []
    out = [(ts, r) for ts, r in all_records if ts > int(watermark) and creditable(r)]
    out.sort(key=lambda r: r[0])
    return out[:limit]


def creditable(record: dict) -> bool:
    """Completed, and not stopped early."""
    if not isinstance(record, dict) or record.get("completed") is not True:
        return False
    reason = str(record.get("interrupt_reason") or "").strip().lower()
    return not reason or reason in _FINISH_REASONS


def map_matches(map_ts, record_ts: int) -> bool:
    """The fetched map belongs to this record (guards against the history list
    shifting between reading the sensor and fetching the map)."""
    try:
        return int(float(map_ts)) == int(record_ts)
    except (TypeError, ValueError):
        return False


def _ids(value) -> set[str]:
    if isinstance(value, dict):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(v) for v in value if str(v).isdigit() and int(v) > 0}


# A room counts as visited from the path with at least this many path points
# (about 30 s of the robot actually in it). Only rooms the clean was asked to
# do are ever credited, and that is what keeps out rooms it merely drove
# through to reach them, so no share-of-path rule is needed. An earlier 5%
# share rule would have refused a tiny room in a long run: live 2026-11-23
# Lounge Room2 (~1 m2) had 33 and 35 points in two ~2000-point sessions.
MIN_PATH_POINTS = 20


def path_rooms(path_hits: dict | None, total_points: int | None = None) -> set[str]:
    """Rooms the recorded path spent real time in (see MIN_PATH_POINTS).
    ``total_points`` is accepted for callers' convenience and not used."""
    if not path_hits:
        return set()
    return {str(s) for s, n in path_hits.items() if str(s).isdigit() and int(n) >= MIN_PATH_POINTS}


def rooms_to_credit(record: dict, cleaned_segments, configured,
                    asked=None, path_hits: dict | None = None,
                    total_points: int | None = None) -> list[str]:
    """Rooms to credit for a missed clean, limited to scheduled rooms, minus any
    the record lists as blocked or skipped. [] without map evidence.

    Uses the map's cleaned-room list when it has one. Otherwise:

    * a room clean (the map lists the rooms asked for): rooms both asked for
      and visited per the recorded path, so a room it only drove through to
      reach them doesn't count;
    * a whole-home clean (no list of asked rooms; the L50 saves none for
      whole-home jobs, UrbanErosion on #6, 2026-11-27): the rooms the path
      spent real time in. A whole-home clean is meant to cover every room, so
      transit isn't the risk it is for a room clean, and this matches what the
      live follower credits for a whole-house clean while HA is up."""
    if not creditable(record):
        return []
    cleaned = _ids(cleaned_segments)
    if not cleaned:
        wanted = _ids(asked)
        visited = path_rooms(path_hits, total_points)
        cleaned = (wanted & visited) if wanted else visited
    if not cleaned:
        return []
    blocked = _ids(record.get("blocked_rooms")) | _ids(record.get("skipped_rooms"))
    return sorted((cleaned & {str(s) for s in configured}) - blocked, key=int)


def run_records(all_records: list[tuple[int, dict]], base_ts, started_ts,
                tolerance: int = 120) -> list[tuple[int, dict]]:
    """The robot's records that belong to one scheduler run: newer than the
    newest record that existed when it was dispatched (``base_ts``) and not from
    before it started. A step-aside or recharge splits a run into several."""
    out = []
    for ts, rec in all_records:
        if base_ts is not None and ts <= int(base_ts):
            continue
        if started_ts is not None and ts < int(started_ts) - tolerance:
            continue
        out.append((ts, rec))
    return out


def record_time(record_ts: int, tz) -> str:
    """ISO time a missed clean is credited at: the record's start time."""
    return datetime.fromtimestamp(int(record_ts), tz).isoformat()
