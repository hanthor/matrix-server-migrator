# Import downtime: where the time goes and options to cut it

Written 2026-10-08 after the reilly.asia production import (6h09m import, about 26h total outage including an unattended stall).
Read-only analysis of `spindle-runtime-cold-restore-candidate` (26496c9), `crates/spindle-server/src/`.

## Where the 6h09m went

- About 50 minutes: quiesce, plus a full `pg_dump` and media capture before the import.
- About 95% of the import is one room (`!iMZEhwCvbfeAYUxAjZ:t2l.io`, v6, about 87k replay events, about 51k state slots):
  - pass 1: about 2.6h;
  - pass 2: about 2.6h;
  - writing the room to the store: about 43 min.
- The other 115 rooms take about 19 minutes in total.

Reasons:
- `replay_resolving` (`import.rs:880-922`) rebuilds the full room log every pass.
- Pass 2 re-runs every full state-group check (recursive SQL that pulls about 51k rows each), every `state_after_keys` call and every ruma resolve, even though their results do not depend on the marks.
- `SnapshotSource.states` (`full.rs:1700`, `411-421`) is never populated.
- The head retry (`full.rs:1723-1734`) re-runs the whole loop without marks.
- Rooms run one at a time (`full.rs:605-617`); the parallel snapshot plumbing in `postgres.rs:73-96` is unused.

## Ranked options

| # | Change | Downtime saved | Effort | Risk |
|---|---|---|---|---|
| 1 | Keep pass 1's resolver and state-group results by event ID and reuse them in pass 2 and the head retry, carrying the marks forward. Pass 2 still checks for zero new marks. | about 2–2.5h | 1–2 days | Low |
| 2 | Run the `pg_dump` alongside the import (the importer already uses a read-only repeatable-read snapshot), and copy media before the freeze plus only the new files afterwards. | about 40–50 min | runbook only | Low–medium |
| 3 | Cache state groups and fetch only delta rows for full checks. | about 1h+ | 2–3 days | Low–medium |
| 4 | Warm the option 1 cache before the freeze, against live Synapse, keyed on (event ID, state group, row hash). At the freeze only new events miss the cache. | Hard room from about 5h to under 1h | 3–5 days plus a rehearsal comparing results | Medium |
| 5 | Seed wrong folds within a single pass, so no pass 2 is needed. | Pass 2 plus part of the write phase | 2–4 days | Medium |
| 6 | Parallel room workers | ≤15 min | 3–5 days | Medium |
| 7 | True delta importer: pre-migrate while live, then catch up above a stream high-water mark. | About 5.5h (downtime becomes roughly 20–40 min) | 2–4 weeks | High |

What #7 would need:
- Head seeding (`import.rs:1222-1245`) writes current state onto the last event, which becomes wrong once more events are appended. It must run only on the final run.
- The notification fence refuses a store that isn't empty (`notifications.rs:98-104`), so it needs a delta-resume mode.
- Mutable domains need forced re-import that also deletes rows gone from Synapse.
- Late redactions and retention pruning of old events need handling.

Recommendation: #1 and #2 first (about 3h saved, low risk), then #4 for a sub-hour cutover. Skip #7 unless migrations like this will recur.

Operational lesson: the outage ran about 20 extra hours because the host rebooted and the gate chain was driven by host-side user systemd units and agent sessions. Run the post-import gates as an in-cluster Job chain, or alert on a stalled phase.
