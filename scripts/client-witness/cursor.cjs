// Actual HTTP predecessor-cursor checks on the witness's authenticated fixture session.
const fs = require('node:fs');
const crypto = require('node:crypto');
const digest = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const requirePass = ok => { if (!ok) throw new Error('Predecessor cursor witness failed'); };
module.exports = async ({ base, accessToken, materialFile }) => {
  const url = new URL(base);
  requirePass(['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname));
  const material = JSON.parse(fs.readFileSync(materialFile, 'utf8'));
  requirePass(material.anchors.length > 0 && material.since.includes('_'));
  const request = async (path, query, body) => {
    const endpoint = new URL(path, url);
    for (const [key, value] of Object.entries(query || {})) endpoint.searchParams.set(key, value);
    const response = await fetch(endpoint, { method: body ? 'POST' : 'GET', headers: {
      Authorization: `Bearer ${accessToken}`, ...(body ? { 'Content-Type': 'application/json' } : {})
    }, body: body ? JSON.stringify(body) : undefined, redirect: 'error', signal: AbortSignal.timeout(60000) });
    return { status: response.status, body: await response.json() };
  };
  const filter = JSON.stringify({ room: { timeline: { limit: 0 }, state: { lazy_load_members: false } }, presence: { not_types: ['*'] } });
  const baseline = await request('/_matrix/client/v3/sync', { timeout: '0', filter });
  const resumed = await request('/_matrix/client/v3/sync', { timeout: '0', filter, since: material.since });
  requirePass(baseline.status === 200 && resumed.status === 200);
  const initial = baseline.body.rooms?.join || {};
  const foreign = resumed.body.rooms?.join || {};
  const roomIds = Object.keys(initial).sort();
  requirePass(roomIds.length >= material.anchors.length && digest(roomIds) === digest(Object.keys(foreign).sort()));
  const stateKeys = room => (room.state?.events || []).map(e => [e.type, e.state_key]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  let stateEvents = 0;
  for (const roomId of roomIds) {
    const expected = stateKeys(initial[roomId]);
    requirePass(expected.length > 0 && digest(expected) === digest(stateKeys(foreign[roomId])));
    stateEvents += expected.length;
  }
  const sliding = [];
  for (const pos of [material.since, `1/${material.since}`]) {
    const reply = await request('/_matrix/client/unstable/org.matrix.simplified_msc3575/sync', { pos, timeout: '0' }, { lists: {} });
    requirePass(reply.status === 400 && reply.body.errcode === 'M_UNKNOWN_POS');
    sliding.push({ token_sha256: digest(pos), status: reply.status, errcode: reply.body.errcode });
  }
  const pages = [];
  for (const anchor of material.anchors) {
    requirePass(roomIds.includes(anchor.room_id));
    const expected = digest(anchor.event_id);
    let comparison;
    for (const kind of ['pagination', 'prev_batch', 'stream_prev_batch']) {
      const reply = await request(`/_matrix/client/v3/rooms/${encodeURIComponent(anchor.room_id)}/messages`, { from: anchor[kind], dir: 'b', limit: '1' });
      const ids = (reply.body.chunk || []).map(e => e.event_id);
      requirePass(reply.status === 200 && ids.length === 1 && digest(ids[0]) === expected);
      requirePass(reply.body.start === anchor[kind]);
      const identity = digest(ids);
      requirePass(!comparison || comparison === identity);
      comparison = identity;
      pages.push({ room_sha256: digest(anchor.room_id), cursor_kind: kind, cursor_sha256: digest(anchor[kind]), status: reply.status, event_count: ids.length, event_ids_sha256: identity, source_anchor_match: true });
    }
  }
  return { passed: true, source_derived_cursor_provenance: material.provenance, cached_owner_session_tested: false,
    since_sha256: digest(material.since), initial_rooms: roomIds.length, initial_state_events: stateEvents,
    joined_rooms_sha256: digest(roomIds), foreign_sync_rebuilt_initial_state: true,
    sliding_foreign_position_results: sliding, pagination: pages, raw_tokens_or_plaintext_written: false };
};
