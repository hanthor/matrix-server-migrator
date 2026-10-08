#!/usr/bin/env python3
"""Exact-binary, two-server synthetic federation smoke; never opens real stores."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

EXPECTED_SHA = 'e4fd7d5141f7b97e39910d1fb1f305db355b9c301366650a1f7674d73773db5a'
VERSIONS = ['1', '6', '9', '10', '12']


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(base, method, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        value = json.load(error)
        raise RuntimeError(json.dumps({'http': error.code, 'errcode': value.get('errcode'),
                                       'error': value.get('error')})) from None


def wait(call, predicate=lambda value: bool(value), seconds=45):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            value = call()
            if predicate(value):
                return value
        except Exception as error:
            last = error
        time.sleep(.25)
    raise RuntimeError('peer observation timed out: ' + str(last))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path,
                        help='New private artifact directory; must not already exist')
    parser.add_argument('--expected-sha256', default=EXPECTED_SHA)
    parser.add_argument('--tls', action='store_true', help='Private-CA HTTPS federation listeners')
    args = parser.parse_args()
    binary = args.binary.resolve()
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != args.expected_sha256:
        raise SystemExit('Binary checksum does not match requested candidate')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = args.output.resolve()
    ports = [port(), port()]
    while ports[0] == ports[1]:
        ports[1] = port()
    urls = [f'http://127.0.0.1:{p}' for p in ports]
    peer_urls = urls.copy()
    tls_config = ['', '']
    environment = os.environ.copy()
    if args.tls:
        def openssl(*values):
            subprocess.run(['openssl', *values], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                '-subj', '/CN=synthetic federation CA', '-keyout', str(root / 'ca.key'),
                '-out', str(root / 'ca.crt'), '-addext', 'basicConstraints=critical,CA:TRUE',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        openssl('req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=127.0.0.1',
                '-keyout', str(root / 'leaf.key'), '-out', str(root / 'leaf.csr'))
        (root / 'leaf.ext').write_text('subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
        openssl('x509', '-req', '-in', str(root / 'leaf.csr'), '-CA', str(root / 'ca.crt'),
                '-CAkey', str(root / 'ca.key'), '-CAcreateserial', '-days', '2',
                '-extfile', str(root / 'leaf.ext'), '-out', str(root / 'leaf.crt'))
        (root / 'ca.key').unlink()
        environment['SSL_CERT_FILE'] = str(root / 'ca.crt')
        used_ports = set(ports)
        for side in range(2):
            federation_port = port()
            while federation_port in used_ports:
                federation_port = port()
            used_ports.add(federation_port)
            peer_urls[side] = f'https://127.0.0.1:{federation_port}'
            tls_config[side] = (f'bind = "127.0.0.1:{federation_port}"\n'
                                f'tls_cert = "{root / "leaf.crt"}"\n'
                                f'tls_key = "{root / "leaf.key"}"\n')
    names = ['federation-a.invalid', 'federation-b.invalid']
    processes, handles = [], []
    report = {'binary_sha256': actual, 'source_room_versions': VERSIONS,
              'scope': 'synthetic two-Spindle loopback stores; explicit peers',
              'federation_transport': 'private-CA HTTPS' if args.tls else 'HTTP',
              'production_changed': False, 'rooms': {}, 'passed': False}
    try:
        for side in range(2):
            config = root / f'{side}.toml'
            config.write_text(f'''[server]
name = "{names[side]}"
bind = "127.0.0.1:{ports[side]}"
[storage]
path = "{root / str(side) / 'store'}"
[federation]
enabled = true
allow_internal = ["127.0.0.1/32"]
retry_base_ms = 100
{tls_config[side]}
[federation.peers."{names[1-side]}"]
url = "{peer_urls[1-side]}"
[registration]
require_token = false
[push]
enabled = false
[previews]
enabled = false
[ratelimit]
enabled = false
''')
            log = open(root / f'{side}.log', 'w')
            handles.append(log)
            processes.append(subprocess.Popen([str(binary), str(config)], stdout=log, stderr=log,
                                              env=environment))
        for url in urls:
            wait(lambda: request(url, 'GET', '/_matrix/client/versions'))
        tokens = []
        for url in urls:
            user = request(url, 'POST', '/_matrix/client/v3/register', {
                'username': 'witness', 'password': secrets.token_urlsafe(24),
                'auth': {'type': 'm.login.dummy'}})
            tokens.append(user['access_token'])
        report['capabilities'] = [request(url, 'GET', '/_matrix/client/v3/capabilities', token=token)
                                  for url, token in zip(urls, tokens)]
        for version in VERSIONS:
            result = report['rooms'][version] = {'passed': False}
            try:
                room = request(urls[0], 'POST', '/_matrix/client/v3/createRoom', {
                    'room_version': version, 'preset': 'public_chat',
                    'name': 'synthetic federation v' + version,
                    'power_level_content_override': {'events': {'m.room.topic': 0}}}, tokens[0])['room_id']
                result['room_id'] = room
                room_path = urllib.parse.quote(room, safe='')
                request(urls[1], 'POST', '/_matrix/client/v3/join/' + room_path
                        + '?server_name=' + urllib.parse.quote(names[0], safe=''),
                        {}, tokens[1])
                result['remote_join'] = True
                for side in range(2):
                    peer = 1-side
                    event = request(urls[side], 'PUT',
                        f'/_matrix/client/v3/rooms/{room_path}/send/m.room.message/{secrets.token_hex(8)}',
                        {'msgtype': 'm.text', 'body': f'v{version} side {side}'}, tokens[side])['event_id']
                    found = wait(lambda: request(urls[peer], 'GET',
                        f'/_matrix/client/v3/rooms/{room_path}/event/{urllib.parse.quote(event, safe="")}',
                        token=tokens[peer]))
                    if found.get('event_id') != event:
                        raise RuntimeError('peer returned a different event')
                    result[f'{side}_to_{peer}_event'] = event
                    topic = f'v{version} topic {side}'
                    request(urls[side], 'PUT',
                        f'/_matrix/client/v3/rooms/{room_path}/state/m.room.topic/',
                        {'topic': topic}, tokens[side])
                    wait(lambda: request(urls[peer], 'GET',
                        f'/_matrix/client/v3/rooms/{room_path}/state/m.room.topic/', token=tokens[peer]),
                        lambda value: value.get('topic') == topic)
                    result[f'{side}_state_seen_by_peer'] = True
                result['passed'] = True
            except Exception as error:
                result['error'] = str(error)
            (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        report['passed'] = all(result['passed'] for result in report['rooms'].values())
    except Exception as error:
        report['error'] = str(error)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    os.umask(0o077)
    main()
