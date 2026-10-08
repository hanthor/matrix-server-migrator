#!/usr/bin/env python3
"""Read-only preparation of stateless maintenance resources and guarded routes."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import yaml

NAME='ess-spindle-maintenance'
SOURCES={'ess-synapse','ess-matrix-authentication-service'}
IMAGE='docker.io/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f'

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ingresses',type=Path,required=True)
    parser.add_argument('--helm-manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    def save(name,value):
        (args.output/name).write_text(json.dumps(value,indent=2)+'\n')
    original=json.loads(args.ingresses.read_text());save('original-ingresses-private.json',original)
    helm={o['metadata']['name']:o for o in yaml.safe_load_all(args.helm_manifest.read_text()) if o and o.get('kind')=='Ingress'}
    selected=[];hosts=set();patches=[];targets=[];changes=[]
    for ingress in original['items']:
        name=ingress['metadata']['name'];ops=[];inverse=[];target=copy.deepcopy(ingress)
        for ri,rule in enumerate(ingress.get('spec',{}).get('rules',[])):
            for pi,path in enumerate(rule.get('http',{}).get('paths',[])):
                service=path['backend'].get('service',{})
                if service.get('name') not in SOURCES:
                    continue
                pointer=f'/spec/rules/{ri}/http/paths/{pi}/backend/service'
                replacement={'name':NAME,'port':{'name':'http'}}
                ops += [{'op':'test','path':pointer,'value':service},{'op':'replace','path':pointer,'value':replacement}]
                inverse += [{'op':'test','path':pointer,'value':replacement},{'op':'replace','path':pointer,'value':service}]
                target['spec']['rules'][ri]['http']['paths'][pi]['backend']['service']=replacement
                hosts.add(rule['host']);changes.append({'ingress':name,'host':rule['host'],'path':path['path'],'source_service':service['name']})
        if not ops:
            continue
        if name not in helm or helm[name]['spec']!=ingress['spec']:
            raise RuntimeError('live ingress spec differs from reviewed Helm desired state')
        uid=ingress['metadata']['uid']
        patch=[{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/spec','value':ingress['spec']}]+ops
        rollback=[{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/spec','value':target['spec']}]+inverse
        save(name+'-maintenance.patch.json',patch);save(name+'-restore.patch.json',rollback)
        selected.append(ingress);targets.append(target);patches.append({'ingress':name,'uid':uid,'switch_patch':name+'-maintenance.patch.json','restore_patch':name+'-restore.patch.json'})
    if {i['metadata']['name'] for i in selected} != {'ess-synapse','ess-matrix-authentication-service'} or len(changes)!=15:
        raise RuntimeError('unexpected source ingress routing shape')
    save('maintenance-ingresses-private.json',{'apiVersion':'v1','kind':'List','items':targets})
    labels={'app.kubernetes.io/name':'spindle-maintenance','app.kubernetes.io/instance':NAME}
    code=Path(__file__).with_name('maintenance-server.py').read_text()
    cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':NAME+'-v1','namespace':'ess'},'immutable':True,'data':{'server.py':code}}
    service={'apiVersion':'v1','kind':'Service','metadata':{'name':NAME,'namespace':'ess'},'spec':{'selector':labels,'ports':[{'name':'http','port':8008,'targetPort':'http'}]}}
    deployment={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':NAME,'namespace':'ess'},'spec':{'replicas':0,'selector':{'matchLabels':labels},'template':{'metadata':{'labels':labels},'spec':{
        'automountServiceAccountToken':False,'enableServiceLinks':False,'dnsPolicy':'None','dnsConfig':{'nameservers':['127.0.0.1']},
        'securityContext':{'runAsUser':10093,'runAsGroup':10093,'runAsNonRoot':True,'seccompProfile':{'type':'RuntimeDefault'}},
        'containers':[{'name':'maintenance','image':IMAGE,'command':['python3','-B','/app/server.py'],
            'env':[{'name':'PUBLIC_HOSTS','value':','.join(sorted(hosts))}],
            'ports':[{'name':'http','containerPort':8008}],
            'resources':{'requests':{'cpu':'25m','memory':'32Mi'},'limits':{'cpu':'250m','memory':'64Mi'}},
            'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
            'volumeMounts':[{'name':'code','mountPath':'/app','readOnly':True}],
            'readinessProbe':{'httpGet':{'path':'/ready','port':'http','httpHeaders':[{'name':'Host','value':'maintenance.internal'}]},'periodSeconds':5,'timeoutSeconds':2},
            'livenessProbe':{'httpGet':{'path':'/ready','port':'http','httpHeaders':[{'name':'Host','value':'maintenance.internal'}]},'periodSeconds':10,'timeoutSeconds':2}}],
        'volumes':[{'name':'code','configMap':{'name':NAME+'-v1','defaultMode':0o444}}]}}}}
    save('resources.json',{'apiVersion':'v1','kind':'List','items':[cm,service,deployment]})
    save('plan.json',{'deployment_replicas':0,'namespace':'ess','service':NAME,'pinned_image':IMAGE,
        'source_services':sorted(SOURCES),'routes':changes,'patches':patches,'script_sha256':hashlib.sha256(code.encode()).hexdigest(),
        'original_snapshot_sha256':hashlib.sha256(args.ingresses.read_bytes()).hexdigest(),
        'helm_desired_manifest_sha256':hashlib.sha256(args.helm_manifest.read_bytes()).hexdigest(),
        'helm_specs_match':True,'production_changes_applied':False})

if __name__=='__main__':main()
