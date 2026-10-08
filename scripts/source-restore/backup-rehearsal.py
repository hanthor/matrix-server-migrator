#!/usr/bin/env python3
"""Back up only owned frozen rehearsal synapse with bounded verified S3 streams."""
import json, os, subprocess, time
from pathlib import Path
from markers import Collector

ROOT=Path('/home/ubuntu/dev/spindle-migrator')
K=['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration']
POD='rehearsal-pg-6bbb6fb9cc-qp4fz'
PREFIX='postgres/spindle-cutover/rehearsal-source-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
OUTPUT=ROOT/'artifacts'/('rehearsal-source-backup-'+str(int(time.time())))
os.umask(0o077);OUTPUT.mkdir(mode=0o700)
STATE={'prefix':PREFIX,'output':str(OUTPUT),'namespace':'spindle-rehearsal','pod':POD,'database':'synapse','complete':False,'passed':False,'production_changed':False,'database_dropped':False,'full_local_spool':False}
def save(phase,**kw):
 STATE.update(phase=phase,updated_at=time.time(),**kw);tmp=OUTPUT/'status.tmp';tmp.write_text(json.dumps(STATE,indent=2));tmp.replace(OUTPUT/'status.json')
def metadata(command):
 with (OUTPUT/('control-'+str(time.time_ns())+'.stderr.log')).open('xb') as log:
  r=subprocess.run(K+command,stdout=subprocess.PIPE,stderr=log,check=True)
 return r.stdout
REPORTS=[]
def upload(name,data=None,source=None):
 key=PREFIX+'/'+name
 with (OUTPUT/(name+'.producer.stderr.log')).open('xb') as plog,(OUTPUT/(name+'.uploader.stderr.log')).open('xb') as ulog,(OUTPUT/(name+'.report.json')).open('xb') as out:
  producer=subprocess.Popen(K+source,stdout=subprocess.PIPE,stderr=plog) if source else None
  receiver=subprocess.Popen(K+['-n','postgres','exec','-i','spindle-cutover-stream-upload-v2','--','python3','/script/s3-stream.py','--key',key],stdin=producer.stdout if producer else subprocess.PIPE,stdout=out,stderr=ulog)
  if producer: producer.stdout.close()
  try:
   if producer:
    while receiver.poll() is None or producer.poll() is None:
     if receiver.poll() not in (None,0) or producer.poll() not in (None,0): raise RuntimeError('stream failed')
     time.sleep(10)
    if producer.returncode or receiver.returncode: raise RuntimeError('stream failed')
   else:
    receiver.communicate(data,timeout=600)
    if receiver.returncode: raise RuntimeError('metadata upload failed')
  finally:
   for proc in [producer,receiver]:
    if proc and proc.poll() is None:
     proc.terminate()
     try:proc.wait(timeout=15)
     except subprocess.TimeoutExpired:proc.kill();proc.wait()
 report=json.loads((OUTPUT/(name+'.report.json')).read_text())
 if report.get('key')!=key or report.get('full_object_reread_verified') is not True or report.get('overwrite_prevented') is not True:raise RuntimeError('upload verification absent')
 REPORTS.append(report);(OUTPUT/'objects.json').write_text(json.dumps(REPORTS,indent=2));return report
try:
 save('recording_owned_database_identity')
 uid=metadata(['-n','spindle-rehearsal','get','pod',POD,'-o','jsonpath={.metadata.uid}']).decode()
 identity=json.loads(metadata(['-n','spindle-rehearsal','exec',POD,'--','psql','-X','-U','postgres','-d','synapse','-Atq','-v','ON_ERROR_STOP=1','-c',"SELECT json_build_object('database',current_database(),'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()),'database_bytes',pg_database_size(current_database()),'server_version',current_setting('server_version'),'data_directory',current_setting('data_directory'),'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),'writer_connections',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend' AND query !~* '^\\s*(select|show|with|set|begin|commit)' AND state='active')); "]))
 if identity['database']!='synapse' or identity['writer_connections']!=0:raise RuntimeError('frozen owned database gate failed')
 identity.update(namespace='spindle-rehearsal',pod=POD,pod_uid=uid,context='admin@aws-migration');(OUTPUT/'identity.json').write_text(json.dumps(identity,indent=2))
 collector=Collector('/home/ubuntu/.kube/config-aws-migration','admin@aws-migration','spindle-rehearsal',POD,OUTPUT/'marker-private-logs')
 save('collecting_frozen_markers_before');before=collector.collect('synapse',synapse=True);(OUTPUT/'markers-before.json').write_text(json.dumps(before,indent=2))
 save('streaming_owned_rehearsal_dump');upload('synapse.dump',source=['-n','spindle-rehearsal','exec',POD,'--','pg_dump','-U','postgres','-Fc','synapse'])
 save('collecting_frozen_markers_after');after=collector.collect('synapse',synapse=True);(OUTPUT/'markers-after.json').write_text(json.dumps(after,indent=2))
 if before['semantic']!=after['semantic']:raise RuntimeError('frozen source markers changed')
 for name in ['identity.json','markers-before.json','markers-after.json']:upload(name,data=(OUTPUT/name).read_bytes())
 completion={'status':'owned_rehearsal_source_backup_complete','source':identity,'source_high_water':{k:before['semantic'][k] for k in ['event_rows','stream_high_water']},'frozen_semantics_stable':True,'full_local_spool':False,'objects':REPORTS.copy()}
 (OUTPUT/'manifest.json').write_text(json.dumps(completion,indent=2));upload('CAPTURE-COMPLETE.json',data=(OUTPUT/'manifest.json').read_bytes())
 save('owned_rehearsal_backup_verified',complete=True,passed=True,frozen_semantics_stable=True,source_high_water=completion['source_high_water'])
except Exception as error:
 save('failed',complete=True,passed=False,error_type=type(error).__name__,error='Owned rehearsal backup failed; raw diagnostics private',private_logs_directory=str(OUTPUT))
 raise SystemExit('Owned rehearsal backup failed; inspect private status') from None
