#!/usr/bin/env python3
"""Read-only production inventory and guarded HAProxy switch/rollback preparation."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    base = ['kubectl', '--kubeconfig', args.kubeconfig, '--context', args.context]

    def get(namespace, *resource):
        return json.loads(subprocess.check_output(base + ['-n', namespace, 'get', *resource, '-o', 'json']))

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, indent=2) + '\n')

    workloads = get('ess', 'deploy,sts')['items']
    inventory = {'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'context': args.context, 'namespace': 'ess', 'production_changed': False,
                 'workloads': [], 'routes': [], 'media': [], 'database': {
                     'host': 'postgres.postgres.svc.cluster.local', 'port': 5432,
                     'user': 'synapse_user', 'dbname': 'synapse', 'sslmode': 'prefer'}}
    for workload in workloads:
        if workload['metadata']['name'].startswith(('ess-synapse', 'ess-haproxy', 'ess-matrix-authentication', 'ess-spindle')):
            pod = workload['spec']['template']['spec']
            inventory['workloads'].append({
                'kind': workload['kind'], 'name': workload['metadata']['name'],
                'uid': workload['metadata']['uid'], 'replicas': workload['spec'].get('replicas'),
                'ready': workload.get('status', {}).get('readyReplicas', 0),
                'images': [c['image'] for c in pod['containers']],
                'secret_refs': sorted({v['secret']['secretName'] for v in pod.get('volumes', []) if 'secret' in v}),
                'config_refs': sorted({v['configMap']['name'] for v in pod.get('volumes', []) if 'configMap' in v}),
                'pvcs': sorted({v['persistentVolumeClaim']['claimName'] for v in pod.get('volumes', []) if 'persistentVolumeClaim' in v}),
            })
    ingress = get('ess', 'ingress', 'ess-synapse')
    inventory['ingress_uid'] = ingress['metadata']['uid']
    for rule in ingress['spec']['rules']:
        for path in rule['http']['paths']:
            inventory['routes'].append({'host': rule['host'], 'path': path['path'], 'backend': path['backend']})
    pvc = get('ess', 'pvc', 'synapse-media-preseed')
    inventory['media'].append({'name': pvc['metadata']['name'], 'uid': pvc['metadata']['uid'],
                              'volume': pvc['spec']['volumeName'], 'capacity': pvc['status']['capacity']})
    well_known = get('ess', 'cm', 'ess-well-known-haproxy')
    inventory['well_known'] = {key: json.loads(value) for key, value in well_known['data'].items()}
    service = get('ess', 'svc', 'ess-synapse')
    inventory['front_service'] = {'selector': service['spec']['selector'], 'ports': service['spec']['ports']}
    cm = get('ess', 'cm', 'ess-haproxy')
    original = cm['data']['haproxy.cfg']
    required = {'synapse-main', 'synapse-main-failover', 'synapse-sliding-sync'}
    counts = {name: {'servers': 0, 'health_paths': 0} for name in required}
    backend, updated = None, []
    for line in original.splitlines(keepends=True):
        match = re.match(r'^backend\s+(\S+)', line)
        if match:
            backend = match[1]
        elif re.match(r'^(frontend|listen|global|defaults|resolvers)\b', line):
            backend = None
        if backend in required:
            if re.match(r'\s*server-template\s+', line):
                old = ('_synapse-http._tcp.ess-synapse-sliding-sync.ess.svc.cluster.local.'
                       if backend == 'synapse-sliding-sync' else
                       '_synapse-http._tcp.ess-synapse-main.ess.svc.cluster.local.')
                if old not in line:
                    raise SystemExit('Source backend changed; review fresh topology')
                line = line.replace(old, '_synapse-http._tcp.ess-spindle.ess.svc.cluster.local.')
                counts[backend]['servers'] += 1
            if re.match(r'\s*http-check send\b', line):
                if 'meth GET uri /health' not in line:
                    raise SystemExit('Unexpected backend health check')
                line = line.replace('meth GET uri /health', 'meth GET uri /ready')
                counts[backend]['health_paths'] += 1
        updated.append(line)
    if any(value != {'servers': 1, 'health_paths': 1} for value in counts.values()):
        raise SystemExit('Expected exactly three backend templates and health checks')
    candidate = ''.join(updated)
    uid = cm['metadata']['uid']
    def patch(before, after):
        return [{'op': 'test', 'path': '/metadata/uid', 'value': uid},
                {'op': 'test', 'path': '/data/haproxy.cfg', 'value': before},
                {'op': 'replace', 'path': '/data/haproxy.cfg', 'value': after}]
    save('haproxy-switch.json', patch(original, candidate))
    save('haproxy-rollback.json', patch(candidate, original))
    (args.output / 'haproxy-before.cfg').write_text(original)
    (args.output / 'haproxy-candidate.cfg').write_text(candidate)
    inventory['haproxy'] = {'uid': uid, 'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
                            'after_sha256': hashlib.sha256(candidate.encode()).hexdigest(), 'changes': counts}
    save('topology.json', inventory)
    save('target-service.json', {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'ess-spindle', 'namespace': 'ess'},
                                'spec': {'selector': {'app.kubernetes.io/instance': 'ess-spindle'},
                                         'ports': [{'name': 'synapse-http', 'port': 8008, 'targetPort': 8008},
                                                   {'name': 'synapse-health', 'port': 8080, 'targetPort': 8008}]}})
    print(json.dumps({'production_changed': False, 'output': str(args.output), 'haproxy': inventory['haproxy']}))


if __name__ == '__main__':
    os.umask(0o077)
    main()
