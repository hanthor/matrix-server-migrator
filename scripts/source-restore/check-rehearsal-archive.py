#!/usr/bin/env python3
"""Verify owned backup archive TOC and bounded full S3 hash; no restore/write."""
import argparse,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--backup-directory',required=True,type=Path);p.add_argument('--decode',action='store_true');a=p.parse_args();os.umask(0o077)
label='archive-decode' if a.decode else 'archive-check'
r=json.loads((a.backup_directory/'synapse.dump.report.json').read_text())
assert r['key'].startswith('postgres/spindle-cutover/rehearsal-source-') and r['key'].endswith('/synapse.dump')
assert r['full_object_reread_verified'] is True and r['overwrite_prevented'] is True
k=['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration']
script=Path(__file__).with_name('s3-download.py').read_text()
status={'complete':False,'passed':False,'full_local_spool':False,'database_restored':False,'key':r['key']}
def save(): (a.backup_directory/(label+'.json')).write_text(json.dumps(status,indent=2))
save();source=None;target=None
try:
 with (a.backup_directory/(label+'-download.stderr.log')).open('xb') as dl,(a.backup_directory/(label+'-parser.stderr.log')).open('xb') as pl,(a.backup_directory/(label+'-stdout.private.log')).open('xb') as toc:
  source=subprocess.Popen(k+['-n','postgres','exec','spindle-cutover-stream-upload-v2','--','python3','-c',script,'--key',r['key'],'--sha256',r['sha256'],'--bytes',str(r['bytes'])],stdout=subprocess.PIPE,stderr=dl)
  target=subprocess.Popen(k+['-n','spindle-rehearsal','exec','-i','rehearsal-pg-6bbb6fb9cc-qp4fz','--']+(['pg_restore','--file=/dev/null'] if a.decode else ['bash','-c','pg_restore --list; archive_status=$?; cat >/dev/null; exit "$archive_status"']),stdin=subprocess.PIPE,stdout=toc,stderr=pl)
  open_pipe=True
  while True:
   chunk=source.stdout.read(1024*1024)
   if not chunk:break
   if open_pipe:
    try:target.stdin.write(chunk);target.stdin.flush()
    except BrokenPipeError:open_pipe=False
  try:target.stdin.close()
  except BrokenPipeError:pass
  source.stdout.close()
  if source.wait() or target.wait():raise RuntimeError('archive parser or bounded full reread failed')
 status.update(complete=True,passed=True,archive_toc_parsed=not a.decode,full_archive_decoded=a.decode,sql_executed=False,full_object_hash_verified_again=True,stream_buffer_bytes=1024*1024)
except Exception as error:
 status.update(complete=True,passed=False,error_type=type(error).__name__,error='Archive verification failed; raw logs private')
finally:
 for proc in [source,target]:
  if proc and proc.poll() is None:
   proc.terminate()
   try:proc.wait(timeout=15)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
 save()
if not status['passed']:raise SystemExit('Archive verification failed; inspect private status')
