"""Source marker collection must stay read-only, bounded and refuse partial output."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'source-restore/markers.py'
spec = importlib.util.spec_from_file_location('source_restore_markers', SCRIPT)
markers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(markers)


class FakeProcess:
    """Stand-in for subprocess.Popen: yields `output` from stdout in the requested chunks."""

    def __init__(self, output=b'', returncode=0):
        self.stdin = io.BytesIO()
        self.stdin_closed_with = None
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.terminated = False

        def close_stdin(real_close=self.stdin.close):
            self.stdin_closed_with = self.stdin.getvalue()
            real_close()
        self.stdin.close = close_stdin

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode


def collector(log_directory=None):
    return markers.Collector('/kube/config', 'ctx', 'ns', 'pod-0', log_directory)


class QuoteIdentTests(unittest.TestCase):
    def test_plain_identifier_is_double_quoted(self):
        self.assertEqual(markers.quote_ident('events'), '"events"')

    def test_embedded_quotes_are_doubled_so_the_name_stays_one_identifier(self):
        self.assertEqual(markers.quote_ident('a"; DROP TABLE users; --'),
                         '"a""; DROP TABLE users; --"')


class ScopeSqlTests(unittest.TestCase):
    def test_default_scope_adds_no_policy_keys_and_no_ledger(self):
        sql = markers.scope_sql()
        self.assertIn("':' || 'reilly.asia'", sql)
        self.assertNotIn('scope_server_name', sql)
        self.assertNotIn('preserve_local_history', sql)
        self.assertNotIn('local_current_membership', sql)

    def test_non_default_server_is_recorded_in_the_policy(self):
        sql = markers.scope_sql('example.org')
        self.assertIn("'scope_server_name','example.org'", sql)
        self.assertNotIn('preserve_local_history', sql)

    def test_preserving_local_history_unions_the_membership_ledger(self):
        sql = markers.scope_sql('example.org', preserve_local_history=True)
        self.assertIn('UNION SELECT room_id FROM local_current_membership', sql)
        self.assertIn("'preserve_local_history',true, 'scope_server_name','example.org'", sql)

    def test_preserving_local_history_records_the_default_server_too(self):
        sql = markers.scope_sql(preserve_local_history=True)
        self.assertIn("'scope_server_name','reilly.asia'", sql)

    def test_single_quotes_in_the_server_name_are_escaped(self):
        sql = markers.scope_sql("o'brien.example")
        self.assertIn("'o''brien.example'", sql)
        self.assertNotIn("'o'brien", sql)


class PrivateLogTests(unittest.TestCase):
    def test_without_a_directory_the_log_is_an_anonymous_temp_file(self):
        with collector().private_log('SELECT 1') as log:
            log.write(b'x')

    def test_log_directory_is_private_and_files_are_exclusive_and_numbered(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent) / 'nested' / 'logs'
            c = collector(directory)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            c.private_log('SELECT 1').close()
            c.private_log('SELECT 2').close()
            digest = hashlib.sha256(b'SELECT 1').hexdigest()[:12]
            first = directory / f'query-0001-{digest}.stderr.log'
            self.assertTrue(first.exists())
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(len(list(directory.iterdir())), 2)

    def test_reusing_a_log_name_is_refused_rather_than_overwritten(self):
        with tempfile.TemporaryDirectory() as parent:
            c = collector(parent)
            c.private_log('SELECT 1').close()
            c.log_counter = 0
            with self.assertRaises(FileExistsError):
                c.private_log('SELECT 1')


class SqlOnceTests(unittest.TestCase):
    def run_once(self, process):
        with patch.object(markers.subprocess, 'Popen', return_value=process) as popen:
            result = collector()._sql_once('synapse', 'SELECT 1;')
        return result, popen

    def test_query_is_sent_read_only_with_utc_and_stop_on_error(self):
        process = FakeProcess(b'{"ok": true}\n')
        result, popen = self.run_once(process)
        self.assertEqual(result, {'ok': True})
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:7], ['kubectl', '--kubeconfig', '/kube/config', '--context', 'ctx', '-n', 'ns'])
        self.assertIn('psql', argv)
        self.assertEqual(argv[argv.index('-d') + 1], 'synapse')
        self.assertIn('ON_ERROR_STOP=1', argv)
        sent = process.stdin_closed_with.decode()
        self.assertTrue(sent.startswith("SET timezone='UTC'; SET datestyle='ISO,YMD'; "
                                        'SET default_transaction_read_only=on; '))
        self.assertTrue(sent.endswith('SELECT 1;'))

    def test_failed_query_reports_no_remote_details(self):
        with self.assertRaisesRegex(RuntimeError, 'query failed; details suppressed'):
            self.run_once(FakeProcess(b'{"ok": true}\n', returncode=1))

    def test_output_cut_before_its_newline_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'ended before its complete output line'):
            self.run_once(FakeProcess(b'{"ok": tr'))

    def test_output_over_eight_mebibytes_terminates_the_query(self):
        process = FakeProcess(b'x' * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(RuntimeError, 'exceeded bounded limit'):
            self.run_once(process)
        self.assertTrue(process.terminated)

    def test_output_that_is_not_json_is_a_decode_error(self):
        with self.assertRaises(json.JSONDecodeError):
            self.run_once(FakeProcess(b'not json\n'))


class SqlRetryTests(unittest.TestCase):
    def test_a_truncated_attempt_is_discarded_and_a_fresh_query_is_used(self):
        c = collector()
        with patch.object(c, '_sql_once',
                          side_effect=[RuntimeError('cut'), json.JSONDecodeError('x', '', 0), {'ok': 1}]) as once:
            self.assertEqual(c.sql('db', 'SELECT 1'), {'ok': 1})
        self.assertEqual(once.call_count, 3)

    def test_the_third_failure_is_raised(self):
        c = collector()
        with patch.object(c, '_sql_once', side_effect=BrokenPipeError()) as once:
            with self.assertRaises(BrokenPipeError):
                c.sql('db', 'SELECT 1')
        self.assertEqual(once.call_count, 3)

    def test_unexpected_errors_are_not_retried(self):
        c = collector()
        with patch.object(c, '_sql_once', side_effect=KeyError('boom')) as once:
            with self.assertRaises(KeyError):
                c.sql('db', 'SELECT 1')
        self.assertEqual(once.call_count, 1)


def digest_lines(count):
    # The collector asserts every row is a 64 character hex digest plus newline.
    return b''.join(hashlib.sha256(str(i).encode()).hexdigest().encode() + b'\n' for i in range(count))


class DigestTableTests(unittest.TestCase):
    def run_digest(self, process, expected_rows=None):
        with patch.object(markers.subprocess, 'Popen', return_value=process) as popen:
            result = collector().digest_table('synapse', 'events', expected_rows)
        return result, popen

    def test_digest_is_taken_over_the_whole_stream_and_rows_are_counted(self):
        output = digest_lines(3)
        result, popen = self.run_digest(FakeProcess(output), expected_rows=3)
        self.assertEqual(result, {'rows': 3, 'sorted_row_sha256_stream_sha256': hashlib.sha256(output).hexdigest()})
        sent = popen.return_value.stdin_closed_with.decode()
        self.assertIn('default_transaction_read_only=on', sent)
        self.assertIn('FROM public."events" row_value', sent)
        self.assertIn('COLLATE "C"', sent)

    def test_an_empty_table_digests_to_zero_rows(self):
        result, _ = self.run_digest(FakeProcess(b''), expected_rows=0)
        self.assertEqual(result, {'rows': 0, 'sorted_row_sha256_stream_sha256': hashlib.sha256(b'').hexdigest()})

    def test_table_name_is_quoted_in_the_copy_query(self):
        with patch.object(markers.subprocess, 'Popen', return_value=FakeProcess(b'')) as popen:
            collector().digest_table('synapse', 'x"y')
        self.assertIn('public."x""y" row_value', popen.return_value.stdin_closed_with.decode())

    def test_a_row_count_that_differs_from_the_frozen_count_is_refused_after_three_attempts(self):
        processes = [FakeProcess(digest_lines(2)) for _ in range(3)]
        with patch.object(markers.subprocess, 'Popen', side_effect=processes) as popen:
            with self.assertRaisesRegex(RuntimeError, 'differs from frozen table count'):
                collector().digest_table('synapse', 'events', expected_rows=3)
        self.assertEqual(popen.call_count, 3)

    def test_a_retry_that_matches_the_frozen_count_succeeds(self):
        processes = [FakeProcess(digest_lines(2)), FakeProcess(digest_lines(3))]
        with patch.object(markers.subprocess, 'Popen', side_effect=processes):
            result = collector().digest_table('synapse', 'events', expected_rows=3)
        self.assertEqual(result['rows'], 3)

    def test_a_line_that_is_not_a_sha256_digest_is_refused(self):
        with patch.object(markers.subprocess, 'Popen',
                          side_effect=[FakeProcess(b'short\n') for _ in range(3)]):
            with self.assertRaisesRegex(RuntimeError, 'bounded domain digest failed'):
                collector().digest_table('synapse', 'events')

    def test_a_failed_exit_status_is_refused_even_with_complete_lines(self):
        with patch.object(markers.subprocess, 'Popen',
                          side_effect=[FakeProcess(digest_lines(1), returncode=1) for _ in range(3)]):
            with self.assertRaisesRegex(RuntimeError, 'bounded domain digest failed'):
                collector().digest_table('synapse', 'events')


class CollectTests(unittest.TestCase):
    SYNAPSE_TABLES = sorted(markers.REQUIRED | {'pushers', 'unrelated_table'})

    def make(self, tables, scope=None):
        c = collector()
        queries = []

        def fake_sql(database, text):
            queries.append(text)
            if 'retained AS' in text:
                return scope if scope is not None else {'event_rows': 7}
            if 'pg_database_size' in text:
                return {'database_bytes': 1}
            if 'FROM pg_tables' in text:
                return tables
            if 'information_schema.columns' in text:
                return {'columns': [], 'constraints': [], 'indexes': []}
            if 'pg_sequences' in text:
                return []
            if 'to_json(count(*))' in text:
                return 5
            raise AssertionError(f'unexpected query: {text}')

        c.sql = fake_sql
        c.digest_table = MagicMock(side_effect=lambda db, table, expected: {'rows': expected, 'table': table})
        return c, queries

    def test_synapse_collection_digests_only_known_domains_that_exist(self):
        c, _ = self.make(self.SYNAPSE_TABLES)
        result = c.collect('synapse', synapse=True)
        self.assertEqual(set(result['semantic']['domains']), markers.REQUIRED | {'pushers'})
        self.assertEqual(set(result['semantic']['table_counts']), set(self.SYNAPSE_TABLES))
        self.assertEqual(result['semantic']['event_rows'], 7)
        self.assertEqual(result['metadata'], {'database_bytes': 1})
        # Every digest is checked against the independently collected frozen count.
        for call in c.digest_table.call_args_list:
            self.assertEqual(call.args[2], 5)

    def test_missing_required_synapse_domain_is_refused(self):
        tables = sorted(markers.REQUIRED - {'event_json'})
        c, _ = self.make(tables)
        with self.assertRaisesRegex(RuntimeError, 'required Synapse source domains absent'):
            c.collect('synapse', synapse=True)
        c.digest_table.assert_not_called()

    def test_preserving_local_history_needs_the_membership_ledger(self):
        c, _ = self.make(self.SYNAPSE_TABLES)
        with self.assertRaisesRegex(RuntimeError, 'preservation membership ledger absent'):
            c.collect('synapse', synapse=True, preserve_local_history=True)

    def test_membership_ledger_is_digested_when_preserving_local_history(self):
        c, queries = self.make(self.SYNAPSE_TABLES + ['local_current_membership'])
        result = c.collect('synapse', synapse=True, server_name='example.org', preserve_local_history=True)
        self.assertIn('local_current_membership', result['semantic']['domains'])
        scope_query = next(q for q in queries if 'retained AS' in q)
        self.assertIn("'scope_server_name','example.org'", scope_query)

    def test_non_synapse_database_digests_every_table_and_skips_scope(self):
        c, queries = self.make(['alpha', 'beta'])
        result = c.collect('mas')
        self.assertEqual(set(result['semantic']['domains']), {'alpha', 'beta'})
        self.assertNotIn('event_rows', result['semantic'])
        self.assertFalse(any('retained AS' in q for q in queries))

    def test_schema_fingerprint_is_independent_of_key_order(self):
        c, _ = self.make(['alpha'])
        schema = {'columns': [], 'constraints': [], 'indexes': []}
        expected = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(c.collect('mas')['semantic']['schema_sha256'], expected)


class MainTests(unittest.TestCase):
    ARGV = ['markers.py', '--kubeconfig', 'k', '--context', 'c', '--namespace', 'n', '--pod', 'p',
            '--synapse-db', 's', '--mas-db', 'm', '--private-log-dir', '']

    def run_main(self, collect):
        with tempfile.TemporaryDirectory() as parent:
            argv = self.ARGV[:-1] + [str(Path(parent) / 'logs')]
            out = io.StringIO()
            with patch.object(sys, 'argv', argv), patch.object(markers.Collector, 'collect', collect), \
                    contextlib.redirect_stdout(out):
                try:
                    markers.main()
                except SystemExit as exit_:
                    return out.getvalue(), exit_
            return out.getvalue(), None

    def test_success_prints_a_marker_document_without_payload(self):
        calls = []

        def collect(self_, database, synapse=False, **kwargs):
            calls.append((database, synapse, kwargs))
            return {'database': database}

        output, failure = self.run_main(collect)
        self.assertIsNone(failure)
        document = json.loads(output)
        self.assertEqual(document['schema_version'], 1)
        self.assertIs(document['source_payload_exposed'], False)
        self.assertEqual(document['synapse'], {'database': 's'})
        self.assertEqual(document['mas'], {'database': 'm'})
        self.assertEqual(calls[0], ('s', True, {'server_name': 'reilly.asia', 'preserve_local_history': False}))
        self.assertEqual(calls[1], ('m', False, {}))

    def test_failure_exits_with_a_message_that_hides_the_cause(self):
        def collect(self_, *args, **kwargs):
            raise RuntimeError('password=hunter2')

        output, failure = self.run_main(collect)
        self.assertEqual(output, '')
        self.assertEqual(str(failure),
                         'Source marker collection failed; credentials and payloads suppressed')
        self.assertIsNone(failure.__cause__)
        self.assertTrue(failure.__suppress_context__)
        self.assertNotIn('hunter2', str(failure))


if __name__ == '__main__':
    unittest.main()
