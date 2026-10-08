#!/usr/bin/env python3
"""Persist the reviewed ESS backend/replica overlay in each future Helm render."""
import argparse
import re
import sys

import yaml

WRITERS = {'ess-synapse-main', 'ess-synapse-fed-sender', 'ess-synapse-sliding-sync'}
FRONTS = {'ess-haproxy', 'ess-matrix-authentication-service'}
BACKENDS = {'synapse-main', 'synapse-main-failover', 'synapse-sliding-sync'}


def transform(config):
    current = None
    counts = {name: [0, 0] for name in BACKENDS}
    output = []
    for line in config.splitlines(keepends=True):
        match = re.match(r'^backend\s+(\S+)', line)
        if match:
            current = match[1]
        elif re.match(r'^(frontend|listen|global|defaults|resolvers)\b', line):
            current = None
        if current in BACKENDS:
            if re.match(r'\s*server-template\s+', line):
                source = ('_synapse-http._tcp.ess-synapse-sliding-sync.ess.svc.cluster.local.'
                          if current == 'synapse-sliding-sync' else
                          '_synapse-http._tcp.ess-synapse-main.ess.svc.cluster.local.')
                target = '_synapse-http._tcp.ess-spindle.ess.svc.cluster.local.'
                if source not in line and target not in line:
                    raise ValueError('unexpected backend')
                line = line.replace(source, target)
                counts[current][0] += 1
            if re.match(r'\s*http-check send\b', line):
                if 'meth GET uri /health' not in line and 'meth GET uri /ready' not in line:
                    raise ValueError('unexpected health check')
                line = line.replace('meth GET uri /health', 'meth GET uri /ready')
                counts[current][1] += 1
        output.append(line)
    if any(value != [1, 1] for value in counts.values()):
        raise ValueError('missing backend templates or health checks')
    return ''.join(output)


def render(documents, mode):
    seen = set()
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        kind = doc.get('kind')
        name = doc.get('metadata', {}).get('name')
        namespace = doc.get('metadata', {}).get('namespace', 'ess')
        if namespace != 'ess':
            continue
        if kind == 'ConfigMap' and name == 'ess-haproxy':
            seen.add('config')
            if mode == 'target':
                doc['data']['haproxy.cfg'] = transform(doc['data']['haproxy.cfg'])
        if (kind == 'StatefulSet' and name in WRITERS) or (kind == 'Deployment' and name in FRONTS):
            seen.add(name)
            if mode == 'quiesced' or (mode == 'target' and name in WRITERS):
                doc['spec']['replicas'] = 0
    if seen != {'config'} | WRITERS | FRONTS:
        raise ValueError('rendered ESS profile changed')
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['source', 'quiesced', 'target'], required=True)
    args = parser.parse_args()
    try:
        documents = list(yaml.safe_load_all(sys.stdin))
        output = yaml.safe_dump_all(render(documents, args.mode), sort_keys=False)
    except Exception:
        raise SystemExit('ESS post-render failed; credential contents suppressed') from None
    sys.stdout.write(output)


if __name__ == '__main__':
    main()
