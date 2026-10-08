#!/usr/bin/env python3
"""Prepare a fresh private PVC-binary candidate pack. Never writes to Kubernetes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def coalesce_pvc_aliases(pod):
    """Mount each claim once; keep read-only aliases read-only at the mount."""
    groups = {}
    for volume in pod.get('volumes', []):
        if 'persistentVolumeClaim' in volume:
            groups.setdefault(volume['persistentVolumeClaim']['claimName'], []).append(volume)
    removed = set()
    containers = (pod.get('initContainers', []) + pod.get('containers', [])
                  + pod.get('ephemeralContainers', []))
    for aliases in groups.values():
        if len(aliases) < 2:
            continue
        # A read-only volumeSource for a second alias can make the underlying
        # Talos/CSI mount read-only globally, including the intended RW path.
        canonical = next((v for v in aliases
                          if not v['persistentVolumeClaim'].get('readOnly', False)), aliases[0])
        names = {v['name']: v['persistentVolumeClaim'].get('readOnly', False)
                 for v in aliases}
        for container in containers:
            for device in container.get('volumeDevices', []):
                assert device['name'] not in names, 'duplicate block PVC aliases unsupported'
            for mount in container.get('volumeMounts', []):
                if mount['name'] in names:
                    if names[mount['name']]:
                        mount['readOnly'] = True
                    mount['name'] = canonical['name']
        removed.update(v['name'] for v in aliases if v is not canonical)
    pod['volumes'] = [v for v in pod.get('volumes', []) if v['name'] not in removed]


def prepare(base, binary, revision, output):
    assert re.fullmatch(r'[0-9a-f]{40}', revision), 'full source commit required'
    assert not output.exists(), 'fresh output directory required'
    identity = json.loads((base / 'identity.json').read_text())
    old_revision = identity['revision']
    old_sha = identity['binary_sha256']
    old_size = identity['bytes']
    assert re.fullmatch(r'[0-9a-f]{40}', old_revision)
    assert re.fullmatch(r'[0-9a-f]{64}', old_sha)
    assert identity['scope'] == 'joined'
    assert binary.is_file() and not binary.is_symlink()
    with binary.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    size = binary.stat().st_size
    assert size > 0
    short = revision[:7]
    old_short = old_revision[:7]
    assert short != old_short and digest != old_sha, 'new identity required'

    def replace(text):
        for previous, current in ((old_sha, digest), (old_revision, revision),
                                  (old_short, short)):
            text = text.replace(previous, current)
        return re.sub(r'(?<![0-9])' + str(old_size) + r'(?![0-9])', str(size), text)

    prepared = {}
    for name in ('prepare.json', 'import.json', 'stage.json', 'runtime-proof-r3.json'):
        text = replace((base / name).read_text())
        document = json.loads(text)
        items = document.get('items', [document])
        for item in items:
            assert item['metadata']['namespace'] == 'ess'
            if item['kind'] == 'Deployment':
                assert item['spec']['replicas'] == 0
            if item['kind'] in ('Deployment', 'Job'):
                coalesce_pvc_aliases(item['spec']['template']['spec'])
            elif item['kind'] == 'Pod':
                coalesce_pvc_aliases(item['spec'])
        assert old_sha not in text and old_short not in text
        prepared[name] = json.dumps(document, indent=2) + '\n'
    uploader = replace((base / 'upload.py').read_text())
    uploader = re.sub(r'^BINARY = .*$', 'BINARY = Path(' + repr(str(binary.resolve())) + ')',
                      uploader, flags=re.MULTILINE)
    assert old_sha not in uploader and old_short not in uploader
    compile(uploader, str(output / 'upload.py'), 'exec')
    prepared['upload.py'] = uploader
    prepared['identity.json'] = json.dumps({
        'revision': revision, 'binary_sha256': digest, 'bytes': size,
        'scope': 'joined', 'base_pack': str(base.resolve()),
        'binary_basename': 'spindle-' + short,
        'target_pvc_uid': identity['target_pvc_uid'],
        'rehearsal_gates_passed': False, 'production_changed': False,
        'target_staged': False, 'target_deployment_replicas': 0,
        'runtime_empty_fixture_passed': False,
        'source_writers_routes_data_changed': False,
    }, indent=2) + '\n'
    prepared['README.md'] = (
        f'Prepared joined-only candidate {revision}, binary SHA256 {digest}.\n'
        'No cluster writes or runtime proofs performed by this generator.\n'
        'Stage, empty-runtime and rehearsal proofs must be generated for this exact identity.\n'
        'Prior candidate proof results are deliberately excluded.\n')
    output.mkdir(mode=0o700)
    for name, contents in prepared.items():
        (output / name).write_text(contents)
    return {'revision': revision, 'binary_sha256': digest, 'bytes': size,
            'output': str(output), 'production_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-pack', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(prepare(arguments.base_pack, arguments.binary,
                             arguments.revision, arguments.output)))
