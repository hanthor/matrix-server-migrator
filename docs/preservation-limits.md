Observed room preservation limits and federation source parity

Read-only production audit at2026-10-06 14:59:01UTC found249 rooms. Default
importer retains116 with at least one CURRENT joined :reilly.asia member in
current_state_events joined to room_memberships. It skips the other133 before
applying only_rooms, so that filter can narrow this set but cannot include
rooms lacking local joins. This audit does not change the import scope.

| Outside the retained room set | Rooms |
| --- | ---: |
| Latest local invite (local_current_membership) | 2 |
| Latest local leave (local_current_membership) | 131 |
| Latest local ban / knock / join | 0 / 0 / 0 |
| Historical event with a local sender | 131 |
| Historical local m.room.message or m.room.encrypted sender | 60 |

All133 lack a local membership in current_state_events. Synapse's separate
local_current_membership table still records the two invites and131 leaves;
looking only at current room state would miss that preservation limit.
Historical sender counts include state/auth/rejected/outlier events, so the
60-room message/encrypted-event count is the narrower indication of local
conversation history. These counts overlap rather than add together.

The133 skipped rooms contain453,620 raw source events, including7,705 with
local senders and6,067 local message/encrypted events. Default room-history
migration does not include those rooms or the two pending-invite rooms. Global
user/device/backup/account data and retained-room authentication dependencies
are separate import domains; their preservation does not establish serving
archived room timelines. Full frozen Synapse PostgreSQL backup retains the
original data for later recovery. Do not describe this migration as preserving
all source room history. Production remains active during this measurement;
final frozen markers must supply the actual final room set and event counts.

Evidence: artifacts/production-20261006-pvc-fallback-8e40719/outside-retained-scope.json.
Reproduction: scripts/scope-audit/outside-retained.py (read-only aggregate SQL;
no room IDs, plaintext, session tokens or credentials emitted).

Federation source comparison

The preserved e4fd7d5141f7b97e39910d1fb1f305db355b9c301366650a1f7674d73773db5a
binary matches the retry artifact. Its preserved source worktree is286a726
plus the head-seeding repair. Comparing106 tracked server/core/dependency
files to final8e40719 shows only config.rs, lib.rs, media.rs and routes.rs
changed, all for configurable media limits. The patched importer is byte
identical; federation.rs, signing.rs, core code and Cargo manifests/lock are
unchanged. routes.rs changes only media route cap/config wiring. The deliberate
media change also raises remote-media caching allowance from50MiB to the
configured100MiB; it does not change federation handshake/message/state routes.

Existing private-CA Synapse1.156.0↔e4 tests passed joins, bidirectional messages
and topic state in ten synthetic rooms covering versions1/6/9/10/12. Exact
final4036a6073191eea49a8bfc58af4b1dbef096774c89cbabf6569ef158b3bba092 also
passed private-CA Spindle↔Spindle federation. It is reasonable to reuse the
Synapse compatibility evidence for the unchanged federation wire/signing/TLS
implementation, with the original tested binary SHA retained. That is an
inference from source parity, not a Synapse↔exact-final rerun. Neither synthetic
result establishes production DNS/routing, delegation or imported historical
PDU behavior beyond their recorded scope. This audit adds no execution gate.

Evidence: artifacts/production-20261006-pvc-fallback-8e40719/federation-source-parity.json;
artifacts/federation-20261006-synapse-r3/result.json;
artifacts/federation-final-20261006-tls/result.json.
