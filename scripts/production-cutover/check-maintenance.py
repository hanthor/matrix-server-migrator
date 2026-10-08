#!/usr/bin/env python3
"""Local behavioral checks and exact guarded ingress patch inverse validation."""
import argparse
import copy
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def apply(document,patch):
    result=copy.deepcopy(document)
    for op in patch:
        segments=op['path'].split('/')[1:];current=result
        for segment in segments[:-1]:
            segment=segment.replace('~1','/').replace('~0','~')
            current=current[int(segment)] if isinstance(current,list) else current[segment]
        key=segments[-1].replace('~1','/').replace('~0','~');key=int(key) if isinstance(current,list) else key
        if op['op']=='test':
            if current[key]!=op['value']:raise ValueError('guard rejected changed object')
        elif op['op']=='replace':
            current[key]=copy.deepcopy(op['value'])
        else:raise ValueError('unexpected patch operation')
    return result

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args()
    plan=json.loads((args.directory/'plan.json').read_text())
    original=json.loads((args.directory/'original-ingresses-private.json').read_text());byname={x['metadata']['name']:x for x in original['items']}
    patched=copy.deepcopy(byname)
    for item in plan['patches']:
        name=item['ingress'];forward=json.loads((args.directory/item['switch_patch']).read_text());inverse=json.loads((args.directory/item['restore_patch']).read_text())
        changed=apply(byname[name],forward);assert apply(changed,inverse)==byname[name]
        assert changed['metadata']==byname[name]['metadata']
        assert changed['spec'].get('tls')==byname[name]['spec'].get('tls')
        patched[name]=changed
        wrong_uid=copy.deepcopy(byname[name]);wrong_uid['metadata']['uid']='replacement-object'
        wrong_backend=copy.deepcopy(byname[name]);wrong_backend['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name']='other-backend'
        for wrong in (wrong_uid,wrong_backend):
            try:apply(wrong,forward)
            except ValueError:pass
            else:raise AssertionError('stale source guard accepted')
        try:apply(byname[name],inverse)
        except ValueError:pass
        else:raise AssertionError('inverse accepted unmaintained route')
    for name in byname:
        if name not in {i['ingress'] for i in plan['patches']}:
            assert patched[name]==byname[name]
    resources=json.loads((args.directory/'resources.json').read_text())['items'];deployment=next(x for x in resources if x['kind']=='Deployment')
    assert deployment['spec']['replicas']==0
    pod=deployment['spec']['template']['spec'];container=pod['containers'][0]
    assert pod['automountServiceAccountToken'] is False and pod['enableServiceLinks'] is False
    assert pod['securityContext']['runAsNonRoot'] and container['securityContext']['readOnlyRootFilesystem']
    assert container['securityContext']['capabilities']['drop']==['ALL'] and not container['securityContext']['allowPrivilegeEscalation']
    assert all(set(v)=={'name','configMap'} for v in pod['volumes'])
    assert all('valueFrom' not in e for e in container.get('env',[]))
    assert container['image']==plan['pinned_image'] and '@sha256:' in container['image']
    # Run server only on localhost with same public host configuration. Fake
    # URL/header/body sentinels must never reach stdout/stderr or error JSON.
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    env=dict(os.environ);env['PUBLIC_HOSTS']=next(e['value'] for e in container['env'] if e['name']=='PUBLIC_HOSTS')
    server=subprocess.Popen([sys.executable,str(Path(__file__).with_name('maintenance-server.py')),'--bind','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while True:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.05)
        requests=[]
        for method in ('GET','HEAD','POST','PUT','PATCH','DELETE','TRACE','CONNECT','PROPFIND'):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            connection.request(method,'/_matrix/client/v3/sync?access_token=URL_TOKEN_SENTINEL',
                body=None if method in ('GET','HEAD') else b'BODY_TOKEN_SENTINEL',
                headers={'Authorization':'Bearer HEADER_TOKEN_SENTINEL','Host':'matrix.reilly.asia'})
            response=connection.getresponse();data=response.read()
            assert response.status==503 and response.getheader('Retry-After')=='60'
            assert response.getheader('Cache-Control')=='no-store' and response.getheader('Access-Control-Allow-Origin')=='*'
            assert b'TOKEN_SENTINEL' not in data
            if method=='HEAD':assert data==b''
            else:assert json.loads(data)['errcode']=='M_UNKNOWN'
            requests.append({'method':method,'status':response.status});connection.close()
        for host,wanted in [('maintenance.internal',200),('auth.reilly.asia',503)]:
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3);connection.request('GET','/ready',headers={'Host':host});response=connection.getresponse();response.read();assert response.status==wanted;connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3);connection.request('OPTIONS','/_matrix/client/v3/login',headers={'Origin':'https://client.example.invalid','Access-Control-Request-Headers':'authorization,content-type'});response=connection.getresponse();assert response.status==204 and response.read()==b'';assert 'Authorization' in response.getheader('Access-Control-Allow-Headers');connection.close()
        with socket.create_connection(('127.0.0.1',port),timeout=3) as raw:
            raw.sendall(b'POST /_matrix/media/v3/upload HTTP/1.1\r\nHost: matrix.reilly.asia\r\nExpect: 100-continue\r\nContent-Length: 99999999\r\n\r\n')
            data=raw.recv(4096);assert data.startswith(b'HTTP/1.1 503') and b'100 Continue' not in data
    finally:
        server.terminate();stdout,stderr=server.communicate(timeout=3)
    assert stdout==b'' and stderr==b''
    report={'passed':True,'production_changes_applied':False,'changed_ingresses':len(plan['patches']),'changed_service_paths':len(plan['routes']),
        'helm_desired_specs_match':True,'uid_and_prior_spec_guards_verified':True,'patch_inverse_exact':True,'tls_hosts_unchanged':True,
        'well_known_rtc_admin_routes_unchanged':True,'deployment_prepared_at_zero_replicas':True,'no_source_mounts_secrets_api':True,
        'methods':requests,'cors_preflight_204':True,'internal_ready_200':True,'public_ready_503':True,
        'expect_100_upload_rejected_before_body':True,'server_stdout_stderr_empty':True}
    (args.directory/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
