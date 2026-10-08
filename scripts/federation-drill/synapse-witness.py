#!/usr/bin/env python3
"""Fresh Synapse witness inside a disposable pod; loopback peers and sqlite only."""
import argparse
import ssl
import urllib.request
import urllib.error
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.parse

from loopback import VERSIONS, request, wait


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary-sha', required=True)
    args = parser.parse_args()
    if len(args.binary_sha) != 64 or any(c not in '0123456789abcdef' for c in args.binary_sha):
        raise SystemExit('invalid explicit candidate SHA')
    root = Path('/lab')
    actual = hashlib.sha256((root / 'spindle').read_bytes()).hexdigest()
    if actual != args.binary_sha:
        raise SystemExit('wrong candidate binary')
    names = ['127.0.0.1:18448', '127.0.0.1:28448']
    urls = ['http://127.0.0.1:18008', 'http://127.0.0.1:28008']
    (root / 'log.yaml').write_text(json.dumps({
        'version': 1, 'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
        'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
        'root': {'level': 'INFO', 'handlers': ['console']}, 'disable_existing_loggers': False}))
    (root / 'homeserver.yaml').write_text(json.dumps({
        'server_name': names[0], 'report_stats': False, 'pid_file': '/lab/synapse.pid',
        'log_config': '/lab/log.yaml', 'signing_key_path': '/lab/synapse.key',
        'tls_certificate_path': '/lab/leaf.crt', 'tls_private_key_path': '/lab/leaf.key',
        'listeners': [
            {'port': 18008, 'type': 'http', 'tls': False, 'bind_addresses': ['127.0.0.1'],
             'resources': [{'names': ['client']}]},
            {'port': 18448, 'type': 'http', 'tls': True, 'bind_addresses': ['127.0.0.1'],
             'resources': [{'names': ['federation', 'keys']}]},
        ],
        'database': {'name': 'sqlite3', 'args': {'database': '/lab/synapse.db'}},
        'media_store_path': '/lab/media', 'enable_registration': True,
        'enable_registration_without_verification': True,
        'macaroon_secret_key': secrets.token_hex(32), 'form_secret': secrets.token_hex(32),
        'trusted_key_servers': [], 'suppress_key_server_warning': True,
        'federation_domain_whitelist': names, 'federation_custom_ca_list': ['/lab/ca.crt'],
        'federation_verify_certificates': True,
        # Synapse's literal-IP federation path checks the blocklist directly.
        # Express the exception in the blocklist itself, keeping all other IPs blocked.
        'ip_range_blacklist': [str(network) for network in
                              ipaddress.ip_network('0.0.0.0/0').address_exclude(
                                  ipaddress.ip_network('127.0.0.1/32'))] + ['::/0'],
        'ip_range_whitelist': ['127.0.0.1/32'],
        'url_preview_enabled': False, 'push': {'enabled': False},
        'rc_message': {'per_second': 1000, 'burst_count': 1000},
        'rc_joins': {'local': {'per_second': 100, 'burst_count': 100},
                     'remote': {'per_second': 100, 'burst_count': 100}},
        'rc_registration': {'per_second': 100, 'burst_count': 100},
        'rc_federation': {'window_size': 1000, 'sleep_limit': 1000, 'sleep_delay': 10,
                          'reject_limit': 1000, 'concurrent': 100},
    }))
    (root / 'spindle.toml').write_text('''[server]
name = "127.0.0.1:28448"
bind = "127.0.0.1:28008"
[storage]
path = "/lab/spindle-store"
[federation]
enabled = true
allow_internal = ["127.0.0.1/32"]
bind = "127.0.0.1:28448"
tls_cert = "/lab/leaf.crt"
tls_key = "/lab/leaf.key"
retry_base_ms = 100
[federation.peers."127.0.0.1:18448"]
url = "https://127.0.0.1:18448"
[registration]
require_token = false
[push]
enabled = false
[previews]
enabled = false
[ratelimit]
enabled = false
''')
    report = {'binary_sha256': actual, 'synapse_version': __import__('synapse').__version__,
              'source_room_versions': VERSIONS, 'rooms': {}, 'passed': False,
              'scope': 'fresh synthetic sqlite Synapse and Spindle; private-CA HTTPS loopback',
              'delegation_checked': False, 'production_changed': False}
    process = None
    try:
        log = open('/lab/synapse.log', 'w')
        process = subprocess.Popen([sys.executable, '-m', 'synapse.app.homeserver',
                                    '-c', '/lab/homeserver.yaml'], stdout=log, stderr=log)
        (root / 'start').touch()
        for url in urls:
            wait(lambda: request(url, 'GET', '/_matrix/client/versions'), seconds=90)
        report['negative_tls'] = {}
        for peer_port in [18448, 28448]:
            try:
                with urllib.request.urlopen(f'https://127.0.0.1:{peer_port}/_matrix/key/v2/server', context=ssl.create_default_context(), timeout=10):
                    raise RuntimeError('untrusted synthetic CA unexpectedly accepted')
            except urllib.error.URLError as error:
                if not isinstance(error.reason, ssl.SSLCertVerificationError):
                    raise
                report['negative_tls'][str(peer_port)] = 'untrusted_CA_rejected'
        tokens = [request(url, 'POST', '/_matrix/client/v3/register', {
                    'username': 'witness', 'password': secrets.token_urlsafe(24),
                    'auth': {'type': 'm.login.dummy'}})['access_token'] for url in urls]
        report['capabilities'] = [request(url, 'GET', '/_matrix/client/v3/capabilities', token=token)
                                  for url, token in zip(urls, tokens)]
        for creator in range(2):
            for version in VERSIONS:
                label = f'{"synapse" if creator == 0 else "spindle"}-created-v{version}'
                result = report['rooms'][label] = {'passed': False}
                try:
                    room = request(urls[creator], 'POST', '/_matrix/client/v3/createRoom', {
                        'room_version': version, 'preset': 'public_chat',
                        'power_level_content_override': {'events': {'m.room.topic': 0}}},
                        tokens[creator])['room_id']
                    result['room_id'] = room
                    room_path = urllib.parse.quote(room, safe='')
                    request(urls[1-creator], 'POST', '/_matrix/client/v3/join/' + room_path
                            + '?server_name=' + urllib.parse.quote(names[creator], safe=''),
                            {}, tokens[1-creator])
                    result['remote_join'] = True
                    for side in range(2):
                        peer = 1-side
                        event = request(urls[side], 'PUT',
                            f'/_matrix/client/v3/rooms/{room_path}/send/m.room.message/{secrets.token_hex(8)}',
                            {'msgtype': 'm.text', 'body': label}, tokens[side])['event_id']
                        found = wait(lambda: request(urls[peer], 'GET',
                            f'/_matrix/client/v3/rooms/{room_path}/event/{urllib.parse.quote(event, safe="")}',
                            token=tokens[peer]))
                        if found.get('event_id') != event:
                            raise RuntimeError('peer event ID differs')
                        result[f'{side}_to_{peer}_event'] = event
                        topic = label + f' side {side}'
                        request(urls[side], 'PUT', f'/_matrix/client/v3/rooms/{room_path}/state/m.room.topic/',
                                {'topic': topic}, tokens[side])
                        wait(lambda: request(urls[peer], 'GET',
                            f'/_matrix/client/v3/rooms/{room_path}/state/m.room.topic/', token=tokens[peer]),
                            lambda value: value.get('topic') == topic)
                        result[f'{side}_state_seen_by_peer'] = True
                    result['passed'] = True
                except Exception as error:
                    result['error'] = str(error)
                (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        report['passed'] = len(report['rooms']) == 10 and all(r['passed'] for r in report['rooms'].values())
    except Exception as error:
        report['error'] = str(error)
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    os.umask(0o077)
    main()
