Current execution (2026-10-07): final candidate `26496c9a64f40ead6d5e8b44a6ecdc3c2c15b8d7`, binary SHA256 `83e59309f913b16b682f78bdc66d122664daa0e514a4ac30af1111bb3e1b8d86`, passed native116 original/cold validation, fresh managed notification-boundary fixture, TLS/MAS/history and SDK/Element fixture gates. First actual huge-room cold HTTP24.59s met the30s objective; later1.98s on a previously read clone retains its warm-store caveat. Production runtime selects `TOKIO_WORKER_THREADS=4`; exact pinned-base empty-runtime proof also passed. Dormant ESS selection now matches this candidate at replicas0. Production Synapse and both frontends are paused; maintenance routing is active. The fresh frozen source/control capture R3 is complete and verified. Independent Synapse and MAS database restores have completed; exact semantic validation remains pending. The first production import failed its init before any main processing because duplicate target PVC volume definitions forced a read-only mount. The actual corrected one-volume mount proof passed; the fresh import Job was recreated with UID `5b972b66-e7ff-432e-a11b-d0b037e8aab9`. Target runtime remains zero and no production write/federation boundary has been crossed. The current pack is `artifacts/production-20261007-pvc-fallback-26496c9`; explicitly select it for cold-target commands. Root gate evidence is `artifacts/production-20261007-final-gates-26496c9/`. Live status is `artifacts/production-20261007-live-26496c9-r2/status.json`; R3 evidence is its `source-capture-r3/` child. The current activation plan is `artifacts/production-20261007-activation-26496c9/README.md`: record the operational commit boundary BEFORE starting the production runtime, because background federation/push can write while fronts are closed. Historical sections below describe earlier preparation and do not override these current identities or ordering.

# reilly.asia production cutover preparation

The user authorized conditional production migration on 2026-10-06: proceed
if the rehearsal succeeds. This document records preparation and remaining gates.
Production routing, replicas, source database, Secrets and volumes have not
been changed by this preparation. Root owns execution after required evidence
passes; another approval request is not a remaining gate.

## Current source and routing

The accessible production cluster is **admin@aws-migration**, kubeconfig
`/home/ubuntu/.kube/config-aws-migration`. Namespace **ess** hosts the source;
**spindle-rehearsal** hosts the frozen rehearsal, not production.

| Role | Current production resource |
| --- | --- |
| Matrix HTTP front and well-known | `deployment/ess-haproxy`, one ready replica |
| Existing identity provider | `deployment/ess-matrix-authentication-service`, MAS 1.23.0, one ready replica |
| Source writers | `statefulset/ess-synapse-main`, `ess-synapse-fed-sender`, `ess-synapse-sliding-sync`, one replica each |
| Source version | Synapse v1.156.0 |
| Database | `postgres.postgres.svc.cluster.local:5432`, database `synapse`, role `synapse_user`; `postgres/deployment/postgres` |
| Source media | `ess/pvc/synapse-media-preseed`, UID `5f55483d-44b2-48ab-bfee-6226b3a811b2`, local-path 12Gi, RWO |
| Public Matrix API | `matrix.reilly.asia` → `ingress/ess-synapse` → `service/ess-synapse` → HAProxy |
| Federation delegation | `reilly.asia/.well-known/matrix/server` → `matrix.reilly.asia:443` |
| MAS issuer | `https://auth.reilly.asia/` |
| Public RTC service | `https://call.reilly.asia`, preserve existing external service |

Login, refresh and logout paths for API v1/r0/v3/unstable are separately routed
to MAS by ingress. General `/_matrix` and `/_synapse` paths reach HAProxy.
HAProxy has three Synapse pools: `synapse-main`, `synapse-main-failover`,
`synapse-sliding-sync`. `ess-synapse` selects HAProxy, so keeping this service
also preserves MAS's Matrix endpoint. No public DNS change is necessary for
the proposed in-cluster backend switch.

Read-only inspection on 2026-10-06 found **116 retained rooms**: v1=1, v6=6,
v9=7, v10=99, v12=3. Source `events` has 1,863,926 rows, maximum
`stream_ordering` 1,620,257. These are live observations, not a quiescent final
scope or freshness proof. PostgreSQL had 26 Synapse sessions across all three
workers and three MAS sessions. All five shutdown-profile workloads were
1/1; no production Spindle workload existed.

Credentials must stay in Kubernetes Secret references:

- `ess-synapse/POSTGRES_PASSWORD` for importer environment
  `SPINDLE_SYNAPSE_PASSWORD`; no password in importer argv.
- `ess-synapse/SIGNING_KEY` for source signing-key file; preserve its ID and
  public key, including old verify-key behavior.
- `ess-generated/MAS_SYNAPSE_SHARED_SECRET` for delegated auth, rendered into
  a tmpfs config by a nonlogging init container.

## Prepared exact routing artifacts

`scripts/production-cutover/prepare.py` only reads Kubernetes resources and
writes a new local private artifact directory. It refuses an existing output
directory and aborts on an unexpected backend template/health shape. The
reviewed run is `artifacts/production-20261006-reviewed-r2/`:

- `topology.json`: sanitized workload refs, media UID, ingress routes and
  public well-known documents.
- `haproxy-before.cfg`, `haproxy-candidate.cfg`: exact original/proposed files.
- `haproxy-switch.json`, `haproxy-rollback.json`: JSON patches that **test the
  ConfigMap UID and exact prior config** before replacement.
- `target-service.json`: proposal for `ess-spindle`, named ports
  `synapse-http:8008` and `synapse-health:8080`, both targeting server port8008.

ConfigMap `ess-haproxy` UID is
`f0351317-3ec5-41ec-9eaf-ec01b218b033`. Original config SHA-256:
`933303b953588e1ee059465dbaed77e1528ee8dcf4bf22f983c69cab824dadf4`.
Candidate SHA-256:
`b14d8a7f9776f8d939d9d6f5f2594519c1221a6510932d4446376ce546ff9532`.
The patch changes only the three server-template SRV targets to
`_synapse-http._tcp.ess-spindle.ess.svc.cluster.local.` and their GET health
paths from `/health` to `/ready`. It retains health port8080, access controls,
CORS, well-known, MAS routes and all other front/backend sections.
The reviewed candidate passed the currently installed production HAProxy3.2
syntax parser (exit0), invoked read-only with stdin, and the exact six-directive
diff/guarded rollback checks. `verification.json` records this limited proof.

Regenerate just before execution to catch intervening changes:

```bash
python3 scripts/production-cutover/prepare.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration --output artifacts/production-FRESH-RUN
```

Syntax check without writing any production file or restarting HAProxy:

```bash
kubectl --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration -n ess exec -i deploy/ess-haproxy -- \
  haproxy -c -f /dev/stdin < artifacts/production-FRESH-RUN/haproxy-candidate.cfg
```

## Selected joined-room candidate and remaining gates

The user selected joined rooms first, with departed history/invitation recovery
later. Dormant ESS still selects56db7d0 at replicas0. Cache candidate2044 passed
actual original/cold116-room native validation and historical25/25 continuity;
its staged pack is not selected for cutover. The final candidate must include
the fresh-import notification boundary currently building on
`fix/import-notification-boundary`, pass exact witnesses, and have a new pinned
production/cold pack. See [current candidate status](cutover-reilly-asia.md).
Keep `preserve_local_history=false`. Fresh final import must produce a completed
`managed_fresh` proof; legacy checkpoints cannot acquire one. Native validation
checks the durable proof/cursor; the independent report checker and cold archive
identity bind the same provenance without replacing frozen-source/domain gates.

Earlier 8e40719 runtime, media and synthetic federation proofs are historical
diagnostics, not automatic certification of the selected candidate. The pinned
base remains `oci.element.io/synapse@sha256:d2215c4a0e0bbd304489af228345b31d6857c1a228175471358d3fda187c0d91`.
The selected pack uses the separately SHA-verified executable on the whole
new target PVC, checks it before start and avoids Talos subPath resolution.
Target deployment remains dormant until required gates pass. Preserve all
source resources and independent backups. See [current cutover order](cutover-reilly-asia.md)
for exact pending finish/client/historical workers and rollback boundaries.

The importer resumes completed checkpoint rooms and phases by skipping them.
`--allow-nonempty` is an existing-store safety override, **not a proven delta
import mode**. Do not carry the rehearsal checkpoint into the final import and
claim production freshness. Supported final path is a fresh checkpoint and
empty durable target after production quiesce, with fresh exact room scope,
stream high-water and all domain validation. Full replay may require a material
outage; an online/delta alternative needs separate implementation and proof.

Default room scope also excludes departed rooms and pending invitations.
The live audit found131 departed rooms and2 pending-invite rooms outside the
116 joined-room set;60 excluded rooms contain local message history. Source
backups retain them, but the default import does not serve that history in
Spindle. The user chose joined rooms first; retain complete backups for departed
history and invitation recovery afterward. See `preservation-limits.md`.

## Ordered execution after gates

The authoritative concise sequence is [cutover-reilly-asia.md](cutover-reilly-asia.md):
finish exact-candidate rehearsal → ordered source quiesce → quiesced Helm state
→ guarded maintenance ingress → complete immutable source/control S3 capture
→ independent source restore → fresh empty target import → all domain/policy/
auth/media/signing gates → independent cold target restore → guarded original
ingress restoration while fronts remain closed → target Helm state → runtime
commit and monitored public service. No DNS change is required.

Keep all five source workloads zero through capture/import/restore. The source
capture zero-session guard runs before importer creation. The currently Ready
independent node12 PostgreSQL restore permits source restore and node11 target
import in parallel only after complete capture and fresh capacity guards.
Earlier node11 exclusive-restore examples are superseded by this measured plan.
Re-read the actual PostgreSQL pod/PVC UIDs and node at execution; recorded names
never exempt a changed identity from verification.

No successful activation runs source `resume`: reopen only saved frontend/MAS
replica counts, leaving all three source writers zero. Before any target write
or federation side effect, rollback keeps fronts closed, stops target and
restores guarded original routing/configuration/source Helm state before the
ordered saved-identity resume. After target writes, retain the written store,
stop target federation and reconcile new events before any source restoration.
Never automatically resume three source writers or roll Helm back to revision1.

## Helm ownership and persistent desired state

Live ownership inspection found **Helm release `ess`, chart
`matrix-stack:26.8.1`**, deployed revision1 in Secret
`ess/sh.helm.release.v1.ess.v1`. HAProxy ConfigMap/deployment, MAS deployment
and all three Synapse StatefulSets carry Helm ownership annotations, no
ownerReferences, and managed fields from Helm Apply on 2026-08-27. Controller
updates since then are ordinary workload status updates. No HelmRelease,
Flux, ArgoCD or ESS operator API/controller was observed; no HPA was present.
Thus direct patch/scale persists under the observed in-cluster controllers.
An external/manual Helm upgrade or rollback can still restore source routes
and writer replicas; the original launcher/repository was not found locally.

Recovered installed source is private under
`artifacts/production-20261006-helm/`: exact chart templates/files/defaults,
original user values, installed rendered manifest and full release record.
`values-private.yaml`, rendered manifests and `release-private.json` may
contain credentials: keep them mode0600 in the private directory, never print
or commit them. The upstream chart source is
[element-hq/ess-helm](https://github.com/element-hq/ess-helm), recovered from
the installed release's chart metadata. Official Helm **v4.3.0+gbec5b06** is
now available locally at `artifacts/tools/helm-v4.3.0/helm`, with its archive
SHA-256 verified against get.helm.sh:
`86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb`.
No Helm upgrade has been run. Source/quiesced/target charts all passed schema
validation and local template rendering. No cluster mutation occurred.

Two persistent overlay forms are prepared:

1. `scripts/production-cutover/helm-post-render.py` transforms each ESS render
   with `--mode quiesced` (all five replicas0), `--mode target` (three writers0,
   rendered frontend/MAS counts preserved, three backend/health changes), or
   `--mode source` (unchanged source render). It refuses a missing/changed
   six-resource profile and unexpected backend layout. Tests on the installed
   revision1 manifest show exact reviewed target config and replica behavior;
   `overlay-verification.json` records scope. Every future install/upgrade must
   include the same overlay. Helm3 accepts executable post-renderers; Helm4
   requires a post-renderer plugin instead, so the Python executable must be
   packaged for Helm4 or replaced by the native chart overlay below.
   [Helm3 post-rendering](https://docs.helm.sh/docs/v3/topics/advanced/),
   [Helm4 migration](https://docs.helm.sh/docs/overview/).
2. Native private `target-chart/` changes
   `configs/synapse/partial-haproxy.cfg.tpl`: three health paths and three
   backend template targets. A narrow `templates/ess-library/_workloads.tpl`
   override sets only the three writer replica fields0. `quiesced-chart/`
   overrides all five front/writer replica fields0 and leaves routes intact.
   These are workload replica overrides, preserving original component values
   and source configuration (a values-based replicas0 attempt also changed
   federation-sender configuration and pool size, so it was superseded).
   Exact semantic comparisons show quiesced changes **only five replicas**;
   target changes **three writer replicas, the exact reviewed HAProxy config,
   and its two checksum labels**. All source/MAS/well-known/ingress configs
   remain identical. `native-render-verification.json` records this proof.

Read-only render commands with the pinned CLI (all output
private; do not use terminal printing of manifests):

```bash
artifacts/tools/helm-v4.3.0/helm template ess artifacts/production-20261006-helm/quiesced-chart --namespace ess \
  -f artifacts/production-20261006-helm/values-private.yaml \
  > artifacts/production-20261006-helm/quiesced-new-render-private.yaml
artifacts/tools/helm-v4.3.0/helm template ess artifacts/production-20261006-helm/target-chart --namespace ess \
  -f artifacts/production-20261006-helm/values-private.yaml \
  > artifacts/production-20261006-helm/target-new-render-private.yaml
```

Compare semantic manifests and hook changes before any upgrade. Quiesce in
the existing **ordered** script first, then record the quiesced chart as
the Helm desired state; Helm's resource update ordering
alone is not a safe quiesce. After final import/readiness/key/client gates,
the reviewed target chart can become the new release
desired state. Add the target PVC/deployment/service through its separate
reviewed manifest pack. Keep all later ESS upgrades on this chart/overlay or
move it into the actual deployment repository before resuming routine upgrades.

Freeze concurrent external Helm jobs during the outage and recheck live
replicas, release revision and config SHA at every gate. Do not use automatic
rollback to revision1 while Spindle can accept writes: that revision starts
Synapse and routes traffic to it. A rollback needs fronts closed and Spindle
stopped first, then the reviewed source chart/values or source render and the
ordered saved-replica resume. The latest release must record the desired state;
live ConfigMap patches alone are an immediate switch, not upgrade persistence.

Reviewed persistent Helm commands for root, **after** ordered quiesce and final
gates at the corresponding stage (not executed):

```bash
artifacts/tools/helm-v4.3.0/helm upgrade ess \
  artifacts/production-20261006-helm/quiesced-chart --namespace ess \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --kube-context admin@aws-migration \
  -f artifacts/production-20261006-helm/values-private.yaml \
  --no-hooks --wait=watcher --timeout 5m
# Only after the final Spindle runtime/service and private gates pass:
artifacts/tools/helm-v4.3.0/helm upgrade ess \
  artifacts/production-20261006-helm/target-chart --namespace ess \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --kube-context admin@aws-migration \
  -f artifacts/production-20261006-helm/values-private.yaml \
  --no-hooks --wait=watcher --timeout 5m
```

The existing chart upgrade hooks initialize Secrets/check source config and
deployment markers. These reviewed transitions keep installed source config
and Secret refs unchanged, so hooks are deliberately suppressed; private
readiness/auth/client/federation checks remain explicit gates. Check current
release revision and freshly rendered semantic diff immediately before upgrade.
No force replacement/ownership/conflict or automatic rollback flags are used.

## Frozen source backup and isolated restore

After ordered quiesce and the quiesced Helm desired state, activate guarded
maintenance routes as described in [maintenance-cutover.md](maintenance-cutover.md).
Preserve the original ingress/Helm/control state independently. The source
exporter must mount the original media PVC read-only, with the expected UID.
All five source workloads and Synapse/MAS sessions remain zero through capture.

Use the bounded uploader-v2 path; the legacy `backup-quiescent-source.sh` full
local spool is not the operational path on this controller's limited disk.
The PG16 producer uses custom archives with `--compress=zstd:1`. A full dump,
media archive, source keys/configs, exact restore markers and immutable control
archive are all required. Omit `--preserve-local-history`: false is the selected
joined-room policy; backups still retain the entire source database.

```bash
python3 scripts/production-cutover/stream-source-backup.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration \
  --uploader spindle-cutover-stream-upload-v2 \
  --media-pod ess-spindle-source-media-export --server-name reilly.asia \
  --prefix postgres/spindle-cutover/REPLACE-WITH-FRESH-TIMESTAMP-UUID \
  --control-directory /PRIVATE/IMMUTABLE-ORIGINAL-CONTROL \
  --output /PRIVATE/NEW-source-capture
```

Require producer exits, full remote reread/conditional manifests, stable source
markers and `CAPTURE-COMPLETE`; partial prefixes do not establish a backup.
Generate the independent restore command from that actual completed capture,
not guessed object keys. The owned independent PostgreSQL pod on node12 is
Ready and empty; its pinned-image/nonroot/empty-database runtime proof is
`artifacts/production-20261007-source-pg/runtime-preflight.json`. The latest
measured capacity permits source restore there while the final importer runs
on node11. Do not reclaim the original rehearsal database for this plan.

```bash
python3 scripts/source-restore/prepare-invocation.py \
  --capture-directory /PRIVATE/NEW-source-capture \
  --output-script /PRIVATE/NEW-source-restore-node12.sh \
  --restore-output /PRIVATE/NEW-source-restore-node12 \
  --pg-pod source-backup-restore-pg16-node12 --expected-pg-node ip-10-20-1-12 \
  --floor-bytes 25769803776 --concurrent-reserve-bytes 8589934592
```

Root reviews and executes the generated script only after measuring free bytes
and confirming the recorded pod/PVC identity. The 24GiB floor remains subject
to the actual filesystem15%+1GiB minimum,15% source growth and6GiB WAL allowance.
Complete the frozen source capture before starting either restore or importer;
source capture requires every connection to production Synapse/MAS closed.
The independent restore uses its own PostgreSQL instance and unused databases.
Recheck capacity when another node12 build or cold-target restore is active.

Restore only into generated unused Synapse/MAS database names. Require real
`pg_restore`, exact frozen high-water/room/domain/identity/key markers and
preserved MAS encryption/configuration; archive decode alone is insufficient.
Preserve private mode600 diagnostics. Source media hash/target media validation
and working delegated auth remain separate gates. See [source-restore.md](source-restore.md)
for verifier details. No source freeze/production restore success is claimed.

## Capacity and preferred bounded S3 export

Historical capacity observations (remeasure at execution): host root had **5.4GiB free**.
Production Synapse database is **17,767,218,199 bytes**, MAS **18,406,423 bytes**;
source PG data is about20GiB and rehearsal PG data32GiB. Both PG mounts report
the same node filesystem,196GiB total with46GiB free; these are not independent
free-space pools. A second source restore needs roughly another17GiB before
import-target/archive/scratch overhead. Compression may reduce a dump, but no
full host-dump capacity has been established. The local-spool backup script
above should be used only if a separate destination has sufficient verified
space.

The preferred alternative uses the existing `postgres/postgres-backup-s3`
Secret and bucket, region `eu-north-1`, with a **new unique
`postgres/spindle-cutover/TIMESTAMP-UUID/` prefix**. Existing backup CronJob is not run:
it also sends notifications and removes older backups. No existing object or
backup is deleted by the prepared stream path.

Prepared `s3-stream-uploader-v2.json` is a new bounded pod in namespacepostgres,
with AWS credential/bucket Secret refs and no API token/source PVC. Its pinned
`docker.io/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f` runtime installs pinned boto3/botocore1.40.76 into a bounded
128MiB scratch volume; memory limit384MiB, CPU500m. Root creates the script
ConfigMap/pod and removes only those new resources after capture/restore:

```bash
kubectl --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration create -f scripts/production-cutover/s3-stream-script-v2.json
kubectl --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration create -f scripts/production-cutover/s3-stream-uploader-v2.json
kubectl --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration -n postgres wait --for=condition=Ready \
  pod/spindle-cutover-stream-upload-v2 --timeout=180s
python3 scripts/production-cutover/stream-source-backup.py \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration \
  --uploader spindle-cutover-stream-upload-v2 --server-name reilly.asia \
  --prefix postgres/spindle-cutover/REPLACE-WITH-FRESH-TIMESTAMP-UUID \
  --control-directory /PRIVATE/IMMUTABLE-ORIGINAL-CONTROL \
  --output artifacts/source-stream-FRESH-RUN
```

`stream-source-backup.py` verifies all five workloads fully0, both database
session counts0, the media PVC UID and read-only exporter mounts. It streams
`pg_dump`/media through subprocess pipes to S3 without a full local spool,
captures small credentials/configs privately to S3, verifies source high-water
unchanged, captures the independent restore-marker program's exact scope and
domain digests as `source-restore-markers.json`, and publishes
`CAPTURE-COMPLETE.json` only after all producers and
verification succeed. Local output holds hash/status reports only. A producer
failure leaves a prefix incomplete even if its partial bytes reached S3.

`s3-stream.py` buffers16MiB parts, conditionally completes each object with
`If-None-Match: *`, rereads the **entire** uploaded object to recompute SHA-256
and length, then conditionally writes/rereads an explicit `.sha256.json`
manifest. It refuses overwriting existing keys. It aborts only its own incomplete
multipart upload on failure; completed objects are retained for diagnosis.
This is write-once behavior for these tools, not an S3 Object Lock policy.
[AWS conditional-write documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

Offline tests passed a two-part stream/full reread, hash/size equality,
conditional-completion SDK support, and empty-stream abort. Root's constant
tiny-object preflight initially failed under `spindle-cutover/`; tested requests
show the configured IAM writer accepts the existing backup namespace `postgres/`.
Root's preflight then **passed** under
`postgres/spindle-cutover/20261006-preflight-1791292834/probe.bin`:81920bytes,
SHA-256 `63a2ca51430efb4f78a49f367c82919393c51e93d7abf631375b63c6fee9e9ed`,
including full reread and conditional manifest verification. Source backups
were not attempted in that probe. Policy/lifecycle/IAM policy reads were denied;
the prefix restriction is supported by the observed requests, not an examined
policy document. Current uploader guards accept only the permitted fresh
`postgres/spindle-cutover/` namespace.

Revisioned immutable script ConfigMapv2 carries SHA-256
`02bac19129ce89b8a14e73f1672e4feb287d748b7d73d454908158ebd8cff46f`;
its matching new pod name is `spindle-cutover-stream-upload-v2`.
Do not modify the original immutable ConfigMap or running pod in place.
Source capture still needs actual isolated Synapse/MAS/media restore. Today's
existing backup HEAD reports Synapse compressed dump3,241,417,464bytes and
MAS871,579bytes. Host free space later fell to2.5GiB, reinforcing bounded
streaming rather than full local copies. The same uploader can stream the **cold final target
archive** from its stopped store, record exact binary/checkpoint/config/key and
media provenance, and verify independent full reread before cutover.

Backup/import ordering decision (2026-10-06)

Keep the source backup controller's zero-all-Synapse/MAS-session guard and
complete capture before starting the fresh final importer. The importer does
begin a read-only repeatable-read PostgreSQL transaction (`import/synapse/
postgres.rs`), but `pg_stat_activity` alone does not establish another backend's
transaction read-only setting; allowing sessions by application name or PID
alone would weaken the independent writer guard. The small potential overlap
saving does not justify that change. All five source workloads stay fully zero
through capture and final import. Source marker capture now receives a private
log directory, and producer/uploader stderr is retained privately per object.

Final target backup streaming review (2026-10-06)

A cold final target export must stop both importer and runtime and verify no
other pod can open the target store before tar begins. Use a new read-only
whole-target-PVC exporter, preserve all Fjall files/media, final executable,
checkpoint, rendered configuration and signing-key provenance, and stream
tar directly to the proven S3 uploader under a fresh unique
`postgres/spindle-cutover/TIMESTAMP-UUID/target/` key. The current source-backup
controller places objects under `/source/`; it must not be used unchanged for
the target. The uploader itself accepts either subdirectory and performs full
reread SHA/size verification without a full host spool. Require both tar
producer and uploader exits zero; do not issue a target-complete marker after
only the uploader succeeds. Preserve private producer/uploader diagnostics.

Full local copies cannot fit the current host headroom. Target/archive/scratch
capacity shares the node filesystem even when PVC sizes differ: confirm actual
node free bytes before creating a cold clone or restore. Restore the S3 stream
onto an independent disposable PVC, compare exact files/hashes/checkpoint/
binary identity, then run read-back validation and readiness with delivery
side effects disabled. Direct streaming and a successful S3 reread alone do
not establish a functioning independent restore. Concrete prepared controller is `scripts/target-restore/cold-target.py`, with
prepared manifests under `artifacts/production-20261006-target-cold-56db7d0/`.
It uses exact frozen marker bytes, dynamic room count/ID/version checks,
original-source media and an observed15% imagefs threshold plus margin.
The controller has not yet executed actual cluster export/restore. Explicitly
select the new pack for every subcommand; its legacy default selects8e40719:

```bash
export SPINDLE_CANDIDATE_PACK=/home/ubuntu/dev/spindle-migrator/artifacts/production-20261006-pvc-fallback-56db7d0
python3 scripts/target-restore/cold-target.py export --execute \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration \
  --prefix postgres/spindle-cutover/FRESH-ROOT-RUN \
  --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/NEW-source-capture/objects.json \
  --output /PRIVATE/NEW-target-export
python3 scripts/target-restore/cold-target.py restore --execute \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration \
  --prefix postgres/spindle-cutover/FRESH-ROOT-RUN \
  --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/NEW-source-capture/objects.json \
  --target-report /PRIVATE/NEW-target-export/target-object.json \
  --restore-pvc-uid NEW_RESTORE_PVC_UID --media-pvc-uid NEW_MEDIA_PVC_UID \
  --node12-pg-growth-reserve-bytes 0 --source-media-selection local-content \
  --output /PRIVATE/NEW-target-restore
python3 scripts/target-restore/cold-target.py validate --execute \
  --kubeconfig /home/ubuntu/.kube/config-aws-migration --context admin@aws-migration \
  --prefix postgres/spindle-cutover/FRESH-ROOT-RUN \
  --source-markers /PRIVATE/source-restore-markers.json --source-capture /PRIVATE/NEW-source-capture/objects.json \
  --target-report /PRIVATE/NEW-target-export/target-object.json \
  --restore-pvc-uid NEW_RESTORE_PVC_UID --media-pvc-uid NEW_MEDIA_PVC_UID \
  --output /PRIVATE/NEW-target-restore
```

Reserve0 on node12 requires the source PG restore to have finished and no further
source PG growth planned. While it runs, use the measured remaining growth plus
WAL; the initial conservative estimate is26912450944bytes. Never use zero solely
because a target restore has started. Local-content
selection still hashes the entire source media tar; full extraction needs its
measured additional space. Root uses the detailed [cold-target invocations](../scripts/target-restore/README.md)
with this environment for export, restore and validate, fresh outputs and actual
frozen source reports/PVC UIDs. Never execute their legacy pack default.

Replay performance and scheduling audit (2026-10-06)

The remaining v6 room has104445 source rows and87297 retained accepted
nonoutlier events, of which85992 are state events;8619 events have multiple
parents. Its current state has51395 slots and3 forward extremities. The active
spool was approximately3.45GiB at the audit. These read-only aggregate
observations are recorded in `artifacts/replay-audit-20261006/`.

`replay_resolving` rebuilds the complete retained room each pass, adding all new
fold-disagreement marks to a monotonically growing set. There is no proof that
this set reaches its fixed point within eight passes. If pass8 still discovers
new marks, the importer excludes the room with a clear failure reason. Do not
raise the bound or force acceptance during this production migration. The head
state retry may invoke a second independent fixed-point run, with its own
8-pass bound. Progress logs omit call/pass identity and new-mark cardinality,
so aggregate reset count cannot predict the eventual verdict. At the audit,
the latest importer invocation had3 progress resets (observed pass4 at63000),
while earlier resets belonged to previous importer invocations. Await the
actual completed checkpoint and full validation.

Prepare pinned images/tools/configs, reviewed Helm bundles, Secrets refs,
backup uploader and target capacity before freezing the source. After
CAPTURE-COMPLETE and full-object backup verification, the fresh target importer
and backup restore verifier can be scheduled independently: importer reads
only frozen source databases and writes its new target store; restore verifier
writes only newly named rehearsal databases and checks the stored source
marker. This overlap requires explicit node capacity/memory/IO budgets and all
five source workloads continuously zero. It must not begin while source backup
capture's zero-all-session guard is still running, and routing must await both
results. Resource contention can erase the time saving; independence does not
prove a speedup. Independent post-import witness clones can run in parallel
once the cold base is validated, as prepared by the current worker.

Do not reuse a live/pre-freeze imported target to shorten downtime: the importer
has no proven delta catch-up and resumes by skipping completed rooms/phases.
Do not manually shard into simultaneous writers to one Fjall store, which
holds an exclusive lock. Selective hardest-room-first imports are also not a
reviewed production shortcut: `--rooms` still completes dependent receipt,
directory and media phases, so a subsequent broader resume can skip required
rows unless those phases are deliberately regenerated and revalidated. Keep
the reviewed single complete fresh import. There is no established flag that
parallelizes its room loop, and increasing CPU quota does not make a serial
resolver parallel or change its convergence bound.

Historical retry caveat: older cached-current/head-flag attempts failed to
establish the final authoritative source head. The selected56db7d0 repair
addresses that path; require its actual completed room checkpoint and full
read-back, not earlier progress resets or older candidate synthetic evidence.
