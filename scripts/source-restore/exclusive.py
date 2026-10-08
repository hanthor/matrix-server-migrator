"""Fail-closed exclusive restore guard for actual ESS target writers."""
import json
import subprocess

NAMESPACE='ess'
IMPORT_JOB='ess-spindle-import-fresh'
RUNTIME='ess-spindle'
TARGET_PVC='ess-spindle-data'

def validate(job, deployment, pods):
    if job is not None:
        # Root keeps the job absent until independent restore is finished.
        # A present suspended/completed job can be changed into another writer.
        raise RuntimeError('exclusive restore requires fresh ESS import Job absent')
    if deployment is None or deployment.get('spec',{}).get('replicas',1)!=0:
        raise RuntimeError('exclusive restore requires ESS target runtime replicas zero')
    status=deployment.get('status',{})
    if any(status.get(k,0) for k in ['replicas','readyReplicas','availableReplicas','updatedReplicas']):
        raise RuntimeError('exclusive restore requires ESS target runtime fully stopped')
    for pod in pods:
        mounts=any(v.get('persistentVolumeClaim',{}).get('claimName')==TARGET_PVC for v in pod.get('spec',{}).get('volumes',[]))
        owned_import=any(o.get('kind')=='Job' and o.get('name')==IMPORT_JOB for o in pod.get('metadata',{}).get('ownerReferences',[]))
        if not (mounts or owned_import):continue
        state=pod.get('status',{})
        statuses=state.get('containerStatuses',[])+state.get('initContainerStatuses',[])+state.get('ephemeralContainerStatuses',[])
        terminal=state.get('phase') in ['Succeeded','Failed'] and statuses and all('terminated' in c.get('state',{}) for c in statuses)
        if not terminal:
            raise RuntimeError('exclusive restore found pod able to open ESS target data')
    return {'namespace':NAMESPACE,'import_job':IMPORT_JOB,'import_job_absent':True,'runtime_deployment':RUNTIME,'runtime_replicas':0,'target_pvc':TARGET_PVC,'target_writer_pods':0}

def check(kubectl):
    def get(kind,name=None):
        command=kubectl+['-n',NAMESPACE,'get',kind]+([name] if name else [])+['-o','json']
        if name:command+=['--ignore-not-found=true']
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60)
        if result.returncode:raise RuntimeError('ESS exclusive capacity workload inspection failed')
        return json.loads(result.stdout) if result.stdout.strip() else None
    return validate(get('job',IMPORT_JOB),get('deployment',RUNTIME),get('pods')['items'])
