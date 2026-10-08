BEGIN READ ONLY;
WITH RECURSIVE retained AS (
 SELECT DISTINCT s.room_id FROM current_state_events s JOIN room_memberships m USING(event_id)
 WHERE s.type='m.room.member' AND right(s.state_key,length('reilly.asia')+1)=':reilly.asia' AND m.membership='join'
), outside AS (
 SELECT r.room_id,r.room_version FROM rooms r LEFT JOIN retained t USING(room_id) WHERE t.room_id IS NULL
), local_latest AS (
 SELECT l.* FROM local_current_membership l JOIN outside o USING(room_id) WHERE right(l.user_id,length('reilly.asia')+1)=':reilly.asia'
), last_departure AS (
 SELECT DISTINCT ON(l.room_id) l.room_id,l.event_id,g.state_group FROM local_latest l JOIN events e USING(event_id) JOIN event_to_state_groups g USING(event_id)
 WHERE l.membership IN('leave','ban') AND NOT e.outlier AND e.rejection_reason IS NULL ORDER BY l.room_id,l.event_stream_ordering DESC,l.event_id
), chain AS (
 SELECT room_id,state_group,0 depth,ARRAY[state_group] path,false cycle FROM last_departure
 UNION ALL SELECT c.room_id,e.prev_state_group,c.depth+1,c.path||e.prev_state_group,e.prev_state_group=ANY(c.path)
 FROM chain c JOIN state_group_edges e ON e.state_group=c.state_group WHERE NOT c.cycle
), local_state AS (
 SELECT DISTINCT ON(c.room_id,s.state_key) c.room_id,s.state_key,s.event_id FROM chain c JOIN state_groups_state s ON s.state_group=c.state_group AND s.room_id=c.room_id
 WHERE s.type='m.room.member' AND right(s.state_key,length('reilly.asia')+1)=':reilly.asia' ORDER BY c.room_id,s.state_key,c.depth
), matched AS (
 SELECT l.room_id,l.user_id,l.membership,l.event_id,s.event_id AS archived_state_event,coalesce(m.membership,'missing') state_membership,coalesce(old.forgotten,0)<>0 forgotten
 FROM local_latest l JOIN last_departure d USING(room_id) LEFT JOIN local_state s ON s.room_id=l.room_id AND s.state_key=l.user_id
 LEFT JOIN room_memberships m ON m.event_id=s.event_id LEFT JOIN room_memberships old ON old.event_id=l.event_id
), facts AS (
 SELECT o.room_id,
 (SELECT count(*) FROM events e WHERE e.room_id=o.room_id AND NOT e.outlier AND e.rejection_reason IS NULL) timelines,
 (SELECT count(*) FROM event_forward_extremities f WHERE f.room_id=o.room_id) tips,
 (SELECT count(*) FROM event_forward_extremities f LEFT JOIN event_to_state_groups g USING(event_id) WHERE f.room_id=o.room_id AND g.state_group IS NULL) missing_tip_groups,
 EXISTS(SELECT 1 FROM local_latest l WHERE l.room_id=o.room_id AND l.membership='leave' AND EXISTS(SELECT 1 FROM room_memberships m WHERE m.room_id=o.room_id AND m.user_id=l.user_id AND m.membership='join')) ever_local_join
 FROM outside o
)
SELECT json_build_object(
 'archived_rooms_with_departure_group',(SELECT count(*) FROM last_departure),
 'archive_departure_group_cycles',(SELECT count(*) FROM chain WHERE cycle),
 'latest_local_membership_rows_in_archived_rooms',(SELECT count(*) FROM matched),
 'departure_group_local_membership_ids_differ',(SELECT count(*) FROM matched WHERE event_id<>archived_state_event OR archived_state_event IS NULL),
 'departure_group_local_join_rows',(SELECT count(*) FROM matched WHERE state_membership='join'),
 'departure_group_missing_local_membership_rows',(SELECT count(*) FROM matched WHERE state_membership='missing'),
 'archived_latest_local_rows_forgotten',(SELECT count(*) FROM matched WHERE forgotten),
 'archived_rooms_multiple_forward_tips',(SELECT count(*) FROM facts WHERE timelines>0 AND tips>1),
 'archive_missing_forward_tip_groups',(SELECT sum(missing_tip_groups) FROM facts WHERE timelines>0),
 'outlier_only_departed_rooms_with_historical_local_join',(SELECT count(*) FROM facts WHERE timelines=0 AND ever_local_join)
);
COMMIT;
