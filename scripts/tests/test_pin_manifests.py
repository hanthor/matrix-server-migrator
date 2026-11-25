"""pin-manifests.py must only emit pinned, zero-replica resources and refuse unsafe input."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/production-cutover/pin-manifests.py'
DIGEST = 'a' * 64
IMAGE = 'ghcr.io/tuna-os/spindle@sha256:' + DIGEST
OTHER_IMAGE = 'ghcr.io/tuna-os/spindle@sha256:' + 'b' * 64
REVISION = '0123456789abcdef0123456789abcdef01234567'
BINARY = b'candidate executable bytes'
DRAFT_MARKER = '# Draft for the tested final candidate, not applied to production.'
PINNED_MARKER = '# Pinned candidate configuration; apply only after the required gates.'


def draft():
    def meta(name):
        return {'name': name, 'namespace': 'ess'}

    return {
        'kind': 'List',
        'items': [
            {'kind': 'PersistentVolumeClaim', 'metadata': meta('data')},
            {'kind': 'ConfigMap', 'metadata': meta('config'),
             'data': {'runtime-base.toml': DRAFT_MARKER + '\nruntime = 1\n',
                      'import-base.toml': DRAFT_MARKER + '\nimport = 1\n',
                      'other.toml': DRAFT_MARKER + '\n'}},
            {'kind': 'Service', 'metadata': meta('svc')},
            {'kind': 'Deployment', 'metadata': meta('spindle'),
             'spec': {'replicas': 0, 'template': {'spec': {
                 'containers': [{'name': 'spindle',
                                 'image': 'ghcr.io/tuna-os/spindle@sha256:' + 'c' * 64,
                                 'args': ['--serve']}]}}}},
            {'kind': 'Job', 'metadata': meta('import'),
             'spec': {'template': {'spec': {
                 'initContainers': [{'name': 'prep', 'image': 'busybox:1.36'}],
                 'containers': [{'name': 'import',
                                 'image': 'ghcr.io/tuna-os/spindle@sha256:' + 'd' * 64,
                                 'args': ['import', '--yes']},
                                {'name': 'sidecar',
                                 'image': 'ghcr.io/tuna-os/spindle@sha256:' + 'e' * 64,
                                 'args': ['x']}]}}}},
        ],
    }


class PinManifestsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.binary = self.root / 'spindle'
        self.binary.write_bytes(BINARY)
        self.sha = hashlib.sha256(BINARY).hexdigest()
        self.output = self.root / 'out'

    def write_draft(self, document):
        path = self.root / 'draft.json'
        path.write_text(json.dumps(document))
        return path

    def run_script(self, document=None, image=IMAGE, revision=REVISION, output=None):
        draft_path = self.write_draft(document if document is not None else draft())
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--draft', str(draft_path), '--image', image,
             '--revision', revision, '--binary', str(self.binary),
             '--output', str(output or self.output)],
            capture_output=True, text=True)

    def load(self, name):
        return json.loads((self.output / name).read_text())

    def assertRejected(self, result, fragment):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(fragment, result.stderr)
        self.assertFalse(self.output.exists(), 'a rejected run must not create output')

    def test_splits_job_from_everything_else_and_records_identity(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         'Pinned resources prepared privately; no cluster changes')

        prepare = self.load('prepare.json')
        self.assertEqual(prepare['kind'], 'List')
        self.assertEqual([x['kind'] for x in prepare['items']],
                         ['PersistentVolumeClaim', 'ConfigMap', 'Service', 'Deployment'])
        imports = self.load('import.json')
        self.assertEqual([x['kind'] for x in imports['items']], ['Job'])
        self.assertEqual(self.load('identity.json'), {
            'image': IMAGE, 'revision': REVISION, 'binary_sha256': self.sha,
            'rehearsal_gates_passed': False, 'production_changed': False})

    def test_every_resource_is_annotated_with_revision_and_binary_hash(self):
        self.assertEqual(self.run_script().returncode, 0)
        items = self.load('prepare.json')['items'] + self.load('import.json')['items']
        for item in items:
            annotations = item['metadata']['annotations']
            self.assertEqual(annotations['spindle.tunaos.org/revision'], REVISION)
            self.assertEqual(annotations['spindle.tunaos.org/binary-sha256'], self.sha)
            self.assertIn('rehearsal/frozen-source execution gates still required',
                          annotations['spindle.tunaos.org/review-status'])

    def test_existing_annotations_are_kept(self):
        document = draft()
        document['items'][0]['metadata']['annotations'] = {'keep': 'me'}
        self.assertEqual(self.run_script(document).returncode, 0)
        annotations = self.load('prepare.json')['items'][0]['metadata']['annotations']
        self.assertEqual(annotations['keep'], 'me')
        self.assertIn('spindle.tunaos.org/revision', annotations)

    def test_configmap_draft_marker_replaced_only_in_base_toml_keys(self):
        self.assertEqual(self.run_script().returncode, 0)
        data = next(x for x in self.load('prepare.json')['items'] if x['kind'] == 'ConfigMap')['data']
        self.assertEqual(data['runtime-base.toml'], PINNED_MARKER + '\nruntime = 1\n')
        self.assertEqual(data['import-base.toml'], PINNED_MARKER + '\nimport = 1\n')
        self.assertEqual(data['other.toml'], DRAFT_MARKER + '\n')

    def test_spindle_images_are_swapped_and_other_images_untouched(self):
        self.assertEqual(self.run_script().returncode, 0)
        job = self.load('import.json')['items'][0]['spec']['template']['spec']
        by_name = {c['name']: c for c in job['initContainers'] + job['containers']}
        self.assertEqual(by_name['import']['image'], IMAGE)
        self.assertEqual(by_name['sidecar']['image'], IMAGE)
        self.assertEqual(by_name['prep']['image'], 'busybox:1.36')
        deployment = next(x for x in self.load('prepare.json')['items'] if x['kind'] == 'Deployment')
        self.assertEqual(deployment['spec']['template']['spec']['containers'][0]['image'], IMAGE)

    def test_spindle_and_import_containers_verify_binary_hash_before_exec(self):
        self.assertEqual(self.run_script().returncode, 0)
        deployment = next(x for x in self.load('prepare.json')['items'] if x['kind'] == 'Deployment')
        job = self.load('import.json')['items'][0]
        wrapped = [deployment['spec']['template']['spec']['containers'][0],
                   job['spec']['template']['spec']['containers'][0]]
        for container in wrapped:
            self.assertEqual(container['command'][:2], ['/bin/sh', '-ec'])
            self.assertIn('= ' + self.sha + ';', container['command'][2])
            self.assertIn('exec /usr/local/bin/spindle "$@"', container['command'][2])
            self.assertEqual(container['command'][3], 'spindle')
        # Original arguments survive so the wrapper's "$@" still sees them.
        self.assertEqual(wrapped[0]['args'], ['--serve'])
        self.assertEqual(wrapped[1]['args'], ['import', '--yes'])
        sidecar = job['spec']['template']['spec']['containers'][1]
        self.assertNotIn('command', sidecar)

    def test_grace_period_set_on_deployment_and_job_pods(self):
        self.assertEqual(self.run_script().returncode, 0)
        deployment = next(x for x in self.load('prepare.json')['items'] if x['kind'] == 'Deployment')
        job = self.load('import.json')['items'][0]
        for item in (deployment, job):
            self.assertEqual(item['spec']['template']['spec']['terminationGracePeriodSeconds'], 120)

    def test_output_directory_is_private(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for name in ('prepare.json', 'import.json', 'identity.json'):
            self.assertEqual(stat.S_IMODE((self.output / name).stat().st_mode) & 0o077, 0, name)

    def test_rejects_image_without_digest(self):
        for image in ('ghcr.io/tuna-os/spindle:latest',
                      'ghcr.io/tuna-os/spindle@sha256:' + 'a' * 63,
                      'ghcr.io/tuna-os/spindle@sha256:' + 'A' * 64,
                      'ghcr.io/tuna-os/spindle@sha256:' + 'a' * 65 + ' x'):
            with self.subTest(image=image):
                self.assertRejected(self.run_script(image=image), 'immutable image digest required')

    def test_rejects_short_or_uppercase_revision(self):
        for revision in ('abc1234', REVISION[:-1], REVISION.upper(), REVISION + '0'):
            with self.subTest(revision=revision):
                self.assertRejected(self.run_script(revision=revision), 'full commit required')

    def test_rejects_existing_output_without_touching_it(self):
        self.output.mkdir()
        (self.output / 'marker').write_text('keep')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('new output directory required', result.stderr)
        self.assertEqual([p.name for p in self.output.iterdir()], ['marker'])

    def test_rejects_draft_with_unexpected_resource_set(self):
        missing = draft()
        missing['items'] = [x for x in missing['items'] if x['kind'] != 'Service']
        self.assertRejected(self.run_script(missing), 'AssertionError')

        extra = draft()
        extra['items'].append({'kind': 'Secret', 'metadata': {'name': 's', 'namespace': 'ess'}})
        self.assertRejected(self.run_script(extra), 'AssertionError')

        not_list = draft()
        not_list['kind'] = 'Deployment'
        self.assertRejected(self.run_script(not_list), 'AssertionError')

    def test_rejects_resource_outside_ess_namespace(self):
        document = draft()
        document['items'][2]['metadata']['namespace'] = 'default'
        self.assertRejected(self.run_script(document), 'AssertionError')

    def test_rejects_running_deployment(self):
        document = draft()
        deployment = next(x for x in document['items'] if x['kind'] == 'Deployment')
        deployment['spec']['replicas'] = 1
        self.assertRejected(self.run_script(document), 'AssertionError')


if __name__ == '__main__':
    unittest.main()
