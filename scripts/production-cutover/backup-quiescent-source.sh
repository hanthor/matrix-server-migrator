#!/usr/bin/env bash
# Root may run only after ordered ESS quiesce. Read-only on production data.
set -euo pipefail
config=${1:?kubeconfig required}
context=${2:?context required}
output=${3:?new private output directory required}
media_pod=${4:?root-created read-only media exporter pod required}
k=(kubectl --kubeconfig "$config" --context "$context")
test ! -e "$output"
for resource in deployment/ess-haproxy deployment/ess-matrix-authentication-service \
  statefulset/ess-synapse-main statefulset/ess-synapse-fed-sender statefulset/ess-synapse-sliding-sync; do
  result=$("${k[@]}" -n ess get "$resource" -o json)
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["spec"]["replicas"]==0; assert d.get("status",{}).get("replicas",0)==0; assert d.get("status",{}).get("readyReplicas",0)==0' <<< "$result"
done
sessions=$("${k[@]}" -n postgres exec deploy/postgres -- psql -X -U postgres -d postgres -Atc \
  "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname IN ('synapse','mas')")
test "$sessions" = 0
test "$("${k[@]}" -n ess get pvc synapse-media-preseed -o jsonpath='{.metadata.uid}')" = 5f55483d-44b2-48ab-bfee-6226b3a811b2
# Verify the exporter's identity and read-only source mount before reading it.
"${k[@]}" -n ess get pod "$media_pod" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); volumes=d["spec"]["volumes"]; mounts=d["spec"]["containers"][0]["volumeMounts"]
assert len(d["spec"]["containers"])==1
assert any(v.get("persistentVolumeClaim",{}).get("claimName")=="synapse-media-preseed" and v["persistentVolumeClaim"].get("readOnly") is True for v in volumes)
assert all(m.get("readOnly") is True for m in mounts)
'
umask 077
mkdir -p "$output"
"${k[@]}" -n postgres exec deploy/postgres -- psql -X -U postgres -d synapse -Atc \
  "SELECT json_build_object('event_rows',count(*),'stream_high_water',max(stream_ordering)) FROM events" \
  > "$output/source-high-water.json"
for database in synapse mas; do
  "${k[@]}" -n postgres exec deploy/postgres -- pg_dump -U postgres --format=custom \
    --no-owner --no-privileges "$database" > "$output/$database.dump.partial"
  test -s "$output/$database.dump.partial"
  mv "$output/$database.dump.partial" "$output/$database.dump"
done
"${k[@]}" -n ess exec "$media_pod" -- tar -C /media -cf - . > "$output/media.tar.partial"
test -s "$output/media.tar.partial"
mv "$output/media.tar.partial" "$output/media.tar"
"${k[@]}" -n ess get cm/ess-haproxy cm/ess-synapse cm/ess-matrix-authentication-service \
  cm/ess-well-known-haproxy ingress/ess-synapse --show-managed-fields -o json > "$output/config-private.json"
python3 - "$config" "$context" "$output" <<'PY'
import base64,json,pathlib,subprocess,sys
k=['kubectl','--kubeconfig',sys.argv[1],'--context',sys.argv[2],'-n','ess'];out=pathlib.Path(sys.argv[3])
for secret, fields in [('ess-synapse',{'SIGNING_KEY':'signing.key','POSTGRES_PASSWORD':'source-db.password'}),
                       ('ess-generated',{'MAS_SYNAPSE_SHARED_SECRET':'mas-homeserver.secret'})]:
 d=json.loads(subprocess.check_output(k+['get','secret',secret,'-o','json']))
 for field, filename in fields.items(): (out/filename).write_bytes(base64.b64decode(d['data'][field]))
# Retain MAS encryption/signing and provisioning credentials for actual rollback,
# discovering only the mounted Secrets of the existing MAS workload.
mas=json.loads(subprocess.check_output(k+['get','deploy','ess-matrix-authentication-service','-o','json']))
names=sorted({v['secret']['secretName'] for v in mas['spec']['template']['spec'].get('volumes',[]) if 'secret' in v})
objects=[json.loads(subprocess.check_output(k+['get','secret',name,'-o','json'])) for name in names]
(out/'mas-secrets-private.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':objects}))
PY
# Confirm source is still frozen after the reads; root also keeps checking during import.
sessions=$("${k[@]}" -n postgres exec deploy/postgres -- psql -X -U postgres -d postgres -Atc \
  "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname IN ('synapse','mas')")
test "$sessions" = 0
"${k[@]}" -n postgres exec deploy/postgres -- psql -X -U postgres -d synapse -Atc \
  "SELECT json_build_object('event_rows',count(*),'stream_high_water',max(stream_ordering)) FROM events" \
  > "$output/source-high-water-after.json"
cmp "$output/source-high-water.json" "$output/source-high-water-after.json"
(cd "$output" && sha256sum synapse.dump mas.dump media.tar signing.key source-db.password \
  mas-homeserver.secret mas-secrets-private.json config-private.json source-high-water.json > SHA256SUMS)
echo "Quiescent source backup captured privately at $output; independent restore verification required"
