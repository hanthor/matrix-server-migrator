import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cold_target_transport', ROOT / 'scripts/target-restore/cold-target.py')
cold = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as candidate_directory:
    candidate = Path(candidate_directory)
    (candidate / 'identity.json').write_text(json.dumps({
        'revision': '1' * 40, 'binary_sha256': '2' * 64, 'binary_basename': 'spindle-1111111',
        'target_pvc_uid': '00000000-0000-0000-0000-000000000000', 'scope': 'joined',
    }))
    with patch.dict(os.environ, {'SPINDLE_CANDIDATE_PACK': str(candidate)}):
        spec.loader.exec_module(cold)


def command(ns, *args):
    return ['kubectl', '-n', ns, *args]


class ColdTargetTransport(unittest.TestCase):
    def test_stdin_channel_only_when_input_is_requested(self):
        with patch.object(cold.subprocess, 'check_output', return_value=b'{}') as execute:
            cold.remote_exec(command, 'owned', ['cat', '/target/import/report.json'])
            self.assertNotIn('-i', execute.call_args.args[0])
            cold.remote_exec(command, 'owned', ['cat'], data=b'')
            self.assertIn('-i', execute.call_args.args[0])
            self.assertEqual(execute.call_args.kwargs['input'], b'')

    def test_exit_zero_truncated_json_retries_until_parsed(self):
        with patch.object(cold.subprocess, 'check_output', side_effect=[b'{"rooms":', b'{"rooms":{"synthetic":{}}}']) as execute:
            report = cold.readonly_remote_json(command, 'owned', ['cat', '/target/import/report.json'])
            self.assertEqual(report, {'rooms': {'synthetic': {}}})
            self.assertEqual(execute.call_count, 2)
            self.assertTrue(all('-i' not in call.args[0] for call in execute.call_args_list))
            self.assertTrue(all(call.kwargs['timeout'] == 300 for call in execute.call_args_list))

    def test_transport_failure_retries_read_only(self):
        fault = subprocess.CalledProcessError(1, ['kubectl'], output=b'{')
        with patch.object(cold.subprocess, 'check_output', side_effect=[fault, b'{"cold":true}']) as execute:
            self.assertEqual(cold.readonly_remote_json(command, 'owned', ['cat', '/target/metadata/identity.json']), {'cold': True})
            self.assertEqual(execute.call_count, 2)

    def test_invalid_utf8_truncation_retries(self):
        with patch.object(cold.subprocess, 'check_output', side_effect=[b'{"path":"\xc3', b'{}']) as execute:
            self.assertEqual(cold.readonly_remote_json(command, 'owned', ['cat', '/target/metadata/identity.json']), {})
            self.assertEqual(execute.call_count, 2)

    def test_timeout_retries_are_bounded(self):
        fault = subprocess.TimeoutExpired(['kubectl'], 300)
        with patch.object(cold.subprocess, 'check_output', side_effect=[fault] * 3) as execute:
            with self.assertRaises(subprocess.TimeoutExpired):
                cold.readonly_remote_json(command, 'owned', ['python3', '-c', cold.CAPACITY_READ])
            self.assertEqual(execute.call_count, 3)

    def test_parse_failure_exhaustion_stays_fatal(self):
        with patch.object(cold.subprocess, 'check_output', return_value=b'{"rooms":') as execute:
            with self.assertRaises(json.JSONDecodeError):
                cold.readonly_remote_json(command, 'owned', ['cat', '/target/import/report.json'])
            self.assertEqual(execute.call_count, 3)

    def test_mutating_remote_exec_never_retries(self):
        fault = subprocess.CalledProcessError(1, ['kubectl'])
        with patch.object(cold.subprocess, 'check_output', side_effect=fault) as execute:
            with self.assertRaises(subprocess.CalledProcessError):
                cold.remote_exec(command, 'owned', ['python3', '-c', 'write_owned_metadata()'], data=b'payload')
            self.assertEqual(execute.call_count, 1)

    def test_mutators_cannot_enter_read_only_retry_path(self):
        with patch.object(cold.subprocess, 'check_output') as execute:
            with self.assertRaises(ValueError):
                cold.readonly_remote_json(command, 'owned', ['python3', '-c', 'write_owned_metadata()'])
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
