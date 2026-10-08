#!/usr/bin/env python3
"""Capture read-only source markers without retrieving account credentials."""
import argparse
import json
import subprocess

SQL = r"""
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH retained AS (
 SELECT DISTINCT s.room_id FROM current_state_events s
 JOIN room_memberships m USING (event_id)
 WHERE s.type='m.room.member'
 AND right(s.state_key,length('reilly.asia')+1)=':reilly.asia'
 AND m.membership='join'
), version_counts AS (
 SELECT coalesce(room_version,'1') version,count(*) rooms
 FROM rooms JOIN retained USING(room_id) GROUP BY 1
)
SELECT json_build_object(
 'captured_at',clock_timestamp(), 'database',current_database(),
 'postgres_started_at',pg_postmaster_start_time(),
 'database_bytes',pg_database_size(current_database()),
 'events',(SELECT count(*) FROM events),
 'events_highwater',(SELECT max(stream_ordering) FROM events),
 'last_received_at',(SELECT to_timestamp(max(received_ts)/1000.0) FROM events),
 'retained_rooms',(SELECT count(*) FROM retained),
 'retained_room_ids_md5',(SELECT md5(string_agg(room_id,E'\n' ORDER BY room_id)) FROM retained),
 'retained_events',(SELECT count(*) FROM events JOIN retained USING(room_id)),
 'current_state_md5',(SELECT md5(string_agg(json_build_array(s.room_id,s.type,s.state_key,s.event_id)::text,E'\n'
 ORDER BY s.room_id,s.type,s.state_key)) FROM current_state_events s JOIN retained USING(room_id)),
 'local_users',(SELECT count(*) FROM users WHERE right(name,length('reilly.asia')+1)=':reilly.asia'),
 'devices',(SELECT count(*) FROM devices),
 'access_token_rows',(SELECT count(*) FROM access_tokens),
 'account_data_rows',(SELECT count(*) FROM account_data),
 'room_account_data_rows',(SELECT count(*) FROM room_account_data),
 'key_backup_rows',(SELECT count(*) FROM e2e_room_keys),
 'room_versions',(SELECT json_object_agg(version,rooms) FROM version_counts)
);
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', default='/home/ubuntu/.kube/config-aws-migration')
    parser.add_argument('--context', default='admin@aws-migration')
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--pod', required=True)
    parser.add_argument('--database', default='synapse')
    args = parser.parse_args()
    command = ['kubectl', '--kubeconfig', args.kubeconfig, '--context', args.context,
               '-n', args.namespace, 'exec', '-i', args.pod, '--',
               'psql', '-X', '-U', 'postgres', '-d', args.database, '-Atq',
               '-v', 'ON_ERROR_STOP=1']
    result = subprocess.run(command, input=SQL, text=True, capture_output=True,
                            check=True, timeout=120)
    markers = json.loads(result.stdout)
    markers.update(namespace=args.namespace, pod=args.pod)
    print(json.dumps(markers, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
