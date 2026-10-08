# Fresh frozen-source Synapse and MAS backup restore

This is a root-operated verifier for the newly captured immutable S3 source
backup. Preparation adds scripts only; no fresh restore has been executed by
this work. The verifier never writes to production, replaces an existing
database, drops any database, or starts a restored Synapse/MAS application.
It can run beside the production import once root has completed the freeze
and backup gates and confirmed the shared-node capacity reservation.

## Capture prerequisites

`production-cutover/stream-source-backup.py` uploads the frozen PostgreSQL
custom dumps, source media and protected configuration into a fresh prefix
under `postgres/spindle-cutover/`. It now also invokes
`scripts/source-restore/markers.py` read-only against the frozen source before
publishing `source/source-restore-markers.json` and `CAPTURE-COMPLETE.json`.
The completion marker is absent on any producer/upload/reread failure.

The marker collector records the exact retained room IDs/versions, event count
and stream high-water, retained event count and local user count. It collects
all public table counts, sequence allocation markers, a column/constraint/index schema fingerprint, deep
streamed SHA-256 row fingerprints for the Synapse event/history/identity/key/
backup/account-data/receipts/profile/directory/media domains, and all MAS
public table fingerprints (including encrypted identity/session fields).
Server COPY returns only sorted fixed-size row hashes; plaintext and account
credentials are never emitted. Controller buffers are 64KiB for row streams
and at most 8MiB for schema/metadata. Source and target use UTC/ISO date
formatting and C collation for hash sorting. Call it only on a frozen source
for a restore reference; separate live-table reads do not share one snapshot.

The default scope remains rooms with current local joins. For the opt-in
preservation importer, pass `--preserve-local-history` to the source backup
controller (and direct marker collection). This includes rooms in the latest
local membership ledger, records the policy/server name in the frozen marker,
and fingerprints that ledger. The source restore verifier reproduces the
captured policy automatically; target cold-restore gates reject a checkpoint
with another policy. `--server-name` supports another server identity while
keeping the prototype default. This wiring does not certify the new backend
until its class-specific import and validation tests pass.

```sh
python3 scripts/source-restore/markers.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration --namespace postgres --pod deploy/postgres \
  --synapse-db synapse --mas-db mas \
  --private-log-dir /private/frozen-source-capture/source-marker-private-logs
```

Root's local capture `objects.json` must contain the verified SHA-256/size
report for `CAPTURE-COMPLETE.json`. The verifier anchors that completion
object to the local report before trusting its member-object checksums.
Required objects include both dumps, media, original signing key, database
password, MAS homeserver shared secret, original MAS Secret objects, original
configuration objects, high-water marker and semantic marker.

When maintenance routes will be active, prepare a separate private immutable
control directory containing the original Ingress snapshots, Helm chart and
values, guarded routing patches and saved quiesce state. Pass it as
`--control-directory` to the source backup controller. Its compressed tar is
streamed directly to S3, declared in the completion manifest and fully reread
by the restore verifier. This keeps original routing available even though
the live configuration capture shows temporary maintenance backends. The
verifier checks the original archive bytes; it does not apply that control
configuration to the cluster.

## Capacity and reservation

The restored source database is roughly 17.77GB and uses the same node
filesystem as migration stores. The last reported node availability was
roughly 46GB; controller availability was roughly 3.9GB. Do not spool either
dump to the controller or make a second archive copy on the node.

The verifier reads current free bytes inside rehearsal PostgreSQL's actual
PGDATA filesystem. It requires 115% of the frozen combined source database
sizes plus 6GiB for WAL/index transients, 8GiB for concurrent production import
growth and an untouched 8GiB floor. For 17.77GB combined source data this
requires about 44.1GB available, so the reported 46GB margin is tight and must
be measured again at execution. A smaller margin fails before DB creation.

A live rehearsal PostgreSQL advisory-lock session prevents two cooperating
restore verifiers from reserving the same capacity at once. The headroom
claim is recorded in `capacity-reservation.json`; it does not preallocate disk
or constrain unrelated writers. Root must coordinate other node consumers.
During restore, available space is checked every 15 seconds; falling below
the 16GiB concurrent-growth/floor reservation cancels the restore and
terminates connections only to the newly created verifier database. The
databases remain available for inspection, and the gate fails. A failed or
interrupted run never authorizes cutover.

## Execute against a new prefix and new output directory

Once source capture completes, an exact invocation can be generated from its
local reports instead of copying a placeholder prefix or an older candidate
artifact backup. This generator performs no cluster action:

```sh
python3 scripts/source-restore/prepare-invocation.py \
  --capture-directory /private/ACTUAL-COMPLETED-SOURCE-CAPTURE \
  --output-script /private/run-fresh-source-restore.sh
```

It requires that capture's completion report, source semantic marker and
matching complete local manifest, then writes a private mode700 script with
the actual immutable prefix, capture checksum path, uploader-v2, a fresh
database suffix and an unused restore evidence directory. Root reviews and
executes that script after coordinating node capacity. Candidate binary/image
backup manifests are rejected because they lack the source completion marker.

Uploader pod `postgres/spindle-cutover-stream-upload-v2` supplies AWS/S3
credentials through its existing Secret environment. The downloader script
is passed as public Python code through `kubectl exec`; no new ConfigMap,
credential copy or credential argument is needed. Its stdout streams raw
dump bytes directly to `pg_restore` through bounded subprocess pipes. It
requires the expected byte count and SHA-256, closes the S3 body and exits
nonzero on truncation/corruption. Single-worker PostgreSQL custom archive
restore accepts stdin; this verifier uses no parallel restore or local dump.

First run capacity-only if useful; it does not create or restore databases:

```sh
python3 scripts/source-restore/verify.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration \
  --prefix postgres/spindle-cutover/FRESH-TIMESTAMP-UUID \
  --capture-reports /private/frozen-source-capture/objects.json \
  --output /private/fresh-source-capacity-check --capacity-check-only
```

Run the restore with another fresh output directory:

```sh
python3 scripts/source-restore/verify.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration \
  --prefix postgres/spindle-cutover/FRESH-TIMESTAMP-UUID \
  --capture-reports /private/frozen-source-capture/objects.json \
  --output /private/fresh-source-restore-proof
```

The verifier generates new unused names `spindle_restore_synapse_<uuid>` and
`spindle_restore_mas_<uuid>`, checks both names are absent, then creates each
from template0 with the captured libc locale/encoding. PostgreSQL major
version other than 16 or ICU locale requires explicit reviewed adaptation;
it fails closed. `--suffix` can supply a reviewed fresh lowercase name suffix;
existing names always fail instead of resuming or replacing old data.

## Passing evidence and limits

`status.json` passes only after both downloads/restores succeed, protected
source object bytes match their original capture hashes, the media archive
is fully reread and hashed without local extraction, and restored Synapse/MAS
schema/count/domain hashes exactly match the frozen reference. Synapse room
scope and high-water must match both independent capture markers. Private
reports include new database names, capacity measurements, restored markers
and original MAS Secret-data fingerprints, never Secret values or decrypted
account data. Database restore, marker query, downloader and reservation
session stderr logs are private mode600 files. PostgreSQL COPY diagnostics
can contain row fields; never paste these logs into chat or print them in a
tool response. Status exposes only sanitized error type and the private log
directory, not raw SQL/SDK exception details.

The original captured MAS encryption/signing/shared-secret configuration is
verified byte-for-byte and remains in S3 unchanged. The restored MAS rows
retain the original encrypted field fingerprints. This database-only proof
does not start MAS with substituted dark secrets or point its provisioning
endpoint at production. Any later application smoke must isolate routing and
use the original captured encryption configuration; changing that key would
invalidate identity recovery.

This proves the frozen source backup can be recovered independently. It does
not establish production routing, live owner login, decrypted production
history, or extraction/restoration of the media filesystem. Those remain
separate gates. No script drops the new databases on failure or completion;
root can inspect and explicitly clean up only these uniquely named verifier
databases after the cutover/rollback evidence is no longer needed.


Independent node12 preparation (not applied)

`scripts/source-restore/pg16-node12.json` owns a new 32Gi local-path PVC and PG16 pod `source-backup-restore-pg16-node12` in spindle-rehearsal, pinned to node12. Its immutable image matches the running rehearsal PG16 image. Postgres listens only on loopback, no Service is created, and a deny-all NetworkPolicy prevents pod traffic. Controller uses kubectl exec and PostgreSQL sockets. The existing source and rehearsal databases remain separate. Root must verify both proposed names are unused before applying; do not replace an existing resource with the same name.

The verifier and invocation generator now default to this independent pod and assert its node is ip-10-20-1-12. Actual node12 capacity is 156.68GB total and 48.60GB available. Local-path and containerd share XFS; kubelet imagefs.available eviction is 15%, stricter than nodefs.available 10%. Therefore the runtime floor is at least 24GiB, plus a minimum 2GiB concurrent target reserve. With source databases around17.77GB, the conservative 15% expansion and6GiB WAL allowance require more free bytes than node12 currently has. The verifier refuses the restore under this measured budget. Added capacity, removal of separately authorized disposable data, or an explicitly reviewed measured WAL plan is required before execution. Running the target restore later alone does not resolve the conservative source restore budget shortfall. PVC requested size is not an enforced local-path quota or independent disk reservation.

An exact root invocation is generated only after a real frozen-source CAPTURE-COMPLETE object exists:

```sh
python3 scripts/source-restore/prepare-invocation.py \
  --capture-directory /absolute/path/to/completed-source-capture \
  --output-script /absolute/path/to/new-private-run-script.sh
```

This does not execute anything. The generated script includes the node12 pod, node assertion, immutable S3 prefix, fresh unused database names,24GiB floor and2GiB concurrent reserve. Increase concurrent reserve when another node12 writer is expected; do not lower the floor below the eviction threshold. All restore and marker diagnostics stay in private logs.


Scoped rehearsal reclamation alternative (plan only; nothing dropped)

After the original import/read-back/media restore and real historical PDU gates finish, the only candidate for reclamation is database `synapse` on pod `rehearsal-pg-6bbb6fb9cc-qp4fz`, namespace `spindle-rehearsal`, Kubernetes context `admin@aws-migration`. Its measured database size is16,693,484,567 bytes. Preserve `synapse_dark` (16,690,240,535 bytes), `mas_dark`, original Spindle data, base116 cold store, checkpoints, and all production namespace/database/PVC resources. Fixture witness imports use synapse_dark, so they do not need this candidate DB. A further historical verifier must finish all source selections before reclamation.

The fi-dark-spindle PVC contains imported Spindle data, not an original database dump: read-only fi-shell inventories found no dump/backup/sql.gz file on /data or /work. Historical session evidence says the source snapshot was `/backups/2026-10-03/synapse.dump` on production namespace pg-backups. That older file must not substitute for a new exact backup of the current owned rehearsal database without independent identity/domain comparison.

Root's reviewable reclamation sequence:

1. Confirm all required rehearsal gates passed; confirm no users of the scratch database remain and no writer connections. Record namespace, podUID, PostgreSQL server identity, database OID/size, and frozen event/room/domain markers using markers.py with private logs. No production database connection is used.
2. Choose a fresh immutable prefix `postgres/spindle-cutover/REHEARSAL-RECLAIM-UNIQUE/rehearsal/`. Stream `kubectl -n spindle-rehearsal exec rehearsal-pg-6bbb6fb9cc-qp4fz -- pg_dump -U postgres -Fc synapse` directly into `kubectl -n postgres exec -i spindle-cutover-stream-upload-v2 -- python3 /script/s3-stream.py --key PREFIX/synapse.dump`. Use the specified kubeconfig/context for both. Keep both stderr streams in separate600 private files and enable shell pipefail; save the uploader JSON report privately. No full local spool. The uploader requires exact size/hash reread and conditional non-overwrite before reporting success.
3. Independently download the bounded dump through s3-download.py using that report and pass through pg_restore --file=/dev/null (no database target), requiring full payload decoding, both process statuses and stream hash success. Upload exact frozen semantic markers and a completion manifest under the same immutable prefix; pin each checksum locally. Compare frozen markers again with the owned rehearsal DB before any reclamation. This proves backup bytes and full archive decoding, not a separate full database restore. Root must explicitly assess this distinction before reclaiming the sole current scratch DB; production source and existing synapse_dark corpus remain intact.
4. Only root may subsequently reclaim the exact owned rehearsal database after identity, backup, gate and zero-active-connection checks. No executable DROP command is provided by this preparation. Never drop production postgres/synapse, synapse_dark, mas_dark or any existing PVC. Record the reclaimed database identity and exact backup report.
5. Measure free node11 filesystem space again. Its measured total210,376,851,456 bytes and imagefs15% eviction require at least32GiB floor. The verifier now checks the chosen floor against actual filesystem total*15% plus1GiB margin. After reclaim, predicted free64.99GB must cover source expansion~20.4GB +6GiB WAL +2GiB concurrency +32GiB floor (~63.3GB). Cold clone growth or other workloads can invalidate this narrow budget; the preflight must fail rather than reduce reserves.

For node11 only after these prerequisites, the invocation generator accepts `--pg-pod rehearsal-pg-6bbb6fb9cc-qp4fz --expected-pg-node ip-10-20-1-11 --floor-bytes 34359738368`. It still creates fresh unused source restore databases and never replaces either original source or fixtures. Root owns every execution step.


Production archive compression

The frozen production capture uses PG16 custom archives with explicit `pg_dump --compress=zstd:1`. Root verified the actual source pg_dump and rehearsal pg_restore compression/decompression using public.schema_version data and a schema archive; evidence is artifacts/production-20261006-s3-preflight/compression/{result.json,data-result.json}. The independent node12 manifest pins the same PG16 image digest and source restores remain streamed through pg_restore stdin. Compression does not permit a full controller spool. The already-running owned rehearsal backup uses its original default custom archive compression and is not interrupted or rewritten.


The authorized owned rehearsal backup is running as reilly-rehearsal-source-backup-20261006.service under artifacts/rehearsal-source-backup-1791295889, prefix postgres/spindle-cutover/rehearsal-source-20261006T141129Z-4089952. It collects frozen semantic markers before and after pg_dump and requires exact equality plus pg_dump exit0 and full S3 reread/conditional checksum before completion. No cleanup occurs.

Before any owned scratch reclamation, independent complete archive decoding is also required: check-rehearsal-archive.py --decode reads the verified bounded S3 object into PG16 `pg_restore --file=/dev/null` with no database target, writes no SQL to a database, and requires both hash-verifying downloader and decoder exit0. Private stderr stays in600 files; no full local spool. Result archive-decode.json must pass, alongside backup status. The separate --list result only checks TOC and does not imply decoded table payloads. Even full decoding is not a real database restore; the frozen production backup still requires fresh DB creation and exact source-marker equality. Parent may additionally defer scratch reclamation until a fresh production source CAPTURE-COMPLETE preservation anchor exists.


The independent owned rehearsal full-decoding check passed (archive-decode.json): complete bounded S3 reread hash and pg_restore --file=/dev/null both exited0. The earlier separate TOC-only attempt failed closed on a kubectl WebSocket transport error when the remote parser exited before stdin was drained; it is neither a pass nor an archive-corruption finding. Full decoding necessarily parses the TOC and supersedes this weaker check. Future TOC mode now drains remote stdin before exit; that fix was compiled but not re-run because the stronger full decode succeeded.


Capacity correction for final import peak

The final importer additionally creates a measured~3.45GiB live replay spool, alongside durable store~1.6GiB and local media115MB. Therefore previous2GiB concurrent-import estimates in this preparation are superseded: true parallel restore/import defaults to8GiB headroom. Neither WAL allowance nor eviction floor is reduced to fit. When combined capacity is insufficient, root must run fresh independent source restoration before starting the final importer, record passing restore/domain evidence, then may reclaim only explicitly authorized newly owned verified restore databases before importing.

The verifier accepts reserve0 only with explicit --exclusive-restore, repeatedly verifies that the configured owned --importer-pod has no spindle import-synapse process, and still checks free bytes against measured filesystem15%+1GiB floor. Root must coordinate all other writers; the process check covers that owned pod, not every possible importer elsewhere. The currently running original retry correctly causes the exclusivity preflight to fail. Generator defaults8GiB and forwards --exclusive-restore/--importer-pod when explicitly requested. No database reclamation is performed by either script.


Exclusive mode now also enforces the actual production target workload identities on every capacity check: `ess/job/ess-spindle-import-fresh` must be absent; `ess/deployment/ess-spindle` must have desired and reported live/ready/available/updated replicas all zero; no Pending, Running or terminating pod in namespace ess may mount PVC ess-spindle-data or belong to the fresh import Job. Terminal pods are ignored only after every reported regular/init/ephemeral container is terminated. These rules are fixed identities, not optional flags that can bypass production inspection. Any API inspection error fails closed. The owned rehearsal importer-process check remains additional. Root keeps the final ESS import Job absent until exclusive restoration finishes; repeated checks detect changes but are not a cross-resource atomic scheduler lock.

The generated exclusive invocation carries these requirements as a reviewable comment, and verify.py records the sanitized ESS workload verdict in status. Read-only live inspection currently confirms Job absent, runtime0 and no target data writers. Five contract cases (active Job, desired runtime1, reported runtime1, Pending target-PVC user, Running fresh-import owner) all reject. No source database mutation, production freeze or target workload change was executed by this preparation.

## Capacity and ordering addendum — 2026-10-07 03:27 UTC

Root's new read-only observation supersedes the earlier node12 shortfall and node11 rehearsal-reclamation proposal: node12 filesystem total **156,680,331,264 bytes**, available **112,178,106,368 bytes**; node11 total210,376,851,456, available50,697,957,376. Prefer the separate new node12 PostgreSQL instance. No rehearsal database reclamation is needed for this plan. These are observations, not reserved capacity; the verifier remeasures its actual PGDATA filesystem and enforces its dynamic floor during execution.

At an estimated17.8GB combined source size, source restore forecast115% is20.47GB. Adding6GiB WAL,24GiB untouched floor, and8GiB concurrent reserve requires **61.27GB** free; the observation leaves50.91GB beyond that budget. The24GiB floor exceeds node12's actual15%+1GiB threshold (24,575,791,514 bytes). Even counting an additional8GiB for cold-target/media restoration beyond the existing concurrent reserve leaves42.32GB. The actual captured database sizes replace these estimates. Full source/media and target archive sizes must also be measured; PVC request sizes do not create independent filesystem capacity.

Reviewed sequence:

1. Complete existing rehearsal gates. Root verifies the proposed node12 PVC/pod/NetworkPolicy names are unused, records new resource UIDs/PV affinity, and creates only these owned restore resources after review. Original source/fixture PVCs and database instances are not mounted or modified.
2. Preserve original route/Helm/quiesce controls privately, then close fronts and stop all source writers. Complete `stream-source-backup.py` with uploader-v2 and `--control-directory` before starting **any** final importer or source-database verifier. This preserves the capture's zero-all-Synapse/MAS-session guard and original control archive.
3. Generate the fresh source-restore invocation below from the actual completed capture. Run this independent node12 restore beside the fresh final importer on node11. Keep `--concurrent-reserve-bytes 8589934592` and `--floor-bytes 25769803776`; do **not** pass `--exclusive-restore`. Restore uses newly named databases on the owned PG instance, so its sessions do not conflict with production `synapse`/`mas` zero-session checks. Keep source writers/fronts closed. Fresh import scope remains joined-only; no preservation flag is added.
4. Require both final import and independent source restore/domain evidence to pass before routing. After the final importer closes its connections, stop target runtime, export its real store to S3, and restore/validate on the separate node12 target/media PVCs using an explicitly selected reviewed/promoted `SPINDLE_CANDIDATE_PACK` (currently prepared2044cb5; ESS selection waits for rehearsal). If source restore still runs, the cold-target controller's `--node12-pg-growth-reserve-bytes` must reserve remaining source growth plus WAL: a conservative initial value is **26,912,450,944 bytes** for this estimate (20.47GB+6GiB). Recalculate from captured source sizes and current progress; never silently use zero while restore can grow. Once source restore finishes and no further growth is planned, root can coordinate zero for this specific reserve. Keep the target controller's own filesystem floor and actual archive/media size checks.
5. Root records readiness/authentication/client/signing proofs and then follows the existing guarded routing sequence. Pre-write rollback can restore the saved source controls; after any target write, preserve the written target and reconcile before returning to Synapse. The new placement does not change that boundary.

Exact generator shape, using actual unused private paths:

```sh
python3 scripts/source-restore/prepare-invocation.py \
  --capture-directory /PRIVATE/ACTUAL-COMPLETED-SOURCE-CAPTURE \
  --output-script /PRIVATE/NEW-source-restore-node12.sh \
  --restore-output /PRIVATE/NEW-source-restore-node12 \
  --pg-pod source-backup-restore-pg16-node12 \
  --expected-pg-node ip-10-20-1-12 \
  --floor-bytes 25769803776 \
  --concurrent-reserve-bytes 8589934592
```

Every final target export/restore/validate invocation additionally selects:

```sh
export SPINDLE_CANDIDATE_PACK=/home/ubuntu/dev/spindle-migrator/artifacts/production-20261007-pvc-fallback-26496c9
```

## Node12 manifest security review

The original `scripts/source-restore/pg16-node12.json` starts the official image's entrypoint with its default root identity and writable root filesystem. A separate prepared variant, `scripts/source-restore/pg16-node12-reviewed.json`, preserves the pinned PG16 digest,32Gi newly owned PVC, node12 placement, loopback-only listener, no Service, disabled API token, no Secrets/source mounts, deny-all NetworkPolicy,512Mi request/2Gi limit, and90-second termination grace. It adds UID/GID999, nonroot enforcement, fsGroup999 limited to the new owned volumes, RuntimeDefault seccomp, dropped capabilities, no privilege escalation, and a read-only root filesystem. Private memory-backed `/var/run/postgresql` (16Mi) and `/tmp` (64Mi) provide the required writable socket/temp locations; PGDATA remains on its own PVC. No hostPath, subPath, privileged container, or root permissions helper is introduced. Single-worker restore remains bounded; no extra Rust build or node archive is needed.

Apply NetworkPolicy before the pod, then the fresh PVC/pod; verify actual image, nonroot identity, writable PGDATA/socket paths, filesystem capacity, and private readiness before restoring real captured data. Local JSON/scope checks passed; root subsequently deployed this exact variant and verified Ready, UID999, pinned image and only postgres/template databases. The private runtime proof is `artifacts/production-20261007-source-pg/runtime-preflight.json`; no production data has been restored yet. Root must confirm node memory/IO capacity before running the two node12 restores concurrently; filesystem headroom alone does not prove resource suitability. Detailed byte estimates are in `artifacts/production-20261007-node12-source-restore-capacity.json`.

The verifier creates new uniquely named databases and never drops them on failure/completion. Keep the owned PostgreSQL PVC and private reports through the cutover/rollback window. Cleanup addresses only recorded owned resource UIDs; no broad label deletion, production DROP, or existing rehearsal reclamation is part of this plan. Deleting the disposable pod/NetworkPolicy does not require deleting its PVC; later PVC deletion must account for local-path reclaim deletion and retained recovery evidence. Original rehearsal and dark fixtures remain intact.
