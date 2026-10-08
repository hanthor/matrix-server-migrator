The maintenance endpoint is prepared and locally verified. Root created its
stateless resources and started one replica on 2026-10-06 at16:28 UTC. Six
actual endpoint checks passed with empty logs. Both public Ingress specifications
still match their original Synapse routes; maintenance traffic switching has
not occurred. The saved manifest starts at zero replicas.
Review artifacts are `artifacts/maintenance-20261006-reviewed/`: `resources.json`,
`plan.json`, `verification.json`, original ingress snapshot, and guarded switch/
restore patches. The server is `scripts/production-cutover/maintenance-server.py`;
preparation and local validation are `prepare-maintenance.py` and
`check-maintenance.py` alongside it.

Actual routing and Helm desired ingress specifications match. Only15 service
references in two existing Ingress objects would change:14 routes on
`matrix.reilly.asia` (`/_matrix`, `/_synapse`, login/refresh/logout variants) and
the existing `/` MAS route on `auth.reilly.asia`. The existing TLS settings,
Ingress hosts/classes/annotations, public DNS, Matrix well-known, RTC and admin
routes remain unchanged. Original source routing is saved independently of the
candidate HAProxy/Spindle routing. Each patch tests the live object's UID,
complete exact prior spec, and each service backend before replacing only that
backend. The inverse tests the exact maintenance spec and restores the saved
original backend values. Concurrent routing/spec changes fail closed. There is
no atomic transaction across the two Ingress objects; after a partial switch,
keep the source quiesced and either finish the second guarded switch or restore
the first object using its guarded inverse.

The service returns503 JSON `M_UNKNOWN`, `Retry-After: 60` and
`Cache-Control: no-store`. Standard Matrix methods and arbitrary valid HTTP
methods receive503. OPTIONS preflight receives204 with public CORS headers;
credentials are not enabled and no incoming header values are reflected. HEAD
has no body. Internal `/ready` probes receive200; the public Matrix/MAS host
names receive503 even at `/ready`. Requests with `Expect: 100-continue` receive
503 before an upload body is requested. It logs no URLs, headers, tokens,
request bodies or exceptions. Local fake URL/header/body token checks produced
empty stdout/stderr. The handler reads no request bodies and closes connections
after responding.

The pinned existing Python image digest is
`05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f`.
It runs as UID/GID10093, with read-only root filesystem, all capabilities
removed, no privilege escalation, no service account token, no service links,
no usable DNS, no Secrets or source mounts. Its only mount is the immutable
script ConfigMap, read-only. CPU limit250m/memory64Mi bound this stateless
service. It does not connect to Synapse, MAS, PostgreSQL or S3 and therefore
adds no source database sessions. Its unique labels do not match source service
selectors. It is outside the ESS Helm release; later ESS upgrades do not create
or remove these resources.

Root-reviewed activation sequence

Prepare/start the maintenance endpoint before switching public routing. The
actual route switch follows the existing ordered quiesce, then the persistent
quiesced Helm chart. All five source workloads must remain fully zero. Existing
source backup capture can proceed while maintenance is active; its workload and
zero-database-session guards remain unchanged.

Include the independently saved original Ingress snapshot and recovered
original Helm chart/values/guarded patches plus quiesce state in a private
immutable control directory. Supply `--control-directory` to source capture
so these originals are streamed and verified off-cluster alongside the live
maintenance configuration. The control archive is not applied by the backup
restore verifier.

```sh
maintenance_k=(kubectl --kubeconfig /home/ubuntu/.kube/config-aws-migration \
  --context admin@aws-migration -n ess)
maintenance_dir=/home/ubuntu/dev/spindle-migrator/artifacts/maintenance-20261006-reviewed
# Initial setup only; root already created and started these resources.
# Skip create/scale when the reviewed endpoint already exists at replica1.
"${maintenance_k[@]}" create -f "$maintenance_dir/resources.json"
"${maintenance_k[@]}" scale deployment/ess-spindle-maintenance \
  --current-replicas=0 --replicas=1
"${maintenance_k[@]}" rollout status deployment/ess-spindle-maintenance --timeout=90s
# The reviewed ordered quiesce + quiesced Helm desired state must be complete.
# Inspect source workload zeros and maintenance endpoint readiness before routes.
"${maintenance_k[@]}" patch ingress ess-matrix-authentication-service --type=json \
  --patch-file "$maintenance_dir/ess-matrix-authentication-service-maintenance.patch.json"
"${maintenance_k[@]}" patch ingress ess-synapse --type=json \
  --patch-file "$maintenance_dir/ess-synapse-maintenance.patch.json"
```

During maintenance, suspend external/manual ESS Helm upgrades. The captured
cluster showed no Flux/Argo/ESS operator, but an external Helm upgrade can reset
these live Ingress overrides. Helm quiesced/target charts retain original
Ingress routing; reapplying them while maintenance is needed would remove the
maintenance routes. Before the target Helm upgrade or rollback to the source
Helm chart, restore both Ingress objects with their reviewed inverses:

```sh
"${maintenance_k[@]}" patch ingress ess-matrix-authentication-service --type=json \
  --patch-file "$maintenance_dir/ess-matrix-authentication-service-restore.patch.json"
"${maintenance_k[@]}" patch ingress ess-synapse --type=json \
  --patch-file "$maintenance_dir/ess-synapse-restore.patch.json"
# Continue reviewed Helm target/rollback procedure, with its own gates.
# Stop the optional endpoint only after the intended frontends are healthy.
"${maintenance_k[@]}" scale deployment/ess-spindle-maintenance \
  --current-replicas=1 --replicas=0
```

If Helm has already restored routing, the inverse's maintenance-spec tests
correctly fail; compare live specs with the independently saved original specs
rather than forcing a stale patch. Maintenance restoration grants no permission
to resume Synapse during successful Spindle activation. Rollback still requires
target stopped, target writes handled and the reviewed ordered source resume.
Restoring routes before frontend restart may produce a short unavailable window;
keep it bounded by the reviewed target/rollback steps.

Local verification passed all nine tested methods (including TRACE, CONNECT
and PROPFIND), CORS preflight, public/internal readiness separation, early
upload rejection, no token logs, pinned-image/pod-security guardrails, exact
forward/inverse patch application and stale UID/backend rejection. TLS and all
unselected original Ingress objects are byte-equivalent on local copies. No
pod, Service, ConfigMap, Ingress backend or source replica has been changed by
this preparation, and no external notifications were sent.
