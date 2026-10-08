# Final source consistency for reilly.asia

Read-only evidence at **2026-10-06 12:50:37 UTC** shows that the rehearsal
is not a current production migration. The source snapshot's newest received
event is from October 3 at 13:28:29 UTC; production continues receiving events.

| Marker | Restored rehearsal | Production |
|---|---:|---:|
| Event rows | 1,840,117 | 1,863,960 |
| Highest event stream ordering | 1,596,434 | 1,620,291 |
| Events in retained rooms | 1,386,497 | 1,410,340 |
| Retained rooms | 116 | 116 |
| Local users | 1,371 | 1,371 |
| Database bytes | 16,693,484,567 | 17,767,152,663 |

The exact room-ID digest matches, but the current-state digest differs.
There are **23,843 additional event rows**, all in retained rooms. The
rehearsal cannot be promoted directly. Saved marker evidence is in
`artifacts/reilly-2026-10-06/source-freshness-20261006.json`.

## Capture markers

`scripts/source-freshness/capture.py` runs one repeatable-read, read-only
transaction. It reads event/count/scope/state markers and never selects
credentials, access-token values, passwords, private keys, or account contents.

```bash
python3 scripts/source-freshness/capture.py \
  --namespace postgres --pod postgres-576759d59f-rhdj6
python3 scripts/source-freshness/capture.py \
  --namespace spindle-rehearsal --pod rehearsal-pg-6bbb6fb9cc-qp4fz
```

Pod names may change. Marker equality alone does not prove quiescence:
metadata can change without changing event high-water marks or row counts.
Actual writers must stay stopped through final import and routing commit.

## Final import procedure

1. Finish rehearsal gates and retain its stopped-store archive. Preserve
   production manifests/replica counts, proxy routing, Synapse media, source
   signing key, PostgreSQL backup, and MAS database/config for rollback.
2. Quiesce every source writer, including Synapse main, federation sender,
   sliding-sync, MAS session/account writers, and any background maintenance
   or administrative integrations. The observed database application names
   are `ess-synapse-main-0`, `ess-synapse-fed-sender-0`,
   `ess-synapse-sliding-sync-0`, and `matrix-authentication-service` (DB `mas`).
   Stopping only public client routing does not stop federation ingestion.
3. Capture markers after shutdown, verify remaining `pg_stat_activity`
   connections are understood, and repeat markers before routing changes.
   Preserve a fresh source PostgreSQL dump/snapshot and matching media copy
   after writers stop, without replacing the rehearsal's source/database.
4. Use the verified exact binary and source signing key to import the frozen
   source into a **new empty store and new checkpoint**, with a separate
   configuration pointing at the new storage directory. Supply the freshly
   captured source connection and media root. Discover the frozen scope
   again; do not assume 116 rooms if membership changes before shutdown.
5. Run original-source read-back validation, strict report gates, cold archive
   and off-cluster checksum transfer, scratch restore/read-back, original
   public signing-key witness, and client/session/federation continuity
   checks on the final import before switching routing.
6. Switch routing only while Synapse stays stopped. Retain rollback inputs.
   A rollback after Spindle accepts writes requires preserving those new
   events; routing back alone cannot copy them into Synapse.

The imported full report at this inspection records **19,165.7 seconds
(5 hours 19 minutes)** for the previous 115-room result, excluding the still
running retry's final success. This is observed rehearsal work, not a final
downtime promise. The existing store is 1.5 GB and source media copy 124 MB;
these are much smaller than the 17.8 GB PostgreSQL source.

## Why checkpoint resume cannot serve as a delta import

The exact validated source skips every room already in `report.rooms`
(`full.rs:551`) and completed phases in `report.phases_done`
(`full.rs:618`). Resuming the stale checkpoint will not ingest newly received
events or refreshed devices, sessions, account data, and keys. Clearing a
checkpoint against a populated store is not the supported empty-target
operation; deletions and rewritten derived state would need separate proof.

The source reader correctly uses one `REPEATABLE READ READ ONLY` transaction
(`postgres.rs:53`) for each invocation. Across resumptions, however, a new
invocation gets a new snapshot; a live source therefore permits mixed-time
checkpoint contents. Keep the final source frozen across retries, validation,
backup, and cutover. A shorter freeze would require an implemented and tested
delta engine covering additions, updates, deletions, non-room domains, and
media; that capability is not established by the current rehearsal.

## Current operator contract and future shorter outages

Fresh validation in the shared migrator compares each checkpoint room's
`source_events` with read-only inventory, in addition to exact room IDs and
server identity. The importer records this count before replay filtering, so
rejected/outlier rows remain part of the source count. This detects newer
messages that do not change current state. It does not detect same-count
updates or substitutions, or certify unchanged account/key domains. Keep a
frozen source and capture its independent markers across the entire final run.

For this migration the user accepts the full safe write pause. Prepare the
binary, volumes, manifests, rehearsal, and rollback before closing the front
doors; keep source data intact, import once from a fresh checkpoint, and reopen
only after validation. This minimizes avoidable time inside the pause.

For other operators, expose the measured rehearsal duration and checkpoint
progress before scheduling the pause. A shorter outage needs a separate delta
implementation: import a live initial snapshot, retain an ordered source change
log, then freeze and apply every addition/update/deletion across room events,
state, identity, devices, keys, account data, receipts, and media. Prove the
result against a full frozen import, including encrypted clients and rollback,
before offering that mode. Current checkpoint resume is only crash recovery
against the same frozen source and must never be advertised as delta import.
