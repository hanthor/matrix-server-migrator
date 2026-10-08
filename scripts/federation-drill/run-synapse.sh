#!/usr/bin/env bash
# New disposable pod and emptyDir only. Existing resources are never overwritten.
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-/home/ubuntu/.kube/config-aws-migration}
context=${MIGRATION_CONTEXT:-admin@aws-migration}
namespace=${MIGRATION_NAMESPACE:-spindle-rehearsal}
binary=${1:?exact binary path required}
output=${2:?new output directory required}
pod=${3:?new pod name required}
expected_sha=${4:?explicit expected binary SHA required}
[[ "$expected_sha" =~ ^[a-f0-9]{64}$ ]]
here=$(cd "$(dirname "$0")" && pwd)
k=(kubectl --context "$context" -n "$namespace")
test ! -e "$output"
test "$(sha256sum "$binary" | cut -d' ' -f1)" = "$expected_sha"
if "${k[@]}" get pod "$pod" >/dev/null 2>&1; then
  echo "Pod already exists; refusing to change it" >&2
  exit 1
fi
umask 077
mkdir -p "$output"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=synthetic Synapse drill CA' \
  -keyout "$output/ca.key" -out "$output/ca.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj '/CN=127.0.0.1' \
  -keyout "$output/leaf.key" -out "$output/leaf.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' > "$output/leaf.ext"
openssl x509 -req -in "$output/leaf.csr" -CA "$output/ca.crt" -CAkey "$output/ca.key" \
  -CAcreateserial -days 2 -extfile "$output/leaf.ext" -out "$output/leaf.crt" 2>/dev/null
rm "$output/ca.key"
python3 - "$pod" > "$output/pod.json" <<'PY'
import json, sys
print(json.dumps({
 'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': sys.argv[1], 'labels': {'app': sys.argv[1]}},
 'spec': {'nodeName': 'ip-10-20-1-12', 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
  'enableServiceLinks': False, 'dnsPolicy': 'None', 'dnsConfig': {'nameservers': ['127.0.0.1']},
  'securityContext': {'runAsUser': 10093, 'runAsGroup': 10093, 'fsGroup': 10093,
                      'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
  'containers': [
   {'name': 'witness', 'image': 'oci.element.io/synapse@sha256:d2215c4a0e0bbd304489af228345b31d6857c1a228175471358d3fda187c0d91', 'command': ['sleep', 'infinity'],
    'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '500m', 'memory': '768Mi'}},
    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
    'volumeMounts': [{'name': 'lab', 'mountPath': '/lab'}]},
   {'name': 'candidate', 'image': 'docker.io/library/rust@sha256:652612f07bfbbdfa3af34761c1e435094c00dde4a98036132fca28c7bb2b165c',
    'command': ['sh', '-c', 'while [ ! -f /lab/start ]; do sleep 1; done; /lab/spindle /lab/spindle.toml > /lab/spindle.log 2>&1'],
    'env': [{'name': 'SSL_CERT_FILE', 'value': '/lab/ca.crt'}],
    'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi'}},
    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
    'volumeMounts': [{'name': 'lab', 'mountPath': '/lab'}]},
  ], 'volumes': [{'name': 'lab', 'emptyDir': {'sizeLimit': '512Mi'}}]}}))
PY
python3 - "$pod" > "$output/network-policy.json" <<'PY_POLICY'
import json,sys
print(json.dumps({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':sys.argv[1]},'spec':{'podSelector':{'matchLabels':{'app':sys.argv[1]}},'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[]}}))
PY_POLICY
"${k[@]}" create -f "$output/network-policy.json"
"${k[@]}" create -f "$output/pod.json"
"${k[@]}" wait --for=condition=Ready "pod/$pod" --timeout=180s
"${k[@]}" cp "$binary" "$pod:/lab/spindle" -c witness
for file in ca.crt leaf.crt leaf.key; do
  "${k[@]}" cp "$output/$file" "$pod:/lab/$file" -c witness
done
for file in loopback.py synapse-witness.py; do
  "${k[@]}" cp "$here/$file" "$pod:/lab/$file" -c witness
done
"${k[@]}" exec "$pod" -c witness -- chmod 700 /lab/spindle
verdict=0
"${k[@]}" exec "$pod" -c witness -- python3 /lab/synapse-witness.py --binary-sha "$expected_sha" > "$output/run.json" 2> "$output/runner.log" || verdict=$?
# Preserve this run's private evidence before stopping its own disposable pod.
"${k[@]}" exec "$pod" -c witness -- tar -C /lab -cf - . > "$output/pod-artifacts.tar"
"${k[@]}" exec "$pod" -c witness -- cat /lab/result.json > "$output/result.json" || verdict=1
"${k[@]}" get pod "$pod" -o json > "$output/pod-final.json"
"${k[@]}" delete pod "$pod" --wait=true --timeout=120s
"${k[@]}" delete networkpolicy "$pod" --wait=true
cat "$output/result.json"
exit "$verdict"
