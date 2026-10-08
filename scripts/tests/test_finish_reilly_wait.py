"""Network-free regressions for disconnected importer watch connections."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'finish-reilly-rehearsal.py'
spec = importlib.util.spec_from_file_location('finish_reilly', SCRIPT)
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class ImporterWaitTests(unittest.TestCase):
    def disconnect(self):
        return subprocess.CalledProcessError(1, ['kubectl', 'exec'])

    def test_disconnect_live_importer_requires_outer_wait_to_continue(self):
        with patch.object(finish, 'remote', side_effect=[self.disconnect(), b'active\n']) as call:
            self.assertTrue(finish.wait_for_importer())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs['timeout'], 30)
        self.assertIn('case "$state" in Z|X)', call.call_args.args[0][3])

    def test_disconnect_exited_importer_allows_outer_coverage_check(self):
        with patch.object(finish, 'remote', side_effect=[self.disconnect(), b'']) as call:
            self.assertFalse(finish.wait_for_importer())
        self.assertEqual(call.call_count, 2)

    def test_failed_fresh_probe_propagates_instead_of_claiming_exit(self):
        failed_probe = subprocess.TimeoutExpired(['kubectl', 'exec'], 30)
        with patch.object(finish, 'remote', side_effect=[self.disconnect(), failed_probe]):
            with self.assertRaises(subprocess.TimeoutExpired) as error:
                finish.wait_for_importer()
        self.assertIs(error.exception, failed_probe)

    def test_local_watch_timeout_also_reconnects(self):
        with patch.object(finish, 'remote', side_effect=[subprocess.TimeoutExpired(['kubectl'], 60), b'active\n']):
            self.assertTrue(finish.wait_for_importer())

    def test_successful_watch_is_bounded_and_does_not_kill_importer(self):
        with patch.object(finish, 'remote', return_value=b'') as call:
            finish.wait_for_importer()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['timeout'], 60)
        command = call.call_args.args[0][3]
        self.assertIn('timeout 45 /work/bin/wait-import "$pid"', command)
        self.assertNotIn('kill', command)
        self.assertIn('else sleep 45;', command)


if __name__ == '__main__':
    unittest.main()
