#!/usr/bin/env python3
"""Wait for root's verified base116; check selected bodies on a NEW cold clone."""
import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import time

ROOT=Path(__file__).resolve().parents[2]
OFFLINE='spindle-historical-pdu-offline-20261006'
K=['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration','-n','spindle-rehearsal']

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--root-artifacts',type=Path,required=True,
                        help='root-owned passing validation/report/public-key artifact directory')
    parser.add_argument('--candidate-sha256',required=True)
    parser.add_argument('--candidate-binary',required=True,help='absolute path inside importer pod')
    parser.add_argument('--cold-base',required=True,help='root-confirmed stopped validated store inside importer pod')
    parser.add_argument('--cold-clone',required=True,help='new unused independent /work path')
    parser.add_argument('--run-tag',required=True,help='unique safe suffix for new remote helper/sample files')
    parser.add_argument('--point-helper',type=Path,required=True,help='bounded static point helper built against this candidate release')
    parser.add_argument('--point-helper-sha256',required=True)
    parser.add_argument('--wait-hours',type=float,default=6,help='bounded verified-base wait deadline,1..24 hours')
    args=parser.parse_args()
    if not 1<=args.wait_hours<=24:
        parser.error('wait hours must be between1 and24')
    if not re.fullmatch(r'[a-f0-9]{64}',args.point_helper_sha256):
        parser.error('helper SHA must be exactly64 lowercase hex characters')
    if not re.fullmatch(r'[a-f0-9]{64}',args.candidate_sha256):
        parser.error('candidate SHA must be exactly64 lowercase hex characters')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,96}',args.run_tag):
        parser.error('run tag must be a short filename component')
    for field in ('candidate_binary','cold_base','cold_clone'):
        value=getattr(args,field)
        if not value.startswith('/work/') or '..' in Path(value).parts:
            parser.error(field+' must be an absolute /work path without traversal')
    if args.cold_base==args.cold_clone:
        parser.error('cold clone must differ from validated base')
    if not args.point_helper.is_file() or args.point_helper.stat().st_size>32*1024**2:
        parser.error('bounded existing point helper required')
    if hashlib.sha256(args.point_helper.read_bytes()).hexdigest()!=args.point_helper_sha256:
        parser.error('point helper SHA mismatch')
    SHA=args.candidate_sha256
    BASE=args.cold_base
    CLONE=args.cold_clone
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        def command(cmd,data=None):
            with (args.output/'commands-stderr-private.log').open('ab') as err:
                result=subprocess.run(cmd,input=data,stdout=subprocess.PIPE,stderr=err)
            if result.returncode:raise RuntimeError('operation failed; see private stderr log')
            return result.stdout
        def remote(cmd,data=None):return command(K+['exec']+(['-i'] if data is not None else [])+['migrator-import','--']+cmd,data)
        def read_verified_json(pod,path,limit=16*1024**2):
            # Retry only immutable, read-only file fetches; never repeat helpers or writes.
            digest=command(K+['exec',pod,'--','sha256sum',path]).decode().split()[0]
            size=int(command(K+['exec',pod,'--','stat','-c','%s',path]).decode())
            if size>limit or not re.fullmatch(r'[a-f0-9]{64}',digest):
                raise RuntimeError('remote JSON exceeds bound or has invalid digest')
            for attempt in range(3):
                try:
                    raw=command(K+['exec',pod,'--','cat',path])
                    if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:
                        raise ValueError('remote JSON transport integrity mismatch')
                    json.loads(raw)
                    return raw
                except (RuntimeError,ValueError,UnicodeError):
                    if attempt==2:raise RuntimeError('bounded read-only JSON fetch failed integrity verification')
                    time.sleep(2)

        def save(phase,**fields):
            state={'phase':phase,'updated_at':time.time(),'candidate_sha256':SHA,'point_helper_sha256':args.point_helper_sha256,**fields}
            tmp=args.output/'status.tmp';tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.replace(args.output/'status.json')
        save('waiting_for_verified_base116')
        deadline=time.monotonic()+args.wait_hours*3600
        while True:
            try:
                status=json.loads((args.root_artifacts/'status.json').read_text())
            except FileNotFoundError:
                if time.monotonic()>deadline:
                    raise RuntimeError('verified-base status creation wait deadline exceeded')
                time.sleep(30)
                continue
            if status.get('candidate_sha256') != SHA:
                raise RuntimeError('root run belongs to a different candidate; refusing stale status')
            if status.get('phase')=='rehearsal_store_verified' and status.get('passed'):
                if status.get('binary_sha256') != SHA or status.get('restored_validation_binary_sha256') != SHA:
                    raise RuntimeError('root imported/restored binary provenance mismatch')
                break
            if status.get('complete') and not status.get('passed'):
                raise RuntimeError('root rehearsal failed; refusing cold clone')
            if time.monotonic()>deadline:raise RuntimeError('verified-base wait deadline exceeded')
            time.sleep(30)
        report=json.loads((args.root_artifacts/'report-restored.json').read_text())
        if len(report['rooms'])!=116 or report.get('excluded_rooms'):
            raise RuntimeError('completed scope is not exactly116 retained rooms')
        binary=remote(['sha256sum',args.candidate_binary]).decode().split()[0]
        if binary!=SHA:raise RuntimeError('remote final binary mismatch')
        scope=args.output/'rooms.json';scope.write_text(json.dumps(sorted(report['rooms'])))
        cached_keys=ROOT/'artifacts/historical-pdu-preflight-20261006/selection/source-public-key.json'
        cached_doc=json.loads(cached_keys.read_text())
        persisted_key=json.loads((args.root_artifacts/'restored-signing-public.json').read_text())
        if cached_doc['verify_keys'].get(persisted_key['key_id'],{}).get('key') != persisted_key['public_key_base64']:
            raise RuntimeError('cached source public document differs from verified persisted key')
        save('selecting_source',scope_rooms=116)
        command(['python3',str(ROOT/'scripts/federation-drill/historical-pdu.py'),'select',
                 '--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration',
                 '--namespace','spindle-rehearsal','--pod','deploy/rehearsal-pg','--database','synapse',
                 '--rooms',str(scope),'--output',str(args.output/'selection'),'--binary-sha',SHA,'--key-file',str(cached_keys)])
        # Preserve root's node headroom:15% filesystem capacity plus1GiB.
        used=int(remote(['du','-sk',BASE]).decode().split()[0])*1024
        filesystem=remote(['df','-Pk','/work']).decode().splitlines()[-1].split()
        available=int(filesystem[3])*1024
        floor=int(int(filesystem[1])*1024*0.15)+1024**3
        if available<used+floor:raise RuntimeError('insufficient node capacity for independent cold clone and required floor')
        save('cloning_cold_base',clone_bytes_estimate=used,node_available_bytes=available,node_floor_bytes=floor)
        remote(['sh','-c','test ! -e "$2" && cp -a --reflink=auto "$1" "$2"','historical-copy',BASE,CLONE])
        helper=args.point_helper
        if hashlib.sha256(helper.read_bytes()).hexdigest()!=args.point_helper_sha256:raise RuntimeError('point helper mismatch')
        remote_helper='/work/bin/pdu-point-lookup-static-'+args.run_tag
        def push_remote(name,data):
            digest=hashlib.sha256(data).hexdigest()
            remote(['sh','-eu','-c','test ! -e "$1"; test ! -e "$1.partial"; umask 077; cat > "$1.partial"; test "$(stat -c %s "$1.partial")" = "$2"; test "$(sha256sum "$1.partial" | cut -d " " -f1)" = "$3"; mv "$1.partial" "$1"','historical-push',name,str(len(data)),digest],data)
        push_remote(remote_helper,helper.read_bytes());remote(['chmod','700',remote_helper])
        samples=(args.output/'selection/source-samples.json').read_bytes()
        remote_samples='/work/historical-pdu-source-samples-'+args.run_tag+'.json';push_remote(remote_samples,samples)
        save('bounded_point_lookups')
        remote_target='/work/historical-pdu-target-'+args.run_tag+'.json'
        remote(['sh','-eu','-c','test ! -e "$1"; umask 077; "$2" --independent-cold-copy "$3" "$4" > "$1"','historical-lookup',remote_target,remote_helper,CLONE,remote_samples])
        target=read_verified_json('migrator-import',remote_target)
        (args.output/'target-pdus-private.json').write_bytes(target)
        offline_dir='/lab/historical-'+args.run_tag
        command(K+['exec',OFFLINE,'--','mkdir',offline_dir])
        files={'historical-pdu-actual.py':(ROOT/'scripts/federation-drill/historical-pdu.py').read_bytes(),
               'actual-source.json':samples,'actual-target.json':target,
               'actual-public.json':(args.output/'selection/source-public-key.json').read_bytes(),
               'actual-selection.json':(args.output/'selection/selection.json').read_bytes()}
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as tar:
            for name,data in files.items():
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))
        command(K+['exec','-i',OFFLINE,'--','tar','-xf','-','-C',offline_dir],archive.getvalue())
        for name,data in files.items():
            digest=command(K+['exec',OFFLINE,'--','sha256sum',offline_dir+'/'+name]).decode().split()[0]
            if digest!=hashlib.sha256(data).hexdigest():
                raise RuntimeError('offline input transfer integrity mismatch')
        save('offline_room_version_crypto')
        # A nonzero result can still have a valuable sanitized report; capture it.
        with (args.output/'crypto-stderr-private.log').open('wb') as err:
            result=subprocess.run(K+['exec',OFFLINE,'--','python',offline_dir+'/historical-pdu-actual.py','verify',
                '--source',offline_dir+'/actual-source.json','--target',offline_dir+'/actual-target.json','--keys',offline_dir+'/actual-public.json',
                '--selection',offline_dir+'/actual-selection.json','--binary-sha',SHA,'--output',offline_dir+'/actual-result.json'],stdout=subprocess.PIPE,stderr=err)
        proof=read_verified_json(OFFLINE,offline_dir+'/actual-result.json')
        (args.output/'result.json').write_bytes(proof)
        verified=json.loads(proof)
        if result.returncode or not verified['passed']:
            save('historical_pdu_failed',complete=True,passed=False);raise RuntimeError('historical PDU continuity failed')
        save('historical_pdu_verified',complete=True,passed=True,scope_rooms=116,samples=len(verified['samples']),
             point_helper_sha256=args.point_helper_sha256,node_floor_bytes=floor,candidate_sha256=SHA,candidate_binary=args.candidate_binary,cold_base=BASE,clone=CLONE,root_artifacts=str(args.root_artifacts),root_status_sha256=hashlib.sha256(json.dumps(status,sort_keys=True).encode()).hexdigest())
        print('Historical imported PDU continuity PASS;116-room scope, all source versions.')
    except Exception:
        failed_phase='initializing'
        status_path=args.output/'status.json'
        if status_path.exists():
            failed_phase=json.loads(status_path.read_text()).get('phase','unknown')
        tmp=args.output/'status.tmp'
        tmp.write_text(json.dumps({'phase':'historical_pdu_failed','complete':True,'passed':False,
                                  'failed_phase':failed_phase,'updated_at':time.time(),
                                  'candidate_sha256':SHA},indent=2)+'\n')
        tmp.replace(status_path)
        raise

if __name__=='__main__':main()
