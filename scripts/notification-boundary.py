#!/usr/bin/env python3
"""Check fresh offline-import provenance alongside native/source validation."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def require_boundary(report, server_name, expected=None):
    # Production uses the default joined-room discovery, with no CLI filters.
    # Match Rust serde's compact tuple encoding, including non-ASCII names.
    scope = json.dumps([server_name, None, None, {}, False, 3],
                       ensure_ascii=False, separators=(',', ':')).encode()
    assert report.get('server_name') == server_name, 'notification server differs'
    assert report.get('dry_run') is False, 'notification proof requires writing import'
    assert report.get('notification_boundary_mode') == 'managed_fresh', 'fresh managed notification boundary required'
    proof = report.get('notification_boundary')
    assert isinstance(proof, dict), 'notification proof missing'
    assert type(proof.get('version')) is int and proof['version'] == 1, 'notification proof version differs'
    assert isinstance(proof.get('import_id'), str) and re.fullmatch('[0-9a-f]{64}', proof['import_id']), 'invalid notification import identity'
    assert proof.get('scope_sha256') == hashlib.sha256(scope).hexdigest(), 'notification scope differs from default joined production import'
    high = proof.get('high_water')
    assert type(high) is int and 0 <= high < 2**64 - 1, 'notification boundary incomplete or exhausted'
    if expected is not None:
        assert proof == expected, 'notification provenance changed across cold restore'
    # This report check cannot inspect the durable cursor. Native --validate-only
    # must succeed separately against the same exact store and frozen source.
    return dict(proof)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--server-name', required=True)
    parser.add_argument('--expected-proof', type=Path)
    args = parser.parse_args()
    expected = json.loads(args.expected_proof.read_text()) if args.expected_proof else None
    print(json.dumps(require_boundary(json.loads(args.report.read_text()), args.server_name, expected)))
