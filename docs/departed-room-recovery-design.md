# Departed-room recovery after joined-room cutover

Design only; no recovery command or live coalescing implementation exists. The current cutover remains joined-only. The isolated preservation candidate (`spindle-preservation-candidate`, `9cc6aef`) implements `--preserve-local-history` for a **fresh isolated target**, with synthetic/native PostgreSQL tests; it has not imported the actual departed-room corpus.

## Why the full importer cannot be reused

`spindle-server/src/import/synapse/full.rs::run` runs signing-key, users, devices, cross-signing, backups, account-data, and pushers phases before rooms, then receipts, directory, and media. `--rooms` filters room discovery, not these global phases. `--allow-nonempty` permits opening an occupied store; it does not implement collision-safe merging. `install_local_memberships` also writes/deletes forgotten markers and installs the source's latest membership. Old source state could replace newer target state.

The general migrator currently invokes this full backend and certifies its full-domain report (`crates/migrator-core/src/execute.rs`). A room recovery operation needs a distinct backend entry point, plan mode, checkpoint, and validator; a fabricated full-import checkpoint with global phases marked done is not a safe substitute.

## Smallest useful API

Proposed, unimplemented commands:

```
spindle recover-synapse-rooms plan SOURCE_MANIFEST --rooms FILE --output PRIVATE_BUNDLE
spindle recover-synapse-rooms apply CONFIG PRIVATE_BUNDLE --checkpoint PRIVATE_REPORT
spindle recover-synapse-rooms validate CONFIG PRIVATE_BUNDLE --checkpoint PRIVATE_REPORT
```

Planning/replay happens in an isolated temporary store while production runs. The initial apply implementation requires the target runtime stopped and exclusive store ownership. A second process must never open/write the live Fjall store. A future online endpoint would require integration into the running server's room locks, stream allocator, caches, and recovery transaction journal; that is additional work.

Extract reusable room discovery, class reconstruction, replay, signature/body checks, custody, and room validation from the existing backend. Construct a narrow room target rather than `full::Target`; never call `full::run` or global phase methods. Verify the local signing public identity read-only, without replacing the live seed. Referenced local users must already exist; missing/deactivated users receive explicit per-user findings, not account creation or reactivation.

The initial operation installs only **absent rooms**. Existing rooms are classified and reported, not forcibly replayed. This delivers useful departed history before implementing general coalescing.

## Source identity and durable idempotency

Use the preserved frozen cutover source, independently restored read-only, with its fully verified capture/marker hashes. Record server name, capture identity, source schema/backend versions, room version/create event, class, raw event counts, event/body digests, historical rejection decisions, archive state heads, and exact latest local membership/forgotten ledger. Bundle/report IDs and event IDs are private evidence. Planning failures do not change the target.

Bind each recovery to the target store identity and a pre-apply target fingerprint, then recheck under exclusive ownership. Inspect room log/meta, pending invitations, user-room membership/history/forgotten records, and event ownership; absence of `RoomMeta` alone is insufficient. Existing account data remains untouched. Event IDs already owned by another room fail. Conflicting PDU bodies or rejection decisions fail; legitimate redacted-copy differences require explicit version-aware validation rather than overwriting either copy.

Maintain a durable per-room recovery journal keyed by source/bundle identity, with staged/applying/complete states and batch progress. Sync target data before recording completion; a filesystem report is secondary evidence. Restart uses the identical bundle and verifies prior writes. Completed recovery is a no-op even if the room subsequently changes on target; it must not restore an old head or membership again. A changed source/bundle requires a new reviewed plan. Do not clear incomplete rooms by deleting a whole key prefix.

The initial offline apply keeps runtime stopped until interrupted rooms are resumed or rolled back and validation passes. Take a verified target backup before applying. Semantic bundle installation allocates target stream positions through the persistence spine: copying a temporary store's keys would collide with global stream counters and expose incorrect sync positions.

## Write scope and current-state precedence

Allow room logs/state nodes, original PDUs/auth-only custody, source rejection provenance, relations/redactions, room pagination mappings, authentic local membership history, and recovery provenance. For newly absent archived rooms, preserve the original departure cutoff and source forgotten preference unless target user-room evidence conflicts. Existing target choices always win; conflicting choices require a separate plan.

Never replay users, tokens, devices, cross-signing/signatures, secret-storage/account data, key backups, pushers, global push rules, directory publication, or receipts. Room account data and receipts remain unchanged initially. Local media can be a separate additive operation: verified missing blobs only, hash/ownership checks on collisions, no replacement of live metadata. The cutover already imports local media, so inventory before proposing more writes.

Frozen pending invites are historical evidence, not proof an invitation remains actionable days later. Initially retain their PDUs/stripped state in a recovery ledger; do not overwrite a target invite, decline, forget, or join. Restoring a live pending invitation needs an explicit freshness policy and meaningful tests. Departed-outlier rooms remain custody-only, without invented timeline or leave positions.

## Existing rooms and later rejoin

| Target condition | Initial recovery action |
| --- | --- |
| No room and no conflicting user-room activity | Install the validated archived room. |
| Same completed bundle | Verify provenance and retained data; no replay. |
| Existing room, including a post-cutover rejoin | Preserve target entirely; report `needs_coalescing`. |
| Version/create/body/rejection conflict | Refuse mutation and retain diagnostics. |

A later coalescing implementation must preserve every existing target event ID, linear index/cursor, current head/forward tips, pending invite, latest membership, receipts, and forgotten choice. Its smallest supported shape is a verified **older prefix**: source and target have compatible room identity and overlapping events, and all missing accepted source events lie before the held target history. Allocate new backward indices and historical indexes through a dedicated history persistence path; do not call `append_seeded` at the live head. This preserves target current state. Import authentic old membership events at their real new history positions, without projecting an old leave over a newer join.

General missing-middle/interleaved DAG history, incompatible overlap, and membership access across multiple join/leave periods need a separate algorithm; reject them rather than renumbering held events or guessing. Backfill primitives exist, but a safe bundle coalescer and its indexes/authorization checks do not. A room recovered while absent can later use the isolated candidate's tested history-preserving remote rejoin path once that runtime change is reviewed/promoted; the current production candidate does not gain that feature from a recovery report.

## Acceptance evidence before implementation is usable

Test a target with newer account/device/backup/account-data rows and prove their values unchanged; reject forbidden writes using an explicit keyspace/write allowlist. Exercise crash/resume after each room batch and completion, source/bundle mismatch, existing event ownership/body conflicts, archived/forgotten/multiple-local-departure rooms, stale invites, and outlier-only custody. For prefix coalescing, test a post-cutover rejoin and new messages: unchanged old target cursors/head/membership, authentic recovered history/cutoffs, restart equivalence, and no federation fanout. Include source versions 5/11/12 and native source state-group reconstruction.

Rehearse against an independently restored source and disposable copy of the post-cutover target, followed by cold backup/restore and client history checks. None of these follow-up requirements changes the joined-only cutover gates.
