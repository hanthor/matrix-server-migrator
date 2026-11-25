"""The ESS post-render overlay must rewrite only what it was reviewed to rewrite."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'production-cutover/helm-post-render.py'
spec = importlib.util.spec_from_file_location('helm_post_render', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

MAIN = '_synapse-http._tcp.ess-synapse-main.ess.svc.cluster.local.'
SLIDING = '_synapse-http._tcp.ess-synapse-sliding-sync.ess.svc.cluster.local.'
SPINDLE = '_synapse-http._tcp.ess-spindle.ess.svc.cluster.local.'

HAPROXY = f'''global
  log stdout format raw local0
defaults
  mode http
backend synapse-main
  http-check send meth GET uri /health ver HTTP/1.1
  server-template synapse-main 1 {MAIN}:8008 check
backend synapse-main-failover
  http-check send meth GET uri /health ver HTTP/1.1
  server-template synapse-failover 1 {MAIN}:8008 check
backend synapse-sliding-sync
  http-check send meth GET uri /health ver HTTP/1.1
  server-template synapse-sliding 1 {SLIDING}:8008 check
backend unrelated
  http-check send meth GET uri /health ver HTTP/1.1
  server-template other 1 {MAIN}:8008 check
'''


def documents():
    """A minimal rendered ESS profile that satisfies every presence check."""
    return [
        {'kind': 'ConfigMap', 'metadata': {'name': 'ess-haproxy', 'namespace': 'ess'},
         'data': {'haproxy.cfg': HAPROXY}},
        {'kind': 'StatefulSet', 'metadata': {'name': 'ess-synapse-main', 'namespace': 'ess'},
         'spec': {'replicas': 1}},
        {'kind': 'StatefulSet', 'metadata': {'name': 'ess-synapse-fed-sender', 'namespace': 'ess'},
         'spec': {'replicas': 2}},
        {'kind': 'StatefulSet', 'metadata': {'name': 'ess-synapse-sliding-sync', 'namespace': 'ess'},
         'spec': {'replicas': 1}},
        {'kind': 'Deployment', 'metadata': {'name': 'ess-haproxy', 'namespace': 'ess'},
         'spec': {'replicas': 2}},
        {'kind': 'Deployment', 'metadata': {'name': 'ess-matrix-authentication-service',
                                            'namespace': 'ess'},
         'spec': {'replicas': 1}},
    ]


def by_name(docs, kind, name):
    return next(d for d in docs if d['kind'] == kind and d['metadata']['name'] == name)


def backend_block(config, backend):
    lines, inside = [], False
    for line in config.splitlines():
        if line.startswith('backend '):
            inside = line.split()[1] == backend
        elif line and not line[0].isspace():
            inside = False
        if inside:
            lines.append(line)
    return '\n'.join(lines)


class TransformTests(unittest.TestCase):
    def test_rewrites_server_templates_and_health_checks_in_the_three_backends(self):
        out = module.transform(HAPROXY)
        for backend in ('synapse-main', 'synapse-main-failover', 'synapse-sliding-sync'):
            block = backend_block(out, backend)
            self.assertIn(SPINDLE, block)
            self.assertNotIn(MAIN, block)
            self.assertNotIn(SLIDING, block)
            self.assertIn('meth GET uri /ready', block)
            self.assertNotIn('uri /health', block)

    def test_sliding_sync_backend_is_repointed_from_its_own_service(self):
        # The sliding-sync source differs from the main one; both must end at Spindle.
        self.assertIn(SLIDING, backend_block(HAPROXY, 'synapse-sliding-sync'))
        self.assertIn(SPINDLE, backend_block(module.transform(HAPROXY), 'synapse-sliding-sync'))

    def test_lines_outside_the_three_backends_are_untouched(self):
        out = module.transform(HAPROXY)
        self.assertEqual(backend_block(out, 'unrelated'), backend_block(HAPROXY, 'unrelated'))
        self.assertTrue(out.startswith('global\n  log stdout format raw local0\ndefaults\n'))

    def test_transform_is_idempotent(self):
        once = module.transform(HAPROXY)
        self.assertEqual(module.transform(once), once)

    def test_foreign_server_template_is_refused(self):
        bad = HAPROXY.replace(f'server-template synapse-main 1 {MAIN}',
                              'server-template synapse-main 1 elsewhere.example.', 1)
        with self.assertRaisesRegex(ValueError, 'unexpected backend'):
            module.transform(bad)

    def test_unexpected_health_check_is_refused(self):
        bad = HAPROXY.replace('http-check send meth GET uri /health ver',
                              'http-check send meth GET uri /metrics ver', 1)
        with self.assertRaisesRegex(ValueError, 'unexpected health check'):
            module.transform(bad)

    def test_missing_backend_is_refused(self):
        bad = HAPROXY.replace('backend synapse-main-failover', 'backend renamed')
        with self.assertRaisesRegex(ValueError, 'missing backend templates'):
            module.transform(bad)

    def test_duplicated_server_template_is_refused(self):
        extra = f'  server-template synapse-main 2 {MAIN}:8008 check\n'
        bad = HAPROXY.replace('backend synapse-main-failover', extra + 'backend synapse-main-failover')
        with self.assertRaisesRegex(ValueError, 'missing backend templates'):
            module.transform(bad)

    def test_new_top_level_section_ends_the_backend(self):
        # `listen` closes the previous backend, so its server-template is not counted.
        bad = HAPROXY + f'listen stats\n  server-template stats 1 {MAIN}:8008 check\n'
        out = module.transform(bad)
        self.assertIn(f'server-template stats 1 {MAIN}:8008', out)


class RenderTests(unittest.TestCase):
    def test_source_mode_changes_nothing(self):
        docs = documents()
        self.assertEqual(module.render(copy.deepcopy(docs), 'source'), docs)

    def test_quiesced_mode_zeroes_writers_and_fronts_only(self):
        out = module.render(documents(), 'quiesced')
        for kind, name in [('StatefulSet', 'ess-synapse-main'), ('StatefulSet', 'ess-synapse-fed-sender'),
                           ('StatefulSet', 'ess-synapse-sliding-sync'), ('Deployment', 'ess-haproxy'),
                           ('Deployment', 'ess-matrix-authentication-service')]:
            self.assertEqual(by_name(out, kind, name)['spec']['replicas'], 0, name)
        # quiesced does not rewrite the HAProxy config
        self.assertEqual(by_name(out, 'ConfigMap', 'ess-haproxy')['data']['haproxy.cfg'], HAPROXY)

    def test_target_mode_zeroes_writers_but_keeps_fronts_and_rewrites_config(self):
        out = module.render(documents(), 'target')
        for name in module.WRITERS:
            self.assertEqual(by_name(out, 'StatefulSet', name)['spec']['replicas'], 0, name)
        self.assertEqual(by_name(out, 'Deployment', 'ess-haproxy')['spec']['replicas'], 2)
        self.assertEqual(by_name(out, 'Deployment', 'ess-matrix-authentication-service')['spec']['replicas'], 1)
        config = by_name(out, 'ConfigMap', 'ess-haproxy')['data']['haproxy.cfg']
        self.assertIn(SPINDLE, config)
        self.assertNotIn('uri /health', backend_block(config, 'synapse-main'))
        self.assertIn('uri /health', backend_block(config, 'unrelated'))

    def test_resources_outside_the_ess_namespace_are_ignored(self):
        docs = documents() + [
            {'kind': 'StatefulSet', 'metadata': {'name': 'ess-synapse-main', 'namespace': 'other'},
             'spec': {'replicas': 3}},
        ]
        out = module.render(docs, 'quiesced')
        self.assertEqual(out[-1]['spec']['replicas'], 3)

    def test_missing_namespace_defaults_to_ess(self):
        docs = documents()
        del by_name(docs, 'StatefulSet', 'ess-synapse-main')['metadata']['namespace']
        out = module.render(docs, 'quiesced')
        self.assertEqual(by_name(out, 'StatefulSet', 'ess-synapse-main')['spec']['replicas'], 0)

    def test_non_mapping_documents_are_skipped(self):
        out = module.render([None, 'text'] + documents(), 'source')
        self.assertEqual(len(out), 8)

    def test_changed_profile_is_refused(self):
        for index in range(len(documents())):
            docs = documents()
            del docs[index]
            with self.subTest(removed=index):
                with self.assertRaisesRegex(ValueError, 'rendered ESS profile changed'):
                    module.render(docs, 'source')


class CommandLineTests(unittest.TestCase):
    def run_script(self, stdin, mode='quiesced'):
        return subprocess.run([sys.executable, '-I', str(SCRIPT), '--mode', mode],
                              input=stdin, capture_output=True, text=True)

    def test_round_trips_a_multi_document_stream(self):
        stream = yaml.safe_dump_all(documents(), sort_keys=False)
        result = self.run_script(stream)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = list(yaml.safe_load_all(result.stdout))
        self.assertEqual(by_name(out, 'Deployment', 'ess-haproxy')['spec']['replicas'], 0)

    def test_failure_suppresses_credential_contents(self):
        secret = 'SUPER-SECRET-TOKEN'
        docs = documents()[:-1] + [{'kind': 'Secret', 'metadata': {'name': 'x'},
                                    'stringData': {'token': secret}}]
        result = self.run_script(yaml.safe_dump_all(docs))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('ESS post-render failed; credential contents suppressed', result.stderr)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_unparseable_input_fails_closed(self):
        result = self.run_script('{unclosed: [')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_mode_is_required_and_validated(self):
        bad = subprocess.run([sys.executable, '-I', str(SCRIPT), '--mode', 'bogus'],
                             input='', capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == '__main__':
    unittest.main()
