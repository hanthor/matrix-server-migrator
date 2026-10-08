"""Candidate rehearsal paths must not collide with previous cold proofs."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'finish-reilly-rehearsal.py'


def load(tag):
    spec = importlib.util.spec_from_file_location('finish_identity', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'MIGRATOR_REHEARSAL_TAG': tag}):
        spec.loader.exec_module(module)
    return module


class RehearsalIdentityTests(unittest.TestCase):
    def test_default_preserves_existing_worker_paths(self):
        module = load('20261006')
        self.assertEqual(module.OUT.name, 'reilly-2026-10-06')
        self.assertEqual(module.RESTORE_CONFIG, '/work/reilly-restore.toml')
        self.assertEqual(module.RESTORE_REPORT, '/work/reilly-restored-report.json')

    def test_new_identity_isolates_every_cold_proof_path(self):
        previous = load('20261006')
        candidate = load('seeded-500d2e1-20261007')
        for attribute in ('OUT', 'RESTORE', 'REMOTE_ARCHIVE', 'EXTRACTED',
                          'RESTORE_CONFIG', 'RESTORE_REPORT', 'SERVER_CONFIG',
                          'SERVER_LOG', 'SERVER_PID'):
            with self.subTest(attribute=attribute):
                self.assertNotEqual(getattr(previous, attribute), getattr(candidate, attribute))
                self.assertIn(candidate.TAG, str(getattr(candidate, attribute)))

    def test_untrusted_tag_cannot_escape_owned_paths_or_enter_shell_syntax(self):
        for tag in ('', '../outside', 'a/b', 'a;id', 'a$(id)', 'a b', 'a' * 65):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                load(tag)


if __name__ == '__main__':
    unittest.main()
