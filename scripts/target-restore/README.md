Root-operated cold final target stream and independent restore

Status: prepared only; no exporter/restore resources applied or source/target
store changed by these tools. Python syntax/CLI, generated JSON/shell syntax,
selective tar extraction and traversal/symlink rejection tests pass. Actual
cluster export/restore/read-back remains an execution gate owned by root.

Prepared2044 manifests are historical preparation, not a selected final candidate.
Regenerate manifests and tool hashes for the certified notification-boundary successor.
Use the explicitly reviewed/promoted pack for every invocation:

    export SPINDLE_CANDIDATE_PACK=/PRIVATE/REVIEWED-FINAL-SUCCESSOR-PACK

The legacy default remains8e40719 for compatibility; omitting this selection
must not silently choose a stale candidate.2044 is staged with an empty-runtime
proof; ESS selection remains56 at0. Neither is the final notification candidate.
Export requires a completed managed_fresh proof with the default joined scope
and frozen server name. It records that proof in archive metadata; cold restore
and native validation require the same immutable provenance and durable cursor.
Legacy/supplementary/dry reports are refused even when all domain checks pass.
Cold export also requires ESS annotations to match the selected full revision
and SHA; prepared manifests and empty runtime proof do not satisfy that gate.
Exporter mounts ess/ess-spindle-data whole and read-only on node11, with no
fsGroup ownership walk, a memory metadata directory, pinned proven base and
private rendered import config/signing seed. Restore uses NEW owned node12
PVCs ess-spindle-cold-restore24Gi and ess-spindle-cold-source-media8Gi. Pod UID
10091, dropped capabilities, read-only rootfs, no API token or hostPath. Its
render init receives existing Secret keys only through volumes and copies
configuration/signing seed/database password to private tmpfs. Restore and
validation write only these newly owned disposable volumes/tmpfs.

Root applies reviewed manifests. Capture newly created disposable PVC UIDs,
verify PV node affinity really is node12 and distinct from BOTH production
source and target, and retain identities. The controller does not apply or
delete resources. It checks exact PVC UIDs before restore/validation.

A full stopped-target export requires deployment replicas/status zero, the
fresh import Job succeeded and inactive, all five source workloads zero, no
source DB sessions, fresh high-water unchanged and no other active pod able
to open the original target. Completed diagnostic pods do not open stores.
It derives expected room IDs/count/version from the exact frozen source
marker bytes (SHA checked against source capture objects.json), compares the
checkpoint scope/per-room source event counts (new frozen markers include
these), checks the aggregate retained event count, and runs the existing strict migrator-cli report gates with
that dynamic count. It never hardcodes116. Run only after capture completes:

The checkpoint preservation policy must also match the frozen source marker.
Opt-in departed-history markers include latest-local membership rooms, and
restore validation forwards `--preserve-local-history`. The new backend and
exact candidate still need rehearsal before this mode is used in production.

    python3 scripts/target-restore/cold-target.py export --execute --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration --prefix postgres/spindle-cutover/FRESH-ROOT-RUN --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/source-capture/objects.json --output /PRIVATE/NEW-target-export

Keep the same fresh prefix used by source capture. Export hashes every target
file, records binary/checkpoint/config/signing provenance, streams uncompressed
tar directly through pipes to the proven uploader-v2, requires producer and
uploader exits zero and full S3 reread verification. No full archive is written
to local host or node. target-object.json contains its exact key/size/hash.
The archive contains signing/identity secrets and must remain private.

Independent restore streams the verified target archive and frozen source
media.tar to fresh node12 volumes. It compares every restored target file
size/hash, config/key metadata hashes, exact binary SHA and room scope before
starting any application. Original Synapse media layout is required: the
validator expects local_content/aa/bb/rest and skips missing source files.
Restored Spindle media must never substitute for this independent source.

    python3 scripts/target-restore/cold-target.py restore --execute --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration --prefix postgres/spindle-cutover/FRESH-ROOT-RUN --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/source-capture/objects.json --target-report /PRIVATE/NEW-target-export/target-object.json --restore-pvc-uid NEW_RESTORE_PVC_UID --media-pvc-uid NEW_MEDIA_PVC_UID --node12-pg-growth-reserve-bytes 26912450944 --output /PRIVATE/NEW-target-restore

The independent source PG16 restore is now an owned Ready node12 pod, distinct
from source and rehearsal databases. Source capture must finish, including all
CAPTURE-COMPLETE/full-reread gates and zero source sessions, before starting
fresh target import and independent source restore in parallel. Cold-target
restore on node12 must reserve any remaining PG database growth plus WAL. The
initial conservative estimate is26,912,450,944 bytes; recalculate from the
fresh captured database sizes and actual progress. Zero requires completed
source restore and root/client coordination that no future PG growth is planned.

Actual statvfs capacity must exceed archive bytes + source media budget +
declared concurrent PG growth +16% filesystem floor (at least8GiB). The source
PG restore separately enforces its24Gi floor and8Gi concurrent reserve.
Node12 kubelet configz confirms imagefs hard15%, nodefs hard10%; containerd,
kubelet and local-path share the same XFS backing pool. At2026-10-07 03:27UTC,
root measured total156,680,331,264 / available112,178,106,368 bytes; remeasure
at execution. No rehearsal database drop is needed for this node12 plan.

Default extraction restores the complete4.9GB source media archive. If shared
headroom requires it, --source-media-selection local-content extracts only
media_store/local_content (~115MB observed, bounded256MiB). It still streams
and checks the entire original media.tar SHA/size, and rejects traversal,
absolute names, all symlinks/hardlinks and special files even in skipped
entries. The media byte validator uses local_content only; thumbnails/remote
cache do not affect that check. Full extraction remains stronger file-level
restore evidence. Any failed extraction leaves an INCOMPLETE disposable copy;
never accept it or retry over the nonempty destination.

Validate against original STILL FROZEN production PostgreSQL. This changes
only the restored target/checkpoint and private tmpfs, with federation, push
and previews disabled. It requires every domain to retain the original number
of compared rows and media comparison rows greater than zero, preventing an
empty source-media mount from silently passing. Credentials are never command
arguments or printed. Reuse the successful restore evidence directory:

    python3 scripts/target-restore/cold-target.py validate --execute --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration --prefix postgres/spindle-cutover/FRESH-ROOT-RUN --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/source-capture/objects.json --target-report /PRIVATE/NEW-target-export/target-object.json --restore-pvc-uid NEW_RESTORE_PVC_UID --media-pvc-uid NEW_MEDIA_PVC_UID --output /PRIVATE/NEW-target-restore

Require validate-passed.json passedtrue AND root readiness, existing-session/
client E2EE, signing continuity and routing gates before public writes. Do not
start federation on this imported witness. Root cleans up ONLY owned exporter/
restore pods and disposable PVCs after preserving evidence; these tools never
delete production source/target PVCs or change routing.

Export requires the archived signing seed SHA to match the verified frozen
source signing.key object. Metadata config/key files explicitly use0400.
Validation stdout/stderr remain private on disk; stream cleanup escalates
terminate to kill/wait and preserves the primary producer/consumer error.

Read-only checkpoint, identity, inventory and capacity JSON fetches omit the
stdin channel and retry at most three times, each bounded to300 seconds, on
transport/parse truncation. Only an exact allowlist of read operations retries.
Metadata writes, extraction, upload and native validation never retry implicitly.
