#!/usr/bin/env python3
"""Root-operated frozen-source backup to prepared S3 uploader, no full local spool."""
import argparse
import base64
import datetime
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', required=True)
    parser.add_argument('--prefix', required=True, help='Fresh postgres/spindle-cutover/TIMESTAMP-UUID prefix')
    parser.add_argument('--output', type=Path, required=True, help='New local reports only directory')
    parser.add_argument('--uploader', default='spindle-cutover-stream-upload-v2')
    parser.add_argument('--media-pod', default='ess-spindle-source-media-export')
    parser.add_argument('--server-name', default='reilly.asia')
    parser.add_argument('--preserve-local-history', action='store_true')
    parser.add_argument('--reuse-media-capture', type=Path,
                        help='Verified prior objects.json; reuse only immutable media after continued-quiesce guards')
    parser.add_argument('--pre-quiesce-workloads', type=Path,
                        help='Original workload List for UID/generation continuity when reusing media')
    parser.add_argument('--media-reuse-proof', type=Path,
                        help='Reviewed provenance linking prior media to this same uninterrupted pause')
    parser.add_argument('--control-directory', type=Path,
                        help='Private immutable original routing/Helm/quiesce control snapshot')
    args = parser.parse_args()
    if args.control_directory is not None:
        args.control_directory = args.control_directory.resolve(strict=True)
        if not args.control_directory.is_dir() or args.control_directory.stat().st_mode & 0o077:
            raise RuntimeError('source control snapshot must be a private directory')
        if not any(args.control_directory.iterdir()):
            raise RuntimeError('source control snapshot is empty')
    if not args.prefix.startswith('postgres/spindle-cutover/') or '..' in args.prefix.split('/'):
        raise SystemExit('invalid fresh backup prefix')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    k = ['kubectl', '--kubeconfig', args.kubeconfig, '--context', args.context]

    def read(namespace, *command):
        return subprocess.check_output(k + ['-n', namespace, *command])

    def resource(kind, name):
        return json.loads(read('ess', 'get', kind, name, '-o', 'json'))

    def frozen():
        for kind, name in [('deploy', 'ess-haproxy'), ('deploy', 'ess-matrix-authentication-service'),
                           ('sts', 'ess-synapse-main'), ('sts', 'ess-synapse-fed-sender'),
                           ('sts', 'ess-synapse-sliding-sync')]:
            obj = resource(kind, name)
            if obj['spec']['replicas'] != 0 or any(obj.get('status', {}).get(f, 0) != 0
                                                  for f in ['replicas', 'readyReplicas']):
                raise RuntimeError('source workload is not fully quiesced')
        count = read('postgres', 'exec', 'deploy/postgres', '--', 'psql', '-X', '-U', 'postgres',
                     '-d', 'postgres', '-Atc', "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname IN ('synapse','mas')")
        if count.strip() != b'0':
            raise RuntimeError('source database has connected writers')
        return read('postgres', 'exec', 'deploy/postgres', '--', 'psql', '-X', '-U', 'postgres',
                    '-d', 'synapse', '-Atc', "SELECT json_build_object('event_rows',count(*),'stream_high_water',max(stream_ordering)) FROM events")

    before = frozen()
    (args.output / 'source-baseline.json').write_bytes(before)
    pvc = resource('pvc', 'synapse-media-preseed')
    if pvc['metadata']['uid'] != '5f55483d-44b2-48ab-bfee-6226b3a811b2':
        raise RuntimeError('source media identity changed')
    pod = resource('pod', args.media_pod)
    if not any(v.get('persistentVolumeClaim', {}).get('claimName') == 'synapse-media-preseed'
               and v['persistentVolumeClaim'].get('readOnly') is True for v in pod['spec']['volumes']):
        raise RuntimeError('exporter source volume is not read-only')
    if len(pod['spec']['containers']) != 1 or not all(m.get('readOnly') is True
        for m in pod['spec']['containers'][0].get('volumeMounts', [])):
        raise RuntimeError('exporter mount profile changed')
    reports = []
    paused_workloads = [resource(kind, name) for kind, name in [
        ('deploy', 'ess-haproxy'), ('deploy', 'ess-matrix-authentication-service'),
        ('sts', 'ess-synapse-main'), ('sts', 'ess-synapse-fed-sender'), ('sts', 'ess-synapse-sliding-sync')]]
    (args.output / 'capture-start.json').write_text(json.dumps({
        'prefix': args.prefix, 'source_high_water': json.loads(before),
        'source_media_pvc_uid': pvc['metadata']['uid'], 'exporter_pod_uid': pod['metadata']['uid'],
        'paused_workloads': {w['metadata']['name']: {
            'uid': w['metadata']['uid'], 'generation': w['metadata']['generation']}
            for w in paused_workloads},
    }, indent=2) + '\n')

    reused_media = None
    expected_workload_names = {'ess-haproxy', 'ess-matrix-authentication-service',
                               'ess-synapse-main', 'ess-synapse-fed-sender', 'ess-synapse-sliding-sync'}
    def media_reuse_guards(originals):
        if len(originals) != 5 or {o['metadata']['name'] for o in originals} != expected_workload_names:
            raise RuntimeError('media reuse requires the exact five original workloads')
        for original in originals:
            current = resource(original['kind'], original['metadata']['name'])
            if (current['metadata']['uid'] != original['metadata']['uid'] or
                    current['metadata']['generation'] != original['metadata']['generation'] + 1):
                raise RuntimeError('source workload generation does not prove continued pause')
        if resource('pvc', 'synapse-media-preseed')['metadata']['uid'] != pvc['metadata']['uid']:
            raise RuntimeError('source media identity changed')
        current_exporter = resource('pod', args.media_pod)
        if (current_exporter['metadata']['uid'] != pod['metadata']['uid'] or
                current_exporter['spec'] != pod['spec']):
            raise RuntimeError('read-only source exporter changed')
        active_pods = json.loads(read('ess', 'get', 'pods', '-o', 'json'))['items']
        for active in active_pods:
            if active.get('status', {}).get('phase') not in ['Succeeded', 'Failed']:
                if any(v.get('persistentVolumeClaim', {}).get('claimName') == 'synapse-media-preseed'
                       and not v['persistentVolumeClaim'].get('readOnly', False)
                       for v in active['spec'].get('volumes', [])):
                    raise RuntimeError('source media has an active writable opener')

    if args.reuse_media_capture is not None:
        if args.pre_quiesce_workloads is None or args.media_reuse_proof is None:
            raise RuntimeError('media reuse requires workload continuity evidence')
        originals = json.loads(args.pre_quiesce_workloads.read_text())['items']
        media_reuse_guards(originals)
        prior = json.loads(args.reuse_media_capture.read_text())
        candidates = [r for r in prior if r['key'].endswith('/source/media.tar')]
        if len(candidates) != 1:
            raise RuntimeError('prior media report missing or ambiguous')
        reused_media = candidates[0]
        provenance = json.loads(args.media_reuse_proof.read_text())
        current_start = json.loads((args.output / 'capture-start.json').read_text())
        if not (provenance['reviewed_same_uninterrupted_pause'] is True and
                reused_media['key'] == provenance['prior_prefix'].rstrip('/') + '/source/media.tar' and
                provenance['source_media_pvc_uid'] == current_start['source_media_pvc_uid'] and
                provenance['exporter_pod_uid'] == current_start['exporter_pod_uid'] and
                provenance['paused_workloads'] == current_start['paused_workloads']):
            raise RuntimeError('media provenance does not identify this same pause')
        if not (reused_media['full_object_reread_verified'] and reused_media['overwrite_prevented']
                and reused_media['key'].startswith('postgres/spindle-cutover/')
                and '..' not in reused_media['key'].split('/') and reused_media['bytes'] > 0):
            raise RuntimeError('prior media report is not verified')
        (args.output / 'media-reuse-guards.json').write_text(json.dumps({
            'continued_workload_uid_generation_pause_verified': True,
            'no_active_writable_source_media_opener': True,
            'source_media_pvc_uid': pvc['metadata']['uid'],
            'exporter_pod_uid': pod['metadata']['uid'],
            'prior_media': reused_media,
            'database_dumps_reused': False,
            'provenance': provenance,
        }, indent=2) + '\n')

    def upload(name, data=None, producer=None):
        command = k + ['-n', 'postgres', 'exec', '-i', args.uploader, '--', 'python3',
                       '/script/s3-stream.py', '--key', args.prefix.rstrip('/') + '/source/' + name]
        process = None
        with open(args.output / (name + ".producer-private.log"), "xb",
                  opener=lambda p, f: os.open(p, f, 0o600)) as producer_log, \
                open(args.output / (name + ".uploader-private.log"), "xb",
                     opener=lambda p, f: os.open(p, f, 0o600)) as uploader_log:
            try:
                if producer is not None:
                    process = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=producer_log)
                    result = subprocess.run(command, stdin=process.stdout, stdout=subprocess.PIPE,
                                            stderr=uploader_log, timeout=21600)
                    process.stdout.close()
                    source_exit = process.wait(timeout=30)
                    if source_exit != 0:
                        raise RuntimeError('source producer failed; prefix remains incomplete')
                else:
                    result = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                            stderr=uploader_log, timeout=21600)
            finally:
                if process is not None:
                    process.stdout.close()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=15)
        if result.returncode != 0:
            raise RuntimeError('upload/reread verification failed; prefix remains incomplete')
        report = json.loads(result.stdout)
        reports.append(report)
        (args.output / 'objects.json').write_text(json.dumps(reports, indent=2) + '\n')

    for database in ['synapse', 'mas']:
        upload(database + '.dump', producer=k + ['-n', 'postgres', 'exec', 'deploy/postgres', '--',
                                                 'pg_dump', '-U', 'postgres', '--format=custom',
                                                 '--compress=zstd:1',
                                                 '--no-owner', '--no-privileges', database])
    if reused_media is None:
        upload('media.tar', producer=k + ['-n', 'ess', 'exec', args.media_pod, '--',
                                         'tar', '-C', '/media', '-cf', '-', '.'])
    else:
        # Both proven stream helpers run in the existing credential-bearing
        # uploader. No 5GB media stream traverses the controller/API again.
        # The downloader verifies every source byte; conditional upload then
        # rereads every destination byte. Both producer exits must succeed.
        downloader = (Path(__file__).resolve().parents[1] / 'source-restore' / 's3-download.py').read_text()
        destination = args.prefix.rstrip('/') + '/source/media.tar'
        worker = '''import json,subprocess,sys
source=json.loads(sys.argv[1]); destination=sys.argv[2]; code=sys.argv[3]
p=subprocess.Popen(['python3','-c',code,'--key',source['key'],'--bytes',str(source['bytes']),'--sha256',source['sha256']],stdout=subprocess.PIPE)
try:
 r=subprocess.run(['python3','/script/s3-stream.py','--key',destination],stdin=p.stdout,stdout=subprocess.PIPE)
 p.stdout.close(); producer=p.wait(timeout=30)
 if r.returncode or producer: raise RuntimeError('verified media stream copy failed')
 report=json.loads(r.stdout)
 if report['bytes']!=source['bytes'] or report['sha256']!=source['sha256']: raise RuntimeError('copied media differs')
 print(json.dumps(report))
finally:
 p.stdout.close()
 if p.poll() is None:
  p.terminate()
  try:p.wait(timeout=15)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=15)
'''
        with (args.output / 'media.tar.reuse-private.log').open('xb') as log:
            result = subprocess.run(k + ['-n', 'postgres', 'exec', args.uploader, '--',
                'python3', '-c', worker, json.dumps(reused_media), destination, downloader],
                stdout=subprocess.PIPE, stderr=log, timeout=21600)
        if result.returncode != 0:
            raise RuntimeError('media reuse copy failed; prefix remains incomplete')
        report = json.loads(result.stdout)
        if (report['key'] != destination or report['bytes'] != reused_media['bytes'] or
                report['sha256'] != reused_media['sha256'] or
                not report['full_object_reread_verified'] or not report['overwrite_prevented']):
            raise RuntimeError('media reuse proof differs')
        reports.append(report)
        (args.output / 'objects.json').write_text(json.dumps(reports, indent=2) + '\n')
    for secret, keys in [('ess-synapse', {'SIGNING_KEY': 'signing.key', 'POSTGRES_PASSWORD': 'source-db.password'}),
                          ('ess-generated', {'MAS_SYNAPSE_SHARED_SECRET': 'mas-homeserver.secret'})]:
        value = resource('secret', secret)
        for key, filename in keys.items():
            upload(filename, data=base64.b64decode(value['data'][key]))
    mas = resource('deploy', 'ess-matrix-authentication-service')
    names = sorted({v['secret']['secretName'] for v in mas['spec']['template']['spec'].get('volumes', []) if 'secret' in v})
    upload('mas-secrets-private.json', data=json.dumps({'apiVersion': 'v1', 'kind': 'List',
        'items': [resource('secret', name) for name in names]}).encode())
    upload('configs-private.json', data=read('ess', 'get', 'cm/ess-haproxy', 'cm/ess-synapse',
                                            'cm/ess-matrix-authentication-service', 'cm/ess-well-known-haproxy',
                                            'ingress/ess-synapse', '-o', 'json'))
    if args.control_directory is not None:
        upload('source-control.tar.gz', producer=['tar', '-C', str(args.control_directory), '-czf', '-', '.'])
    marker_script = Path(__file__).resolve().parents[1] / 'source-restore' / 'markers.py'
    if not marker_script.is_file():
        raise RuntimeError('source restore marker verifier is missing')
    upload('source-restore-markers.json', producer=['python3', str(marker_script),
           '--kubeconfig', args.kubeconfig, '--context', args.context, '--namespace', 'postgres',
           '--pod', 'deploy/postgres', '--synapse-db', 'synapse', '--mas-db', 'mas',
           '--private-log-dir', str(args.output / 'source-marker-private-logs'),
           '--server-name', args.server_name] + (['--preserve-local-history'] if args.preserve_local_history else []))
    after = frozen()
    if reused_media is not None:
        media_reuse_guards(originals)
    if before != after:
        raise RuntimeError('source high-water changed; prefix remains incomplete')
    upload('source-high-water.json', data=before)
    manifest = {'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'source_high_water': json.loads(before), 'objects': reports.copy(),
                'status': 'capture_complete_restore_pending', 'full_local_spool': False,
                'postgres_dump_compression': 'zstd:1',
                'source_control_included': args.control_directory is not None}
    if reused_media is not None:
        manifest['media_reused_from'] = reused_media
        manifest['media_reuse_continued_quiesce_verified'] = True
        manifest['database_dumps_reused'] = False
    upload('CAPTURE-COMPLETE.json', data=json.dumps(manifest, indent=2).encode()+b'\n')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'prefix': args.prefix, 'objects': len(reports),
                      'status': 'capture_complete_restore_pending', 'full_local_spool': False}))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception:
        raise SystemExit('Frozen source stream backup failed; prefix remains incomplete; credential contents suppressed') from None
