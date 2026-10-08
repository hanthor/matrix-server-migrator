#!/usr/bin/env python3
"""Private bounded source selection and offline room-version-aware PDU verification."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import urllib.request


def private_json(path, value):
    with open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(value, handle, separators=(',', ':'))
        handle.write('\n')

def select(args):
    if not re.fullmatch(r'[a-f0-9]{64}',args.binary_sha):
        raise RuntimeError('candidate SHA must be64 lowercase hex characters')
    rooms = json.loads(args.rooms.read_text())
    if not isinstance(rooms, list) or not rooms or any(not isinstance(r, str) or not r.startswith('!') for r in rooms):
        raise RuntimeError('scope must be a nonempty JSON room-ID array from completed import')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if args.key_file is not None:
        doc = json.loads(args.key_file.read_text())
    else:
        with urllib.request.urlopen(args.key_url, timeout=30) as response:
            doc = json.load(response)
    if doc['server_name'] != args.server:
        raise RuntimeError('wrong source signing document')
    key_ids = list(doc.get('verify_keys', {})) + list(doc.get('old_verify_keys', {}))
    if not key_ids:
        raise RuntimeError('no advertised verification keys')
    # Literals are escaped as SQL text, never as executable shell commands.
    literal = lambda s: "'" + s.replace("'", "''") + "'"
    room_sql = ','.join(map(literal, rooms))
    key_sql = ','.join(map(literal, key_ids))
    sql = f"""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
    WITH eligible AS (
      SELECT e.event_id,e.room_id,r.room_version,e.stream_ordering,j.json::jsonb AS pdu
      FROM events e JOIN event_json j USING(event_id) JOIN rooms r ON r.room_id=e.room_id
      LEFT JOIN rejections rejected ON rejected.event_id=e.event_id
      WHERE e.room_id IN ({room_sql}) AND rejected.event_id IS NULL
        AND e.rejection_reason IS NULL AND e.outlier = FALSE
        AND e.sender LIKE {literal('%:' + args.server)}
        AND (j.json::jsonb->'signatures'->{literal(args.server)}) ?| ARRAY[{key_sql}]
        AND NOT EXISTS (SELECT 1 FROM redactions d WHERE d.redacts=e.event_id)
    ), ranked AS (
      SELECT *,row_number() OVER(PARTITION BY room_version ORDER BY stream_ordering,event_id) AS n,
        count(*) OVER(PARTITION BY room_version) AS total FROM eligible
    ) SELECT coalesce(json_agg(json_build_object('event_id',event_id,'room_id',room_id,
      'room_version',room_version,'stream_ordering',stream_ordering,'pdu',pdu)
      ORDER BY room_version,n),'[]'::json)
      FROM ranked WHERE n IN (1,2,greatest(1,total/2),greatest(1,total-1),total);
    COMMIT;"""
    command = ['kubectl','--kubeconfig',args.kubeconfig,'--context',args.context,'-n',args.namespace,
               'exec','-i',args.pod,'--','psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres','-d',args.database]
    with open(args.output/"selection-stderr-private.log", "xb", opener=lambda p,f:os.open(p,f,0o600)) as log:
        for attempt in range(3):
            result = subprocess.run(command,input=sql.encode(),stdout=subprocess.PIPE,stderr=log)
            try:
                if result.returncode:
                    raise ValueError('read-only source selection transport failed')
                if len(result.stdout)>16*1024**2:
                    raise RuntimeError('source sample JSON exceeds bound')
                samples = json.loads(result.stdout)
                break
            except (ValueError,UnicodeError):
                if attempt==2:
                    raise RuntimeError('read-only source selection failed after bounded retries')
                import time
                time.sleep(2)
    if not samples or len(samples)>30:
        raise RuntimeError('unexpected bounded sample count')
    private_json(args.output/'source-samples.json', samples)
    private_json(args.output/'source-public-key.json', doc)
    private_json(args.output/'selection.json', {'room_scope_sha256':hashlib.sha256(args.rooms.read_bytes()).hexdigest(),
                'scope_rooms':len(rooms),'samples':len(samples),'versions':sorted({s['room_version'] for s in samples}),
                'final_binary_sha256':args.binary_sha,'source_database':args.database,'server':args.server})

def verify(args):
    from canonicaljson import encode_canonical_json
    from signedjson.key import decode_verify_key_bytes
    from signedjson.sign import verify_signed_json
    from unpaddedbase64 import decode_base64
    from synapse.api.room_versions import KNOWN_ROOM_VERSIONS
    from synapse.crypto.event_signing import check_event_content_hash
    from synapse.events import make_event_from_dict
    from synapse.events.utils import prune_event
    source=json.loads(args.source.read_text()); target=json.loads(args.target.read_text())
    doc=json.loads(args.keys.read_text()); selection=json.loads(args.selection.read_text())
    if not re.fullmatch(r'[a-f0-9]{64}',args.binary_sha) or selection['final_binary_sha256'] != args.binary_sha:
        raise RuntimeError('candidate SHA mismatch')
    server=selection['server']; public={**doc.get('old_verify_keys',{}), **doc.get('verify_keys',{})}
    # The historical signature establishes old signing-key continuity. Verify
    # the public key document's own signature too, without loading private keys.
    document_valid=False
    for kid in doc.get('signatures',{}).get(server,{}):
        if kid in public:
            try:
                verify_signed_json(doc,server,decode_verify_key_bytes(kid,decode_base64(public[kid]['key'])))
                document_valid=True
            except Exception:
                pass
    rows=[]; lookup={(s['room_id'],s['event_id']):s.get('pdu') for s in target}
    canonical=lambda p:encode_canonical_json({k:v for k,v in p.items() if k not in ('unsigned','age_ts')})
    for sample in source:
        original=sample['pdu']; imported=lookup.get((sample['room_id'],sample['event_id']))
        row={k:sample[k] for k in ('event_id','room_id','room_version','stream_ordering')}
        row.update(source_sha256=hashlib.sha256(canonical(original)).hexdigest(),found=imported is not None)
        if imported is not None:
            row['target_sha256']=hashlib.sha256(canonical(imported)).hexdigest()
            row['canonical_equal']=canonical(original)==canonical(imported)
            ev=make_event_from_dict(dict(imported),KNOWN_ROOM_VERSIONS[str(sample['room_version'])])
            row['content_hash_valid']=check_event_content_hash(ev)
            pruned=prune_event(ev).get_pdu_json(); valid=[]
            for kid in imported.get('signatures',{}).get(server,{}):
                if kid not in public:
                    continue
                try:
                    verify_signed_json(pruned,server,decode_verify_key_bytes(kid,decode_base64(public[kid]['key'])))
                    valid.append(kid)
                except Exception:
                    pass
            row['valid_signature_keys']=valid
        row['passed']=bool(row.get('found') and row.get('canonical_equal') and row.get('content_hash_valid') and row.get('valid_signature_keys'))
        rows.append(row)
    expected=set(map(str,args.expected_versions.split(','))); covered={str(s['room_version']) for s in source}
    result={'candidate_sha256':args.binary_sha,'source_key_document_self_signature_valid':document_valid,
            'room_scope_sha256':selection['room_scope_sha256'],'scope_rooms':selection['scope_rooms'],
            'expected_versions':sorted(expected),'covered_versions':sorted(covered),'samples':rows,
            'passed':bool(document_valid and rows and expected<=covered and all(r['passed'] for r in rows)),
            'limits':'Offline historical PDU/signature continuity only; does not prove delegated federation transport or new peer authentication.'}
    private_json(args.output,result)
    if not result['passed']:
        raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description=__doc__); commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('select'); p.set_defaults(run=select)
    for name in ('kubeconfig','context','namespace','pod','database'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--rooms',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--binary-sha',required=True);p.add_argument('--server',default='reilly.asia');p.add_argument('--key-url',default='https://matrix.reilly.asia/_matrix/key/v2/server');p.add_argument('--key-file',type=Path)
    p=commands.add_parser('verify');p.set_defaults(run=verify)
    for name in ('source','target','keys','selection','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--binary-sha',required=True);p.add_argument('--expected-versions',default='1,6,9,10,12')
    args=parser.parse_args();args.run(args)

if __name__=='__main__':
    main()
