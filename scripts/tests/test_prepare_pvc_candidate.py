"""A new candidate must never inherit passed proofs from its base pack."""
import importlib.util
import hashlib
import contextlib
import io
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/production-cutover/prepare-pvc-candidate.py'
spec = importlib.util.spec_from_file_location('prepare_pvc_candidate', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = ROOT / 'artifacts/production-20261006-pvc-fallback-56db7d0'
REVISION = '1234567890abcdef1234567890abcdef12345678'


class CandidatePackTests(unittest.TestCase):
    def test_duplicate_target_pvc_sources_are_coalesced_without_losing_readonly_mounts(self):
        names = ('prepare.json', 'import.json', 'stage.json', 'runtime-proof-r3.json')
        original_hashes = {name: hashlib.sha256((BASE / name).read_bytes()).hexdigest()
                           for name in names}
        old = json.loads((BASE / 'import.json').read_text())['items'][0]['spec']['template']['spec']
        self.assertEqual(len([v for v in old['volumes']
                              if v.get('persistentVolumeClaim', {}).get('claimName')
                              == 'ess-spindle-data']), 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'spindle'; binary.write_bytes(b'new candidate executable')
            output = root / 'pack'
            module.prepare(BASE, binary, REVISION, output)
            for name in names:
                document = json.loads((output / name).read_text())
                for item in document.get('items', [document]):
                    if item['kind'] in ('Deployment', 'Job'):
                        pod = item['spec']['template']['spec']
                    elif item['kind'] == 'Pod':
                        pod = item['spec']
                    else:
                        continue
                    claims = [v['persistentVolumeClaim']['claimName'] for v in pod['volumes']
                              if 'persistentVolumeClaim' in v]
                    self.assertEqual(len(claims), len(set(claims)))
                    if name in ('prepare.json', 'import.json'):
                        data = next(v for v in pod['volumes'] if v['name'] == 'data')
                        self.assertFalse(data['persistentVolumeClaim'].get('readOnly', False))
                        main = pod['containers'][0]
                        artifact = next(m for m in main['volumeMounts'] if m['mountPath'] == '/artifact')
                        self.assertEqual(artifact['name'], 'data')
                        self.assertTrue(artifact['readOnly'])
                        writable = next(m for m in main['volumeMounts']
                                        if m['mountPath'] == '/var/lib/spindle-data')
                        self.assertFalse(writable.get('readOnly', False))
                    if name == 'import.json':
                        media = next(v for v in pod['volumes'] if v['name'] == 'source-media')
                        self.assertTrue(media['persistentVolumeClaim']['readOnly'])
                        init = next(c for c in pod['initContainers'] if c['name'] == 'prepare-data')
                        self.assertIn('test -z', init['command'][2])
                        self.assertIn('test ! -e /data/import/report.json', init['command'][2])
                    if name == 'runtime-proof-r3.json':
                        target = next(v for v in pod['volumes'] if v['name'] == 'target')
                        self.assertTrue(target['persistentVolumeClaim']['readOnly'])
            self.assertEqual(original_hashes,
                             {name: hashlib.sha256((BASE / name).read_bytes()).hexdigest()
                              for name in names})

    def test_alias_order_and_effective_readonly_are_preserved(self):
        pod = {'volumes': [
            {'name': 'artifact', 'persistentVolumeClaim': {'claimName': 'data', 'readOnly': True}},
            {'name': 'data', 'persistentVolumeClaim': {'claimName': 'data'}}],
            'containers': [{'volumeMounts': [
                {'name': 'artifact', 'mountPath': '/artifact'},
                {'name': 'data', 'mountPath': '/data'}]}]}
        module.coalesce_pvc_aliases(pod)
        self.assertEqual([v['name'] for v in pod['volumes']], ['data'])
        mounts = pod['containers'][0]['volumeMounts']
        self.assertEqual(mounts[0], {'name': 'data', 'mountPath': '/artifact', 'readOnly': True})
        self.assertFalse(mounts[1].get('readOnly', False))

    def test_all_readonly_aliases_remain_readonly(self):
        pod = {'volumes': [
            {'name': name, 'persistentVolumeClaim': {'claimName': 'cold', 'readOnly': True}}
            for name in ('first', 'second')],
            'containers': [{'volumeMounts': [{'name': 'second', 'mountPath': '/cold'}]}]}
        module.coalesce_pvc_aliases(pod)
        self.assertEqual(len(pod['volumes']), 1)
        self.assertTrue(pod['volumes'][0]['persistentVolumeClaim']['readOnly'])
        self.assertTrue(pod['containers'][0]['volumeMounts'][0]['readOnly'])

    def test_cold_manifests_remain_single_claim_mounts_from_repaired_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'spindle'; binary.write_bytes(b'new candidate executable')
            pack = root / 'pack'
            module.prepare(BASE, binary, REVISION, pack)
            cold_script = ROOT / 'scripts/target-restore/cold-target.py'
            cold_spec = importlib.util.spec_from_file_location('cold_pvc_regression', cold_script)
            cold = importlib.util.module_from_spec(cold_spec)
            with patch.dict(os.environ, {'SPINDLE_CANDIDATE_PACK': str(pack)}):
                cold_spec.loader.exec_module(cold)
            output = root / 'cold'
            with contextlib.redirect_stdout(io.StringIO()):
                cold.manifests(output)
            for name in ('exporter.json', 'restore.json'):
                document = json.loads((output / name).read_text())
                for item in document.get('items', [document]):
                    if item['kind'] != 'Pod':
                        continue
                    volumes = [v for v in item['spec']['volumes'] if 'persistentVolumeClaim' in v]
                    claims = [v['persistentVolumeClaim']['claimName'] for v in volumes]
                    self.assertEqual(len(claims), len(set(claims)))
                    target = next(v for v in volumes if v['name'] == 'target')
                    self.assertEqual(target['persistentVolumeClaim']['readOnly'],
                                     name == 'exporter.json')
                    self.assertNotIn('synapse-media-preseed', claims)

    def test_passed_base_proofs_are_reset_and_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'spindle'; binary.write_bytes(b'new candidate executable')
            output = root / 'pack'
            result = module.prepare(BASE, binary, REVISION, output)
            identity = json.loads((output / 'identity.json').read_text())
            for key in ('target_staged', 'runtime_empty_fixture_passed',
                        'rehearsal_gates_passed', 'production_changed'):
                self.assertFalse(identity[key])
            self.assertFalse((output / 'completed-pods').exists())
            self.assertFalse((output / 'runtime-config-proof.json').exists())
            for name in ('prepare.json', 'import.json', 'stage.json', 'runtime-proof-r3.json'):
                text = (output / name).read_text()
                self.assertNotIn('56db7d0', text)
                self.assertIn(result['binary_sha256'], text)
            stage = (output / 'stage.json').read_text()
            self.assertNotIn('36644784', stage)
            self.assertIn('ess-spindle-stage-1234567', stage)
            self.assertIn(str(binary), (output / 'upload.py').read_text())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'spindle'; binary.write_bytes(b'candidate')
            output = root / 'pack'; output.mkdir()
            evidence = output / 'proof.json'; evidence.write_text('keep')
            with self.assertRaises(AssertionError):
                module.prepare(BASE, binary, REVISION, output)
            self.assertEqual(evidence.read_text(), 'keep')

    def test_unpinned_source_cannot_generate_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'spindle'; binary.write_bytes(b'candidate')
            with self.assertRaises(AssertionError):
                module.prepare(BASE, binary, 'HEAD', root / 'pack')
            self.assertFalse((root / 'pack').exists())


if __name__ == '__main__':
    unittest.main()
