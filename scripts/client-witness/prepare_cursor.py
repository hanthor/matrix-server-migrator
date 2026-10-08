#!/usr/bin/env python3
"""Read-only source-derived Synapse cursors; never claims cached owner tokens."""
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source-restore'))
from markers import Collector
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/reilly-2026-10-06';os.umask(0o077)
c=Collector('/home/ubuntu/.kube/config-aws-migration','admin@aws-migration','spindle-rehearsal','rehearsal-pg-6bbb6fb9cc-qp4fz',OUT/'cursor-source-private-logs')
m=json.loads((OUT/'client-witness-fixture-material/manifest.json').read_text())
columns=c.sql('synapse_dark',"SELECT json_agg(table_name||'.'||column_name) FROM information_schema.columns WHERE table_schema='public';")
streams={'presence':('presence_stream','stream_id'),'receipt':('receipts_linearized','stream_id'),'account_data':('account_data','stream_id'),'push_rules':('push_rules_stream','stream_id'),'to_device':('device_inbox','stream_id'),'device_list':('device_lists_stream','stream_id'),'un_partial_stated_rooms':('un_partial_stated_room_stream','stream_id'),'thread_subscriptions':('thread_subscriptions','stream_id'),'sticky_events':('sticky_events','stream_id'),'quarantined_media':('quarantined_media','stream_id')}
positions={'room':c.sql('synapse_dark','SELECT to_json(max(stream_ordering)) FROM events;'),'typing':0,'groups':0};absent=[]
for kind,(table,column) in streams.items():
 if table+'.'+column not in columns:positions[kind]=0;absent.append(kind)
 else:positions[kind]=c.sql('synapse_dark',f'SELECT to_json(coalesce(max({column}),0)) FROM {table};')
anchors=[]
for room in sorted({s['room_id'] for s in m['samples']}):
 samples=[s for s in m['samples'] if s['room_id']==room];event_ids=[s['event_id'] for s in samples];quote=lambda x:"'"+x.replace("'","''")+"'"
 rows=c.sql('synapse_dark',"SELECT json_agg(json_build_object('event_id',event_id,'depth',topological_ordering,'stream',stream_ordering) ORDER BY topological_ordering,stream_ordering) FROM events WHERE event_id IN ("+','.join(map(quote,event_ids))+') AND NOT outlier;')
 assert rows
 row=rows[len(rows)//2];anchors.append(dict(row,room_id=room))
payload={'positions':positions,'anchors':anchors}
code='''import sys,json,asyncio\nfrom synapse.types import StreamToken,RoomStreamToken,MultiWriterStreamToken\nx=json.load(sys.stdin);p=x['positions']\ndef make(room):\n return StreamToken(room,p['presence'],p['typing'],MultiWriterStreamToken(stream=p['receipt']),p['account_data'],p['push_rules'],p['to_device'],MultiWriterStreamToken(stream=p['device_list']),p['groups'],p['un_partial_stated_rooms'],p['thread_subscriptions'],p['sticky_events'],MultiWriterStreamToken(stream=p['quarantined_media']))\nasync def run():\n x['since']=await make(RoomStreamToken(stream=p['room'])).to_string(None)\n for a in x['anchors']:\n  room=RoomStreamToken(stream=a['stream'],topological=a['depth'])\n  a['pagination']=await room.to_string(None)\n  a['prev_batch']=await make(room).to_string(None)\n  a['stream_prev_batch']=await make(RoomStreamToken(stream=a['stream'])).to_string(None)\n print(json.dumps(x))\nasyncio.run(run())'''
k=['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration','-n','spindle-rehearsal','exec','-i','spindle-historical-pdu-offline-20261006','--','python','-c',code]
with (OUT/'cursor-source-serializer.stderr.log').open('wb') as log:r=subprocess.run(k,input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=log,check=True)
result=json.loads(r.stdout);result.update(provenance='Synapse1.156 serializer from actual retained synapse_dark stream/event positions; not a captured client token',absent_database_stream_components_zeroed=absent,ephemeral_typing_and_removed_groups_zeroed=True,source_database_changed=False)
out=OUT/'client-witness-cursor-material.private.json';out.write_text(json.dumps(result,indent=2));print(json.dumps({'prepared':True,'source_database_changed':False,'anchors':len(anchors),'private_output':str(out),'cached_owner_token_claimed':False}))
