#!/usr/bin/env python3
"""Read-only aggregate preservation limits; no room IDs, event bodies or credentials emitted."""
import argparse,datetime,json,subprocess
from pathlib import Path
SQL="""BEGIN READ ONLY;
WITH local_current AS (
 SELECT s.room_id,m.membership FROM current_state_events s JOIN room_memberships m USING(event_id)
 WHERE s.type='m.room.member' AND right(s.state_key,length('reilly.asia')+1)=':reilly.asia'
), retained AS (SELECT DISTINCT room_id FROM local_current WHERE membership='join'),
 outside AS (SELECT r.room_id FROM rooms r LEFT JOIN retained t USING(room_id) WHERE t.room_id IS NULL),
 latest_local AS (SELECT room_id,array_agg(DISTINCT membership ORDER BY membership) memberships FROM local_current_membership WHERE right(user_id,length('reilly.asia')+1)=':reilly.asia' GROUP BY room_id),
local_members AS (SELECT room_id,array_agg(DISTINCT membership ORDER BY membership) AS memberships FROM local_current GROUP BY room_id),
 history AS (
 SELECT e.room_id,count(*) AS event_rows,
 count(*) FILTER (WHERE right(e.sender,length('reilly.asia')+1)=':reilly.asia') AS local_sender_events,
 count(*) FILTER (WHERE right(e.sender,length('reilly.asia')+1)=':reilly.asia' AND e.type IN ('m.room.message','m.room.encrypted')) AS local_message_events
 FROM events e JOIN outside o USING(room_id) GROUP BY e.room_id
), audited AS (
 SELECT o.room_id,coalesce(m.memberships,ARRAY[]::text[]) memberships,
 coalesce(l.memberships,ARRAY[]::text[]) latest_memberships,
 coalesce(h.event_rows,0) event_rows,coalesce(h.local_sender_events,0) local_sender_events,coalesce(h.local_message_events,0) local_message_events
 FROM outside o LEFT JOIN local_members m USING(room_id) LEFT JOIN latest_local l USING(room_id) LEFT JOIN history h USING(room_id)
)
SELECT json_build_object(
 'source_rooms',(SELECT count(*) FROM rooms),
 'retained_rooms',(SELECT count(*) FROM retained),
 'outside_retained_rooms',count(*),
 'outside_with_current_state_local_invite',count(*) FILTER (WHERE 'invite'=ANY(memberships)),
 'outside_with_current_state_local_leave',count(*) FILTER (WHERE 'leave'=ANY(memberships)),
 'outside_with_current_state_local_ban',count(*) FILTER (WHERE 'ban'=ANY(memberships)),
 'outside_with_current_state_local_knock',count(*) FILTER (WHERE 'knock'=ANY(memberships)),
 'outside_with_any_current_state_local_membership',count(*) FILTER (WHERE cardinality(memberships)>0),
 'outside_with_historical_local_sender',count(*) FILTER (WHERE local_sender_events>0),
 'outside_with_historical_local_message_sender',count(*) FILTER (WHERE local_message_events>0),
 'outside_with_no_current_state_local_membership_but_historical_local_sender',count(*) FILTER (WHERE cardinality(memberships)=0 AND local_sender_events>0),
 'outside_with_latest_local_invite',count(*) FILTER (WHERE 'invite'=ANY(latest_memberships)),
 'outside_with_latest_local_leave',count(*) FILTER (WHERE 'leave'=ANY(latest_memberships)),
 'outside_with_latest_local_ban',count(*) FILTER (WHERE 'ban'=ANY(latest_memberships)),
 'outside_with_latest_local_knock',count(*) FILTER (WHERE 'knock'=ANY(latest_memberships)),
 'outside_with_latest_local_join',count(*) FILTER (WHERE 'join'=ANY(latest_memberships)),
 'outside_with_any_latest_local_membership',count(*) FILTER (WHERE cardinality(latest_memberships)>0),
 'latest_local_membership_combinations',(SELECT json_object_agg(combination,n) FROM (SELECT coalesce(nullif(array_to_string(latest_memberships,','),''),'none') combination,count(*) n FROM audited GROUP BY combination ORDER BY combination) counted),
 'outside_event_rows',coalesce(sum(event_rows),0),
 'outside_local_sender_events',coalesce(sum(local_sender_events),0),
 'outside_local_message_events',coalesce(sum(local_message_events),0),
 'source_event_rows',(SELECT count(*) FROM events),
 'retained_event_rows',(SELECT count(*) FROM events JOIN retained USING(room_id)),
 'current_state_local_membership_combinations',(SELECT json_object_agg(combination,n) FROM (SELECT coalesce(nullif(array_to_string(memberships,','),''),'none') combination,count(*) n FROM audited GROUP BY combination ORDER BY combination) counted)
) FROM audited;
COMMIT;
"""
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
k=['kubectl','--kubeconfig','/home/ubuntu/.kube/config-aws-migration','--context','admin@aws-migration','-n','postgres','exec','-i','deploy/postgres','--','psql','-X','-U','postgres','-d','synapse','-Atq','-v','ON_ERROR_STOP=1']
r=subprocess.run(k,input=SQL.encode(),capture_output=True,timeout=120)
if r.returncode:raise SystemExit('Read-only scope audit failed; raw details suppressed')
d=json.loads(r.stdout);d.update(captured_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope_rule='At least one CURRENT joined member whose Matrix ID ends :reilly.asia; all other rooms have no room-history import by default',counts_overlap=True,historical_sender_includes_auth_state_rejected_outlier_events=True,production_changed=False)
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps(d,indent=2))
