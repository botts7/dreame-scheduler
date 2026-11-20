# External cleaning credit

Enable **Credit completed cleans started outside the scheduler** in the integration's General options. It defaults to off and can also be set through `set_config` as `external_credits`. Import starts from the time the option is enabled. Disabling and re-enabling starts a fresh import window.

This credits completed app/button/automation-started whole-home or room passes in the scheduler's existing room model. It updates this week's cleaned rooms, the persistent `confirmed` timestamps introduced in v0.6.5, and the last-clean time used by the stale-house nudge. It does not start the robot or change its cleaning settings.

## Evidence and limitations

- Requires a single saved floor, multi-map disabled, and the scheduler's default map. Matching segment numbers alone cannot identify a floor.
- Requires an explicitly completed record with no interruption reason, positive cleaned area, and a completion estimate that is not in the future.
- Requires a cached historical map matching both the record timestamp and saved-map ID, with explicit `cleaned_segments` evidence. Requested segments and total area alone never imply that a room was cleaned. Blocked/skipped, disabled, and unknown rooms are excluded.
- Zone/spot/partial work is excluded. Models without cleaned-room evidence receive no automatic credit. The existing manual confirmation remains available.
- Uses the Dreame integration's private `_cleaning_history` and `_history_map_data` caches through its resolved coordinator. It does not fetch map data or make network calls. If a map is not cached, that record is retried on later ticks while it remains in history. Depending on the integration/model, viewing a historical map may be needed to populate the cache. This is deliberately conservative and is a compatibility limitation.
- Each evidenced pass is imported once. Vacuum/mop passes remain separate history rows at their original estimated finish times. Combining them under the final pass time could hide an intervening failure for a room cleaned only in the first pass. The timestamp is start plus reported cleaning minutes, not an exact wall-clock completion time.
- Installed mop pads do not prove that mopping occurred, so unknown cleaning modes remain unknown and mop-cadence counters are not changed.
- Completed scheduler and edge dispatch intervals are persisted to exclude their later history. The ownership cache is bounded; expiring intervals also advance the import cutoff so old scheduler runs cannot be reimported.
- Later room failures and newer confirmed cleans are preserved. Prior-week records update persistent confirmation without populating the current week's cleaned set.

## Verification

Run `python tests/test_external_history.py`, `python tests/test_logic.py`, `python tests/test_multimap.py`, and `python tests/test_edge_geometry.py`.

The external-history tests include the actual reconciliation and ownership methods with minimal runtime doubles. They cover malformed/incomplete records, blocked rooms, missing/stale/wrong-floor maps, delayed maps, repeated polling and serialized reload, cross-week/local-time credit, sequential-pass ordering, later misses, newer confirmations, history retention, and retry after a failed storage write.

This split has not been validated with a physical robot run or a full Home Assistant runtime. It needs that validation before claiming model compatibility.
