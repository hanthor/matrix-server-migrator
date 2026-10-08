#!/usr/bin/env python3
"""Pin reviewed draft resources to an immutable executable image; no cluster writes."""
import argparse
import hashlib
import json
import pathlib
import re
import os

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--draft', type=pathlib.Path, required=True)
p.add_argument('--image', required=True)
p.add_argument('--revision', required=True)
p.add_argument('--binary', type=pathlib.Path, required=True)
p.add_argument('--output', type=pathlib.Path, required=True)
a = p.parse_args()
assert re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', a.image), 'immutable image digest required'
assert re.fullmatch(r'[0-9a-f]{40}', a.revision), 'full commit required'
assert not a.output.exists(), 'new output directory required'
sha = hashlib.file_digest(a.binary.open('rb'), 'sha256').hexdigest()
d = json.loads(a.draft.read_text())
assert d['kind'] == 'List'
assert sorted(x['kind'] for x in d['items']) == sorted(['PersistentVolumeClaim','ConfigMap','Service','Deployment','Job'])
for x in d['items']:
    assert x['metadata']['namespace'] == 'ess'
    annotations = x['metadata'].setdefault('annotations', {})
    annotations['spindle.tunaos.org/review-status'] = 'Pinned preparation; rehearsal/frozen-source execution gates still required'
    annotations['spindle.tunaos.org/revision'] = a.revision
    annotations['spindle.tunaos.org/binary-sha256'] = sha
    if x['kind'] == 'ConfigMap':
        for key in ['runtime-base.toml','import-base.toml']:
            x['data'][key] = x['data'][key].replace('# Draft for the tested final candidate, not applied to production.', '# Pinned candidate configuration; apply only after the required gates.')
    if x['kind'] == 'Deployment':
        assert x['spec']['replicas'] == 0
    if x['kind'] in ['Deployment','Job']:
        spec = x['spec']['template']['spec']
        spec['terminationGracePeriodSeconds'] = 120
        for c in spec.get('initContainers', []) + spec['containers']:
            if c['image'].startswith('ghcr.io/tuna-os/spindle@'):
                c['image'] = a.image
            if c['name'] in ['spindle','import']:
                # Verify the image payload before opening the store, every start.
                args = c.get('args', [])
                c['command'] = ['/bin/sh', '-ec', 'test "$(sha256sum /usr/local/bin/spindle | cut -d " " -f 1)" = ' + sha + '; exec /usr/local/bin/spindle "$@"', 'spindle']
                c['args'] = args
os.umask(0o077)
a.output.mkdir(parents=True, mode=0o700)
for name, items in [('prepare.json',[x for x in d['items'] if x['kind'] != 'Job']), ('import.json',[x for x in d['items'] if x['kind'] == 'Job'])]:
    (a.output/name).write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items},indent=2)+'\n')
(a.output/'identity.json').write_text(json.dumps({'image':a.image,'revision':a.revision,'binary_sha256':sha,'rehearsal_gates_passed':False,'production_changed':False},indent=2)+'\n')
print('Pinned resources prepared privately; no cluster changes')
