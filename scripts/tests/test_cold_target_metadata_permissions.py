"""The exporter's metadata-permissions init container must chmod before chown.

After chown to UID 10091 the container's root process no longer owns
/metadata, and without CAP_FOWNER its chmod fails with EPERM.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/target-restore/cold-target.py'
IDENTITY = {
    'revision': '1' * 40, 'binary_sha256': '2' * 64, 'binary_basename': 'spindle-1111111',
    'target_pvc_uid': '00000000-0000-0000-0000-000000000000', 'scope': 'joined',
}
IMPORT = {'items': [{'kind': 'Deployment', 'spec': {'template': {'spec': {
    'initContainers': [{'name': 'render-config', 'command': ['sh', '-c', 'true']}],
    'containers': [{'name': 'spindle', 'volumeMounts': []}],
    'volumes': [{'name': name} for name in ('source-media', 'data', 'artifact')]
               + [{'name': 'source-signing-key', 'secret': {'items': []}}],
    'securityContext': {},
}}}}]}


def load_cold(pack):
    spec = importlib.util.spec_from_file_location('cold_target_metadata_permissions', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'SPINDLE_CANDIDATE_PACK': str(pack)}):
        spec.loader.exec_module(module)
    return module


class MetadataPermissionsOrder(unittest.TestCase):
    def test_generated_exporter_init_container_runs_chmod_before_chown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'pack'
            pack.mkdir()
            (pack / 'identity.json').write_text(json.dumps(IDENTITY))
            (pack / 'import.json').write_text(json.dumps(IMPORT))
            cold = load_cold(pack)
            output = root / 'cold'
            with contextlib.redirect_stdout(io.StringIO()):
                cold.manifests(output)
            exporter = json.loads((output / 'exporter.json').read_text())
            init = next(c for c in exporter['spec']['initContainers']
                        if c['name'] == 'metadata-permissions')
            self.assertEqual(init['command'][:2], ['python3', '-c'])
            state = {'owner': 0, 'mode': 0o755}

            def chown(path, uid, gid):
                state['owner'] = uid

            def chmod(path, mode):
                # Root without CAP_FOWNER may only change modes of files it still owns.
                if state['owner'] != 0:
                    raise PermissionError(1, 'Operation not permitted', path)
                state['mode'] = mode

            with patch.multiple(os, chown=chown, chmod=chmod):
                exec(init['command'][2], {})
            self.assertEqual(state, {'owner': 10091, 'mode': 0o700})


if __name__ == '__main__':
    unittest.main()
