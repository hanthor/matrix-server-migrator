Current execution (2026-10-07): final candidate `26496c9a64f40ead6d5e8b44a6ecdc3c2c15b8d7`, binary SHA256 `83e59309f913b16b682f78bdc66d122664daa0e514a4ac30af1111bb3e1b8d86`, passed native116 original/cold validation, fresh managed notification-boundary fixture, TLS/MAS/history and SDK/Element fixture gates. First actual huge-room cold HTTP24.59s met the30s objective; later1.98s on a previously read clone retains its warm-store caveat. Production runtime selects `TOKIO_WORKER_THREADS=4`; exact pinned-base empty-runtime proof also passed. Dormant ESS selection now matches this candidate at replicas0. Production Synapse and both frontends are paused; maintenance routing is active. The fresh frozen source/control capture R3 is complete and verified. Independent Synapse and MAS database restores have completed; exact semantic validation remains pending. The first production import failed its init before any main processing because duplicate target PVC volume definitions forced a read-only mount. The actual corrected one-volume mount proof passed; the fresh import Job was recreated with UID `5b972b66-e7ff-432e-a11b-d0b037e8aab9`. Target runtime remains zero and no production write/federation boundary has been crossed. The current pack is `artifacts/production-20261007-pvc-fallback-26496c9`; explicitly select it for cold-target commands. Root gate evidence is `artifacts/production-20261007-final-gates-26496c9/`. Live status is `artifacts/production-20261007-live-26496c9-r2/status.json`; R3 evidence is its `source-capture-r3/` child. The current activation plan is `artifacts/production-20261007-activation-26496c9/README.md`: record the operational commit boundary BEFORE starting the production runtime, because background federation/push can write while fronts are closed. Historical sections below describe earlier preparation and do not override these current identities or ordering.

# reilly.asia cutover: joined rooms first

The user authorized production migration after the required proofs pass, with
the fastest safe procedure and no fixed downtime limit. The chosen scope is
currently joined rooms; departed history and pending invitations are a later
recovery task. Preserve complete Synapse/MAS/media backups for that recovery.
No further approval window is required. A failed gate stops progress.

## Candidate and pending rehearsal gates

As of2026-10-07 08:30 UTC, production still uses Synapse. The completed
116-room import passed exact2044cb5 original and independent cold readback:
zero divergent/short/sample mismatches, all15 domain checks,2356 media files
and original signing-key continuity. The pristine inert cold proof is
`artifacts/reilly-rehearsal-seeded-2044cb5-20261007/status.json` (complete/pass).
Actual historical PDU continuity passed25/25 across versions1/6/9/10/12 at
`artifacts/historical-pdu-actual116-cache-2044cb5-20261007-r2/`.
Exact2044 synthetic TLS and delegated MAS fixture proofs also passed.
The client cold-HTTP diagnostic needs investigation before SDK/Element proofs.

Do not select2044 for production. Scratch push defaults exposed a historical
notification scan; scratch config is now explicitly inert and the affected
copy was preserved before creating a fresh cold restore from the verified
archive. Production needs a durable notification boundary established by the
fresh importer. Successor `fix/import-notification-boundary` is building in
`spindle-import-notification-candidate`; draft source a3126b3 is not yet a
certified release. It adds immutable import provenance and atomically seeds
the push cursor after all phases. Runtime counter recovery respects that cursor.

The dormant ESS selection remains56db7d0 at replicas0. The2044 binary is staged
and its empty-runtime proof passed, but neither pack is the final cutover
candidate. Generate and pin a fresh successor pack after build/test success,
rerun its exact116-room/cold/client/history/TLS/MAS witnesses, and verify an
actual fresh small native import establishes and validates the managed fence.
Keep `preserve_local_history=false`. Rehearsal116 is a legacy checkpoint and
must remain explicitly unmanaged; final production must start with an empty
store and complete a `managed_fresh` notification proof.

After final native readback and strict domain/source gates, run
`scripts/notification-boundary.py REPORT --server-name reilly.asia` on the
fresh production report. This independent report check supplements native
validation of the durable fence/cursor. Cold tooling requires the same proof,
binds it into the stopped-store archive identity and refuses provenance changes.
Source freshness, frozen scope and per-room counts remain mandatory.

The116 rehearsal rooms are not a production scope constant. Synthetic owned
accounts prove fixture recovery and key continuity; they do not establish an
owner's production encrypted-history decryption. Preserve exact fresh proof
identities; prior binary results cannot certify the successor.

## Ordered production execution

Root owns execution. Production is context `admin@aws-migration`, namespace
`ess`; `spindle-rehearsal` is a separate frozen rehearsal. Preserve original
Secret references, MAS issuer/encryption configuration, signing key and source
PVCs. Freeze external ESS Helm jobs during the transition.

1. **Finish rehearsal and prepare identities.** Verify selected pack, binary
   SHA, dormant target deployment/PVC UIDs and runtime config. Capture original
   Helm chart/values/revision, ingress specs, guarded routing patches and saved
   workload identities in a private immutable control directory. Measure
   actual node filesystem capacity; PVC request sizes are not independent space.
2. **Quiesce, then persist quiescence.** Run the ordered
   `spindle-integration/scripts/synapse-k8s-cutover.sh quiesce --execute`
   profile with a new saved state directory: close HAProxy/MAS and stop all
   three Synapse writers. Require all five workloads fully zero and no
   Synapse/MAS database sessions. Only then upgrade ESS with the reviewed
   quiesced Helm chart. Helm update ordering is not the quiesce mechanism.
3. **Activate guarded maintenance routing.** Verify the stateless maintenance
   endpoint, then apply both UID/prior-spec-guarded ingress patches. Preserve
   original ingress/control bytes independently and keep source workloads zero.
   See [maintenance procedure](maintenance-cutover.md).
4. **Capture and independently restore the frozen source.** Run
   `scripts/production-cutover/stream-source-backup.py` with a fresh immutable
   `postgres/spindle-cutover/` prefix and `--control-directory`. Require complete
   Synapse/MAS dumps, media, original keys/configs, exact markers and control
   backup; producer success and full-object S3 reread hashes precede
   `CAPTURE-COMPLETE`. Restore into newly named unused databases and require
   exact high-water, room scope, identity/key/domain markers and original MAS
   configuration. Archive decoding alone is insufficient. Use bounded streams
   and private diagnostic logs, with no full host spool. See [source restore](source-restore.md).
5. **Import the complete fresh target.** Derive joined-room IDs/count/versions
   from frozen markers. Use an empty store, fresh checkpoint and exact binary;
   no rehearsal checkpoint, delta shortcut, selective-room resume or
   `--allow-nonempty` freshness claim. Keep federation/push/previews disabled,
   runtime zero and source media read-only. Require exact completed scope,
   empty exclusions, all 15 required domains with zero mismatches, rejection
   policy versions exactly `[3]`, original signing-key import, media byte
   comparison and delegated auth/session/device/key continuity. Refresh
   dependent phases after any explicitly reviewed room retry. Require a
   completed `managed_fresh` notification proof, exact native durable fence
   and cursor readback, and independent default joined-scope report verification.
6. **Prove an independent cold target restore.** Stop importer/runtime and
   exclude other target-store openers. Stream the complete target to S3, verify
   hashes, restore to separate owned PVCs and compare every file plus
   candidate/checkpoint/config/key identity. Validate against the still-frozen
   production source and independent original source media. Require dynamic
   scope/domain checks, nonzero media checks, readiness and original public
   signing key. Explicitly set `SPINDLE_CANDIDATE_PACK` to
   the certified final successor pack; the controller's legacy default is
   older. The restored notification proof must equal the original archive
   identity. See [cold target procedure](../scripts/target-restore/README.md).
7. **Restore guarded ingress and record target Helm state.** Keep fronts
   closed while applying both maintenance inverse patches to restore original
   ingress. Then upgrade with the reviewed target chart: HAProxy's three pools
   route to Spindle, all Synapse writers remain zero. Verify Helm/config/binary
   identities and private readiness/auth/signing gates before reopening saved
   frontend/MAS replica counts. Never use source `resume` for target activation.
8. **Commit the production runtime.** Enable reviewed production behavior
   only after preceding gates. Check public Matrix/MAS routes, existing-session
   identity/device mapping, signing response, history/media, sync and federation.
   Record when Spindle first accepts any client or federation write. Preserve
   written data; monitor errors, queues and sync lag. Future ESS upgrades must
   retain the reviewed target desired state.

Detailed routing/Helm and stream workflows are in [production preparation](production-cutover.md).
Historical observations and older pack examples there never override this
candidate/order. Actual frozen-source capture, fresh production import and
independent cold target restore remain pending execution gates.

Parallel import/source restore requires the measured eviction floor plus
margin, 6 GiB WAL allowance and **8 GiB concurrent import reserve**. If it does
not fit, finish source restore before starting the importer. Reserve zero
requires exclusive guards: ESS fresh-import Job absent, target runtime zero
and no pod able to open `ess-spindle-data`. Do not lower margins to force
concurrency. Only separately authorized, uniquely owned, verified scratch data
may be reclaimed; retain source, dark fixtures and written target.

## Rollback boundary

Before target writes or federation side effects, keep fronts closed and stop
Spindle. Restore guarded ingress/HAProxy and original MAS configuration as
needed, restore reviewed source Helm desired state, then use ordered source
resume with the saved identities/replica counts. Never force stale UID/spec
patches or automatically roll Helm back to a revision that starts writers.

After Spindle accepts writes, close fronts and disable/stop target federation,
retain the written store and backups, and reconcile new events before source
restoration. Routing reversal alone loses new events. Never automatically
resume the three Synapse writers or discard the written target.
