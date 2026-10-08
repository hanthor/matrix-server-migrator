#!/usr/bin/env python3
"""Optional owned production canaries; root executes only after rehearsal gates.

Default --inspect performs metadata reads only. Seed creates exactly two new
owned MAS users, four device-scoped sessions, one private encrypted room and
server-side key backups through the separately built SDK adapter. Credentials
remain in mode-0600 files; no real user's account/session is used.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

MAS = 'https://auth.reilly.asia'
SOURCE = 'https://matrix.reilly.asia'
ADMIN_CLIENT = '0000000000000000000SYNAPSE'
K = ['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration',
     '--context','admin@aws-migration','-n','ess']


def private(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:json.dump(value,file,indent=2);file.write('\n')


def call(base,method,path,body=None,token=None,basic=None,form=None):
    headers={}
    if token:headers['Authorization']='Bearer '+token
    if basic:headers['Authorization']='Basic '+base64.b64encode((':'.join(basic)).encode()).decode()
    if form is not None:
        data=urllib.parse.urlencode(form).encode();headers['Content-Type']='application/x-www-form-urlencoded'
    elif body is not None:
        data=json.dumps(body).encode();headers['Content-Type']='application/json'
    else:data=None
    request=urllib.request.Request(base+path,method=method,headers=headers,data=data)
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            payload=response.read()
            return response.status,json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        # Never include server error bodies, which may contain credentials.
        if error.code==404:return 404,None
        raise RuntimeError('canary request failed: '+method+' '+path+' HTTP '+str(error.code)) from None


def secret_metadata(name):
    return json.loads(subprocess.check_output(K+['get','secret',name,'-o','json']))


def admin_token():
    secret=secret_metadata('ess-generated')
    credential=base64.b64decode(secret['data']['MAS_SYNAPSE_OIDC_CLIENT_SECRET']).decode()
    code,value=call(MAS,'POST','/oauth2/token',basic=(ADMIN_CLIENT,credential),
                    form={'grant_type':'client_credentials','scope':'urn:mas:admin'})
    assert code==200
    return value['access_token']


def sdk(binary,command,args,output,prefix):
    env=os.environ.copy();env['CANARY_PREFIX']=prefix
    result=subprocess.run([str(binary),command]+args,env=env,capture_output=True,timeout=1200)
    # Capture SDK output privately and expose only verdict/counts to operators.
    (output/(command+'-'+str(time.time_ns())+'.stdout')).write_bytes(result.stdout)
    (output/(command+'-'+str(time.time_ns())+'.stderr')).write_bytes(result.stderr)
    if result.returncode:raise RuntimeError('canary SDK '+command+' failed; private logs retained')
    return json.loads(result.stdout)


def require_gates(path):
    gates=json.loads(path.read_text())
    assert all(gates.get(key) is True for key in ['passed','import_store','client_e2ee','federation']), \
        'root-signed aggregate rehearsal gate file has not passed'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['inspect','seed','verify','revoke'])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--sdk',type=Path)
    parser.add_argument('--rehearsal-gates',type=Path)
    parser.add_argument('--target-url',help='Root-owned private final target URL or final public Matrix URL')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    if args.mode=='inspect':
        values=secret_metadata('ess-matrix-authentication-service')
        generated=secret_metadata('ess-generated')
        print(json.dumps({'admin_policy_fragment_present':'user-0-adminClient' in values['data'],
                          'existing_oidc_client_credential_present':'MAS_SYNAPSE_OIDC_CLIENT_SECRET' in generated['data'],
                          'admin_client_id':ADMIN_CLIENT,'production_changed':False,
                          'oauth_grant_not_requested':True}))
        return
    assert args.execute,'root must explicitly select execution after reviewing this optional canary'
    assert args.output is not None
    if args.mode=='seed':
        assert args.rehearsal_gates is not None and args.sdk is not None and args.sdk.is_file()
        require_gates(args.rehearsal_gates)
        args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
        prefix='spindle-cutover-'+time.strftime('%Y%m%d',time.gmtime())+'-'+os.urandom(4).hex()
        assert re.fullmatch(r'spindle-cutover-[0-9]{8}-[0-9a-f]{8}',prefix)
        state={'prefix':prefix,'sessions':[],'users':{},'server_name':'reilly.asia',
               'complete':False,'production_canaries_created':False,'scope':'only owned synthetic users/room'}
        private(args.output/'state.json',state)
        sessions=args.output/'sessions';sessions.mkdir(mode=0o700)
        token=admin_token()
        for key in ['a','b']:
            username=prefix+'-'+key
            code,_=call(MAS,'GET','/api/admin/v1/users/by-username/'+username,token=token)
            assert code==404,'refusing to use or change a preexisting account'
            code,user=call(MAS,'POST','/api/admin/v1/users',token=token,
                           body={'username':username,'skip_homeserver_check':False})
            assert code in [200,201]
            actor=user['data']['id'];state['users'][key]={'username':username,'actor_id':actor,'mxid':'@'+username+':reilly.asia'}
            (args.output/'state.json').write_text(json.dumps(state,indent=2)+'\n')
            for phase in ['seed','recovery']:
                device='CUTOVER'+os.urandom(8).hex().upper()
                scope='urn:matrix:org.matrix.msc2967.client:api:* urn:matrix:org.matrix.msc2967.client:device:'+device
                code,value=call(MAS,'POST','/api/admin/v1/personal-sessions',token=token,
                                body={'actor_user_id':actor,'human_name':prefix+' '+phase+' '+key,
                                      'scope':scope,'expires_in':7*24*3600})
                assert code in [200,201]
                credential=value['data']['attributes']['access_token']
                sid=value['data']['id'];state['sessions'].append({'sid':sid,'key':key,'phase':phase,'device':device})
                (args.output/'state.json').write_text(json.dumps(state,indent=2)+'\n')
                private(sessions/('session-'+phase+'-'+key+'.json'),
                        {'user_id':state['users'][key]['mxid'],'device_id':device,
                         'access_token':credential,'refresh_token':None})
            (args.output/'state.json').write_text(json.dumps(state,indent=2)+'\n')
        seed_output=args.output/'sdk';seed_output.mkdir(mode=0o700)
        summary=sdk(args.sdk,'seed',['--homeserver',SOURCE,'--server-name','reilly.asia','--existing-users',
                    '--secrets-dir',str(sessions),'--out',str(seed_output)],args.output,prefix)
        state.update(complete=True,production_canaries_created=True,
                     sdk_binary_sha256=hashlib.sha256(args.sdk.read_bytes()).hexdigest(),seed_summary=summary)
        (args.output/'state.json').write_text(json.dumps(state,indent=2)+'\n')
        print(json.dumps({'canary_seed_passed':True,'prefix':prefix,'owned_users':2,
                          'private_encrypted_rooms':1,'frozen_source_inventory_must_be_refreshed':True}))
        return
    state=json.loads((args.output/'state.json').read_text());prefix=state['prefix']
    assert re.fullmatch(r'spindle-cutover-[0-9]{8}-[0-9a-f]{8}',prefix)
    assert all(user['username']==prefix+'-'+key for key,user in state['users'].items())
    if args.mode=='revoke':
        token=admin_token()
        for session in state['sessions']:
            code,_=call(MAS,'POST','/api/admin/v1/personal-sessions/'+session['sid']+'/revoke',token=token)
            assert code in [200,201,204]
        print(json.dumps({'owned_sessions_revoked':len(state['sessions']),'real_user_sessions_changed':False}))
        return
    assert state['complete'] and args.sdk is not None and args.sdk.is_file() and args.target_url
    assert hashlib.sha256(args.sdk.read_bytes()).hexdigest()==state['sdk_binary_sha256']
    target=urllib.parse.urlparse(args.target_url)
    assert target.scheme in ['http','https'] and not target.username and not target.password
    credentials=json.loads((args.output/'sdk/credentials.json').read_text())
    manifest=args.output/'sdk/manifest.json'
    rooms=json.loads(manifest.read_text())['rooms']
    result={'passed':False,'original_issued_sessions':{},'fresh_device_backup_recovery':{}}
    for key in ['a','b']:
        original=json.loads((args.output/('sessions/session-seed-'+key+'.json')).read_text())
        code,who=call(args.target_url,'GET','/_matrix/client/v3/account/whoami',token=original['access_token'])
        assert code==200 and who['user_id']==state['users'][key]['mxid'] and who['device_id']==original['device_id']
        code,joined=call(args.target_url,'GET','/_matrix/client/v3/joined_rooms',token=original['access_token'])
        assert code==200 and all(room['room_id'] in joined['joined_rooms'] for room in rooms)
        result['original_issued_sessions'][key]={'same_mxid_and_device':True,'private_room_membership_preserved':True}
        recovery_path=args.output/('recovery-key-'+key)
        fd=os.open(recovery_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as file:file.write(credentials['recovery-key-'+key])
        report=sdk(args.sdk,'verify',['--homeserver',args.target_url,'--user',key,'--manifest',str(manifest),
                    '--session-file',str(args.output/('sessions/session-recovery-'+key+'.json')),
                    '--recovery-key-file',str(recovery_path)],args.output,prefix)
        assert report['pass'] and report['recovery_ok'] and report['own_device_cross_signed']
        assert report['summary']['failures']==0 and report['summary']['expected_readable']>0
        result['fresh_device_backup_recovery'][key]=report['summary']
    result['passed']=True
    private(args.output/('verify-'+str(time.time_ns())+'.json'),result)
    print(json.dumps(result))


if __name__=='__main__':main()
