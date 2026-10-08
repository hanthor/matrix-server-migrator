#!/usr/bin/env python3
"""Root-operated stopped-target S3 stream and disposable node12 restore; never applies resources."""
import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import importlib.util

GIB=1024**3
ROOT=Path(__file__).resolve().parents[2]
_proof_spec = importlib.util.spec_from_file_location('notification_boundary', ROOT/'scripts/notification-boundary.py')
_proof_module = importlib.util.module_from_spec(_proof_spec)
_proof_spec.loader.exec_module(_proof_module)
require_boundary = _proof_module.require_boundary
# Keep existing invocations backward compatible; select a new reviewed pack explicitly.
PACK=Path(os.environ.get('SPINDLE_CANDIDATE_PACK',str(ROOT/'artifacts/production-20261006-pvc-fallback-8e40719'))).resolve()
CANDIDATE=json.loads((PACK/'identity.json').read_text())
REVISION=CANDIDATE['revision']
SHA=CANDIDATE['binary_sha256']
assert re.fullmatch(r'[0-9a-f]{40}',REVISION),'invalid candidate revision'
assert re.fullmatch(r'[0-9a-f]{64}',SHA),'invalid candidate SHA256'
BINARY=CANDIDATE.get('binary_basename',Path(CANDIDATE.get('binary_path','/artifact/bin/spindle-'+REVISION[:7])).name)
assert BINARY=='spindle-'+REVISION[:7],'candidate binary filename/revision mismatch'
BINARY_REL='bin/'+BINARY
BINARY_TARGET='/target/'+BINARY_REL
BASE=CANDIDATE.get('base_image','oci.element.io/synapse@sha256:d2215c4a0e0bbd304489af228345b31d6857c1a228175471358d3fda187c0d91')
assert re.fullmatch(r'oci\.element\.io/synapse@sha256:[0-9a-f]{64}',BASE),'candidate base must be immutable'
UID=CANDIDATE.get('target_pvc_uid','29022f50-02a3-4e5a-84bd-88376dd90c73')
assert re.fullmatch(r'[0-9a-f-]{36}',UID),'invalid target PVC UID'
DOWNLOAD=(ROOT/'scripts/source-restore/s3-download.py').read_text()
INVENTORY=r'''import os,pathlib,hashlib,json,sys
root=pathlib.Path(sys.argv[1]); result={}
for directory,dirs,files in os.walk(root):
 for name in dirs+files:
  path=pathlib.Path(directory)/name
  if path.is_symlink():raise SystemExit('symlink forbidden in cold target')
 for name in files:
  path=pathlib.Path(directory)/name; rel=str(path.relative_to(root))
  if rel.startswith('metadata/'):continue
  with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  result[rel]={'bytes':path.stat().st_size,'sha256':digest}
print(json.dumps(result,sort_keys=True))
'''
EXTRACT=r'''import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1]);root.mkdir(exist_ok=True); selection=sys.argv[2] if len(sys.argv)>2 else 'full'; written=0
if any(root.iterdir()):raise SystemExit('restore destination is not empty')
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
 for member in archive:
  if pathlib.PurePosixPath(member.name).is_absolute() or '..' in pathlib.PurePosixPath(member.name).parts:raise SystemExit('unsafe archive path')
  if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):raise SystemExit('unexpected archive member type')
  if selection=='local-content' and pathlib.PurePosixPath(member.name).parts[:2]!=('media_store','local_content'):continue
  written+=member.size
  if selection=='local-content' and written>256*1024**2:raise SystemExit('selected local media exceeded capacity budget')
  archive.extract(member,path=root,filter='data')
'''


CAPACITY_READ = "import os,json; s=os.statvfs('/target'); print(json.dumps({'free':s.f_bavail*s.f_frsize,'total':s.f_blocks*s.f_frsize}))"
READ_ONLY_JSON_COMMANDS = {
    ('cat', '/target/import/report.json'),
    ('cat', '/target/metadata/file-manifest.json'),
    ('cat', '/target/metadata/identity.json'),
    ('python3', '-c', INVENTORY, '/target'),
    ('python3', '-c', CAPACITY_READ),
}


def remote_exec(command, pod, argv, data=None, timeout=21600):
    # Read-only execs need no stdin channel; -i caused observed large-report
    # websocket truncation. Empty bytes still intentionally request stdin.
    args = command('ess', 'exec', *(['-i'] if data is not None else []), pod, '--', *argv)
    return subprocess.check_output(args, input=data, timeout=timeout)


def readonly_remote_json(command, pod, argv, attempts=3, timeout=300):
    # Only these exact read operations may retry; copying metadata, extraction,
    # validation and upload commands always remain single execution attempts.
    if tuple(argv) not in READ_ONLY_JSON_COMMANDS:
        raise ValueError('JSON retry requires an explicitly read-only command')
    for attempt in range(attempts):
        try:
            return json.loads(remote_exec(command, pod, argv, timeout=timeout))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, UnicodeDecodeError):
            if attempt + 1 == attempts:
                raise
    raise RuntimeError('read-only JSON retry limit must be positive')


def manifests(out):
    source=json.loads((PACK/'import.json').read_text())['items'][0]['spec']['template']['spec']
    def pod(name,node,target,readonly):
        spec=copy.deepcopy(source)
        spec['nodeSelector']={'kubernetes.io/hostname':node}
        spec['initContainers']=[c for c in spec['initContainers'] if c['name']=='render-config']
        spec['volumes']=[v for v in spec['volumes'] if v['name'] not in ['source-media','data','artifact']]
        spec['volumes'].append({'name':'target','persistentVolumeClaim':{'claimName':target,'readOnly':readonly}})
        c={'name':'tool','image':BASE,'imagePullPolicy':'IfNotPresent','command':['python3','-c','import time; time.sleep(86400)'],
           'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
           'resources':{'requests':{'cpu':'100m','memory':'256Mi'},'limits':{'cpu':'2','memory':'11Gi'}},
           'volumeMounts':[{'name':'target','mountPath':'/target','readOnly':readonly},{'name':'runtime','mountPath':'/run/spindle','readOnly':True}]}
        spec['containers']=[c]
        if not readonly:
            c['resources']['requests']={'cpu':'500m','memory':'6Gi'}
            c['volumeMounts'][1]['readOnly']=False
            key=next(v for v in spec['volumes'] if v['name']=='source-signing-key')
            key['secret']['items'].append({'key':'POSTGRES_PASSWORD','path':'db.password'})
            spec['initContainers'][0]['command'][2] += '; python3 -c '+__import__('shlex').quote("import pathlib,os; p=pathlib.Path('/run/spindle/db.password'); p.write_bytes(pathlib.Path('/source-signing/db.password').read_bytes()); p.chmod(0o400); os.chown(p,10091,10091)")
        if not readonly:
            # New disposable volumes only. Source production media is never mounted.
            spec['securityContext'].update(fsGroup=10091,fsGroupChangePolicy='OnRootMismatch')
            spec['volumes'].append({'name':'restored-source-media','persistentVolumeClaim':{'claimName':'ess-spindle-cold-source-media'}})
            c['volumeMounts'].append({'name':'restored-source-media','mountPath':'/source-media'})
        else:
            spec['volumes'].append({'name':'metadata','emptyDir':{'medium':'Memory','sizeLimit':'16Mi'}})
            c['volumeMounts'].append({'name':'metadata','mountPath':'/metadata'})
            spec['initContainers'].append({'name':'metadata-permissions','image':BASE,'command':['python3','-c',"import os; os.chown('/metadata',10091,10091); os.chmod('/metadata',0o700)"],'securityContext':{'runAsUser':0,'runAsGroup':0,'runAsNonRoot':False,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL'],'add':['CHOWN']}},'volumeMounts':[{'name':'metadata','mountPath':'/metadata'}],'resources':{'requests':{'memory':'16Mi','cpu':'10m'},'limits':{'memory':'64Mi','cpu':'100m'}}})
            # No exporter fsGroup: never recursively rewrite a cold target.
        return {'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'ess','labels':{'spindle.tunaos.org/role':'cold-target-proof'}},'spec':spec}
    items=[]
    for name,size in [('ess-spindle-cold-restore','24Gi'),('ess-spindle-cold-source-media','8Gi')]:
        items.append({'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':name,'namespace':'ess','labels':{'spindle.tunaos.org/role':'cold-target-proof'}},'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'local-path','resources':{'requests':{'storage':size}}}})
    exporter=pod('ess-spindle-cold-export','ip-10-20-1-11','ess-spindle-data',True)
    restore=pod('ess-spindle-cold-restore','ip-10-20-1-12','ess-spindle-cold-restore',False)
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,data in [('exporter.json',exporter),('restore.json',{'apiVersion':'v1','kind':'List','items':items+[restore]})]:
        (out/name).write_text(json.dumps(data,indent=2)+'\n')
    print('Manifests prepared only; root applies after review. Capture fresh disposable PVC UIDs before restore.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','export','restore','validate'])
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--kubeconfig');p.add_argument('--context');p.add_argument('--execute',action='store_true')
    p.add_argument('--prefix');p.add_argument('--source-markers',type=Path);p.add_argument('--source-capture',type=Path)
    p.add_argument('--target-report',type=Path);p.add_argument('--restore-pvc-uid');p.add_argument('--media-pvc-uid')
    p.add_argument('--source-media-selection',choices=['full','local-content'],default='full')
    p.add_argument('--node12-pg-growth-reserve-bytes',type=int,help='Required restore reservation for any concurrent future PG growth; zero only after coordinating root/client_witness')
    p.add_argument('--gate-cli',type=Path,default=ROOT/'target/debug/migrator-cli')
    a=p.parse_args();os.umask(0o077)
    if a.mode=='prepare':return manifests(a.output)
    assert a.execute and a.kubeconfig and a.context and a.source_markers and a.source_capture
    assert a.prefix and a.prefix.startswith('postgres/spindle-cutover/') and '..' not in a.prefix.split('/')
    a.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    assert a.output.stat().st_mode&0o077==0,'evidence directory must be private'
    k=['kubectl','--kubeconfig',a.kubeconfig,'--context',a.context]
    def command(ns,*cmd):return k+['-n',ns,*cmd]
    def read(ns,*cmd):return subprocess.check_output(command(ns,*cmd),timeout=21600)
    def obj(kind,name):return json.loads(read('ess','get',kind,name,'-o','json'))
    def remote(pod,argv,data=None):return remote_exec(command,pod,argv,data)
    def remote_json(pod,argv):return readonly_remote_json(command,pod,argv)
    def private_run(cmd,**kw):
        log=a.output/('command-'+str(time.time_ns())+'-'+str(private_run.count)+'.stderr-private.log');private_run.count+=1
        with log.open('xb') as file:return subprocess.run(cmd,stderr=file,**kw)
    private_run.count=0
    marker=json.loads(a.source_markers.read_text());semantic=marker['synapse']['semantic']
    preserve_local_history=semantic.get('preserve_local_history',False)
    assert isinstance(preserve_local_history,bool),'invalid frozen source scope policy'
    assert CANDIDATE.get('scope')!='joined' or not preserve_local_history,'joined-only candidate cannot validate preservation-mode source'
    scope={r['room_id']:r['room_version'] for r in semantic['exact_retained_room_scope']}
    assert scope
    source_objects=json.loads(a.source_capture.read_text())
    assert isinstance(source_objects,list)
    byname={r['key'].split('/')[-1]:r for r in source_objects}
    assert 'CAPTURE-COMPLETE.json' in byname and 'media.tar' in byname and 'source-restore-markers.json' in byname
    assert hashlib.sha256(a.source_markers.read_bytes()).hexdigest()==byname['source-restore-markers.json']['sha256']
    assert all(r['full_object_reread_verified'] and r['key'].startswith(a.prefix.rstrip('/')+'/source/') for r in source_objects)
    def frozen():
        candidate=obj('deploy','ess-spindle')
        annotations=candidate['metadata'].get('annotations',{})
        assert annotations.get('spindle.tunaos.org/binary-sha256')==SHA,'dormant runtime candidate differs from selected proof pack'
        assert annotations.get('spindle.tunaos.org/revision')==REVISION,'dormant runtime revision differs from selected proof pack'
        for kind,name in [('deploy','ess-haproxy'),('deploy','ess-matrix-authentication-service'),('sts','ess-synapse-main'),('sts','ess-synapse-fed-sender'),('sts','ess-synapse-sliding-sync'),('deploy','ess-spindle')]:
            d=obj(kind,name);assert d['spec']['replicas']==0 and all(d.get('status',{}).get(x,0)==0 for x in ['replicas','readyReplicas'])
        d=obj('job','ess-spindle-import-fresh');assert d.get('status',{}).get('succeeded')==1 and not d.get('status',{}).get('active',0)
        value=read('postgres','exec','deploy/postgres','--','psql','-X','-U','postgres','-d','synapse','-Atc',"SELECT json_build_object('event_rows',count(*),'stream_high_water',max(stream_ordering)) FROM events")
        high=json.loads(value);assert all(high[x]==semantic[x] for x in ['event_rows','stream_high_water'])
        connections=read('postgres','exec','deploy/postgres','--','psql','-X','-U','postgres','-d','postgres','-Atc',"SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND datname IN ('synapse','mas')")
        assert connections.strip()==b'0'
    def gate(report, archived_proof=None):
        proof_path=a.output/'notification-boundary.json'
        expected=json.loads(proof_path.read_text()) if proof_path.exists() else None
        if archived_proof is not None:
            if expected is not None:assert archived_proof==expected,'archived notification provenance differs'
            expected=archived_proof
        proof=require_boundary(report, semantic.get('scope_server_name','reilly.asia'), expected)
        assert report.get('preserve_local_history',False) is preserve_local_history,'checkpoint scope policy differs from frozen source'
        assert set(report['rooms'])==set(scope) and not report['excluded_rooms']
        assert all(report['rooms'][room]['version']==version for room,version in scope.items())
        counts=semantic.get('retained_room_event_rows')
        if counts is not None:
            assert set(counts)==set(scope)
            assert all(report['rooms'][room]['source_events']==count for room,count in counts.items()),'room source event count differs from frozen marker'
        assert sum(r['source_events'] for r in report['rooms'].values())==semantic['retained_event_rows'],'aggregate retained source event count differs'
        path=a.output/'checked-report.json';path.write_text(json.dumps(report)+'\n')
        result=private_run([str(a.gate_cli),'report',str(path),str(len(scope))],stdout=subprocess.PIPE,timeout=30,
                           env={**os.environ,'MIGRATOR_PRESERVE_LOCAL_HISTORY':str(preserve_local_history).lower()})
        assert result.returncode==0
        (a.output/'report-gates.json').write_bytes(result.stdout)
        proof_path.write_text(json.dumps(proof,sort_keys=True)+'\n')
        return proof
    def pipe(producer,consumer,label):
        with (a.output/(label+'-producer-private.log')).open('xb') as logfile:
            proc=subprocess.Popen(producer,stdout=subprocess.PIPE,stderr=logfile)
            try:
                result=private_run(consumer,stdin=proc.stdout,stdout=subprocess.PIPE,timeout=21600)
                proc.stdout.close();code=proc.wait(timeout=30)
                assert code==0 and result.returncode==0,'stream producer or consumer failed'
                return result.stdout
            finally:
                if proc.stdout:proc.stdout.close()
                if proc.poll() is None:
                    primary=sys.exc_info()[0] is not None
                    try:
                        proc.terminate()
                        try:proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=15)
                    except Exception as cleanup_error:
                        (a.output/('cleanup-'+str(time.time_ns())+'.private.log')).write_text(type(cleanup_error).__name__+'\n')
                        if not primary:raise RuntimeError('stream process cleanup failed; private diagnostic retained') from None
    frozen()
    target=obj('pvc','ess-spindle-data');assert target['metadata']['uid']==UID
    assert target['spec']['volumeName']!=obj('pvc','synapse-media-preseed')['spec']['volumeName']
    if a.mode=='export':
        pod='ess-spindle-cold-export';d=obj('pod',pod)
        assert d['metadata']['labels']['spindle.tunaos.org/role']=='cold-target-proof'
        assert d['spec']['containers'][0]['image']==BASE
        assert {v['persistentVolumeClaim']['claimName'] for v in d['spec']['volumes'] if 'persistentVolumeClaim' in v}=={'ess-spindle-data'}
        assert d['spec']['nodeSelector']['kubernetes.io/hostname']=='ip-10-20-1-11'
        assert all(v.get('persistentVolumeClaim',{}).get('readOnly') is True for v in d['spec']['volumes'] if 'persistentVolumeClaim' in v)
        for other in json.loads(read('ess','get','pods','-o','json'))['items']:
            if other['metadata']['name']==pod or other.get('status',{}).get('phase') in ['Succeeded','Failed']:continue
            assert not any(v.get('persistentVolumeClaim',{}).get('claimName')=='ess-spindle-data' for v in other['spec'].get('volumes',[])),'other pod can open target'
        report=remote_json(pod,['cat','/target/import/report.json']);boundary=gate(report)
        files=remote_json(pod,['python3','-c',INVENTORY,'/target']);inventory=json.dumps(files,sort_keys=True).encode()
        assert files[BINARY_REL]['sha256']==SHA
        remote(pod,['python3','-c',"import pathlib,sys; p=pathlib.Path('/metadata/file-manifest.json'); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o400)"],inventory)
        remote(pod,['python3','-c',"import shutil,pathlib; shutil.copyfile('/run/spindle/spindle.toml','/metadata/import.toml'); shutil.copyfile('/run/spindle/source-signing.key','/metadata/source-signing.key'); pathlib.Path('/metadata/import.toml').chmod(0o400); pathlib.Path('/metadata/source-signing.key').chmod(0o400)"])
        metadata_hashes={}
        for name in ['import.toml','source-signing.key','file-manifest.json']:
            metadata_hashes[name]=remote(pod,['sha256sum','/metadata/'+name]).decode().split()[0]
        assert metadata_hashes['source-signing.key']==byname['signing.key']['sha256'],'signing seed differs from frozen source backup'
        identity={'metadata_file_sha256':metadata_hashes,'binary_sha256':SHA,'binary_path':BINARY_REL,'revision':REVISION,'target_pvc_uid':UID,'source_marker_sha256':hashlib.sha256(a.source_markers.read_bytes()).hexdigest(),'room_scope':scope,'notification_boundary':boundary,'cold':True}
        remote(pod,['python3','-c',"import pathlib,sys; p=pathlib.Path('/metadata/identity.json'); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o400)"],json.dumps(identity).encode())
        archive=pipe(command('ess','exec',pod,'--','tar','-C','/target','-cf','-','.', '-C','/','metadata'),command('postgres','exec','-i','spindle-cutover-stream-upload-v2','--','python3','/script/s3-stream.py','--key',a.prefix.rstrip('/')+'/target/actual-store.tar'),'target-export')
        result=json.loads(archive);assert result['full_object_reread_verified']
        frozen();(a.output/'target-object.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'passed':True,'archive':result,'rooms':len(scope),'full_local_spool':False}));return
    assert a.target_report and a.restore_pvc_uid and a.media_pvc_uid
    pod='ess-spindle-cold-restore';d=obj('pod',pod)
    assert d['metadata']['labels']['spindle.tunaos.org/role']=='cold-target-proof' and d['spec']['containers'][0]['image']==BASE
    assert d['spec']['nodeSelector']['kubernetes.io/hostname']=='ip-10-20-1-12'
    expected={'ess-spindle-cold-restore':a.restore_pvc_uid,'ess-spindle-cold-source-media':a.media_pvc_uid}
    for name,uid in expected.items():
        claim=obj('pvc',name);assert claim['metadata']['uid']==uid and claim['metadata']['labels']['spindle.tunaos.org/role']=='cold-target-proof'
        assert claim['spec']['volumeName'] not in [target['spec']['volumeName'],obj('pvc','synapse-media-preseed')['spec']['volumeName']]
    assert {v['persistentVolumeClaim']['claimName'] for v in d['spec']['volumes'] if 'persistentVolumeClaim' in v}==set(expected)
    def download(report):
        assert report['key'].startswith(a.prefix.rstrip('/')+'/') and report['full_object_reread_verified']
        return command('postgres','exec','spindle-cutover-stream-upload-v2','--','python3','-c',DOWNLOAD,'--key',report['key'],'--sha256',report['sha256'],'--bytes',str(report['bytes']))
    archive=json.loads(a.target_report.read_text())
    if a.mode=='restore':
        assert a.node12_pg_growth_reserve_bytes is not None and a.node12_pg_growth_reserve_bytes>=0
        capacity=remote_json(pod,['python3','-c',CAPACITY_READ])
        # imagefs15% is the observed hard threshold; retain an extra1% margin.
        floor=max(8*GIB,(capacity['total']*16+99)//100)
        media_budget=byname['media.tar']['bytes'] if a.source_media_selection=='full' else 256*1024**2
        assert capacity['free']>archive['bytes']+media_budget+floor+a.node12_pg_growth_reserve_bytes,'node12 lacks shared imagefs floor / PG growth margin'
        (a.output/'capacity-plan.json').write_text(json.dumps(dict(capacity,floor_bytes=floor,source_media_budget_bytes=media_budget,pg_growth_reserve_bytes=a.node12_pg_growth_reserve_bytes),indent=2)+'\n')
        pipe(download(archive),command('ess','exec','-i',pod,'--','python3','-c',EXTRACT,'/target'),'target-restore')
        pipe(download(byname['media.tar']),command('ess','exec','-i',pod,'--','python3','-c',EXTRACT,'/source-media',a.source_media_selection),'source-media-restore')
        wanted=remote_json(pod,['cat','/target/metadata/file-manifest.json'])
        actual=remote_json(pod,['python3','-c',INVENTORY,'/target'])
        assert actual==wanted and actual[BINARY_REL]['sha256']==SHA
        identity=remote_json(pod,['cat','/target/metadata/identity.json'])
        assert identity['binary_sha256']==SHA and identity['target_pvc_uid']==UID,'restored candidate identity mismatch'
        assert identity.get('revision',REVISION)==REVISION and identity.get('binary_path',BINARY_REL)==BINARY_REL,'restored candidate revision/path mismatch'
        for name,digest in identity['metadata_file_sha256'].items():
            assert remote(pod,['sha256sum','/target/metadata/'+name]).decode().split()[0]==digest
        assert identity['metadata_file_sha256']['source-signing.key']==byname['signing.key']['sha256']
        assert identity['source_marker_sha256']==hashlib.sha256(a.source_markers.read_bytes()).hexdigest() and identity['room_scope']==scope
        assert isinstance(identity.get('notification_boundary'),dict),'archive lacks fresh notification provenance'
        gate(remote_json(pod,['cat','/target/import/report.json']),identity['notification_boundary'])
        (a.output/'restore-passed.json').write_text(json.dumps({'passed':True,'files':len(actual),'source_media_stream_hash_verified':True,'source_media_selection':a.source_media_selection,'room_scope':scope,'source_marker_sha256':identity['source_marker_sha256'],'original_validation':remote_json(pod,['cat','/target/import/report.json'])['validation']})+'\n')
        print('Independent full-file restore and frozen source media extraction passed; validation still required');return
    assert json.loads((a.output/'restore-passed.json').read_text())['passed']
    # Copy preserved import config to private tmpfs and change only target store path.
    remote(pod,['python3','-c',"import pathlib; p=pathlib.Path('/run/spindle/validate.toml'); p.write_text(pathlib.Path('/target/metadata/import.toml').read_text().replace('/var/lib/spindle-data/store','/target/store')); p.chmod(0o400)"])
    cmd=command('ess','exec',pod,'--','/bin/sh','-ec','test "$(sha256sum '+BINARY_TARGET+' | cut -d " " -f 1)" = '+SHA+'; export SPINDLE_SYNAPSE_PASSWORD="$(cat /run/spindle/db.password)"; exec '+BINARY_TARGET+' import-synapse /run/spindle/validate.toml "host=postgres.postgres.svc.cluster.local port=5432 user=synapse_user dbname=synapse sslmode=prefer" --media /source-media/media_store --checkpoint /target/import/report.json --validate-only'+(' --preserve-local-history' if preserve_local_history else ''))
    with (a.output/('validation-stdout-'+str(time.time_ns())+'.private.log')).open('xb') as stdout_log:
        result=private_run(cmd,stdout=stdout_log,timeout=21600)
    assert result.returncode==0
    checked=remote_json(pod,['cat','/target/import/report.json']);gate(checked)
    original=json.loads((a.output/'restore-passed.json').read_text())['original_validation']
    assert set(checked['validation']['domains'])==set(original['domains'])
    assert all(checked['validation']['domains'][name][0]==rows[0] for name,rows in original['domains'].items())
    assert checked['validation']['domains']['media'][0]>0,'media validation must compare real source files'
    frozen();(a.output/'validate-passed.json').write_text(json.dumps({'passed':True,'rooms':len(scope),'exact_binary_sha256':SHA,'source_original_frozen':True,'independent_restored_source_media':True})+'\n')
    print('Independent restore read-back validation passed; readiness/client/signing witnesses remain root gates')

if __name__=='__main__':
    main()
