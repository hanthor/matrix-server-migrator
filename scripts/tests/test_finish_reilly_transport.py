"""Fault-injected checkpoint transport tests; no cluster access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'finish-reilly-rehearsal.py'
spec = importlib.util.spec_from_file_location('finish_transport', SCRIPT)
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)
VALID = json.dumps({'rooms': {'example': {}}, 'phases_done': []}).encode()


class TransportTests(unittest.TestCase):
    def test_stdin_is_requested_only_when_payload_is_present(self):
        for data, interactive in [(None, False), (b'', True), (b'payload', True)]:
            with self.subTest(data=data), patch.object(finish.subprocess, 'run') as run:
                run.return_value.stdout = b'ok'
                finish.remote(['cat', '/example'], data=data, timeout=17)
                command = run.call_args.args[0]
                self.assertEqual('-i' in command, interactive)
                self.assertEqual(run.call_args.kwargs['input'], data)
                self.assertEqual(run.call_args.kwargs['timeout'], 17)

    def test_exit_zero_truncation_retries_without_publishing_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, 'OUT', Path(directory)):
            path = Path(directory, 'report.json'); path.write_bytes(b'previous')
            def responses(*args, **kwargs):
                if responses.calls == 0:
                    responses.calls += 1
                    return VALID[:10]
                self.assertEqual(path.read_bytes(), b'previous')
                return VALID
            responses.calls = 0
            with patch.object(finish, 'remote', side_effect=responses) as remote:
                report = finish.fetch_report('report.json')
            self.assertEqual(remote.call_count, 2)
            self.assertEqual(report['rooms'], {'example': {}})
            self.assertEqual(path.read_bytes(), VALID)

    def test_exit_one_retry_then_complete_report(self):
        failure = subprocess.CalledProcessError(1, ['kubectl'], output=VALID[:10])
        with tempfile.TemporaryDirectory() as directory, patch.object(finish, 'OUT', Path(directory)), patch.object(finish, 'remote', side_effect=[failure, VALID]) as remote:
            finish.fetch_report('report.json')
            self.assertEqual(remote.call_count, 2)
            self.assertEqual(remote.call_args.kwargs['timeout'], 60)

    def test_exhaustion_is_fatal_and_preserves_existing_artifact(self):
        for failure in [b'{', subprocess.CalledProcessError(1, ['kubectl'])]:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory, patch.object(finish, 'OUT', Path(directory)):
                path = Path(directory, 'report.json'); path.write_bytes(b'previous')
                results = [failure] * 3
                with patch.object(finish, 'remote', side_effect=results) as remote:
                    with self.assertRaises((json.JSONDecodeError, subprocess.CalledProcessError)):
                        finish.fetch_report('report.json')
                self.assertEqual(remote.call_count, 3)
                self.assertEqual(path.read_bytes(), b'previous')

    def test_refresh_hash_guard_rejects_truncated_stdin_before_rename(self):
        for truncated in [False, True]:
            with self.subTest(truncated=truncated), tempfile.TemporaryDirectory() as directory:
                original = Path(directory, 'report.json'); original.write_bytes(b'original')
                with patch.object(finish, 'shell') as shell:
                    finish.write_refreshed_report(json.loads(VALID))
                code, payload = shell.call_args.args
                code = code.replace('/work/', directory + '/')
                received = payload[:10] if truncated else payload
                result = subprocess.run(['sh', '-eu', '-c', code], input=received, capture_output=True)
                self.assertEqual(Path(directory, 'report.json.pre-domain-refresh').read_bytes(), b'original')
                if truncated:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(original.read_bytes(), b'original')
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(original.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
