#!/usr/bin/env python3
"""Capture/check one existing MAS session without login, token refresh, or logout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def capture(base, token, issuer):
    url = urllib.parse.urlparse(base)
    assert url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    assert url.path in {"", "/"} and not url.query and not url.fragment and not url.username
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(path, body=None, allow_missing=False):
        request = urllib.request.Request(base.rstrip("/") + path,
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with opener.open(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if allow_missing and error.code == 404:
                return None
            raise

    metadata = call("/_matrix/client/v1/auth_metadata")
    assert metadata["issuer"] == issuer, "wrong identity issuer"
    who = call("/_matrix/client/v3/account/whoami")
    assert who.get("device_id"), "user session with a device is required"
    joined = sorted(call("/_matrix/client/v3/joined_rooms")["joined_rooms"])
    devices = call("/_matrix/client/v3/devices")["devices"]
    assert any(device["device_id"] == who["device_id"] for device in devices)
    user = who["user_id"]
    keys = call("/_matrix/client/v3/keys/query", {"device_keys": {user: [who["device_id"]]}})
    identities = {name: keys.get(name, {}).get(user, {}).get("keys", {})
                  for name in ["master_keys", "self_signing_keys", "user_signing_keys"]}
    key_digest = hashlib.sha256(json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    device_keys = keys.get("device_keys", {}).get(user, {}).get(who["device_id"], {}).get("keys", {})
    device_digest = hashlib.sha256(json.dumps(device_keys, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    backup = call("/_matrix/client/v3/room_keys/version", allow_missing=True)
    if backup is not None:
        backup = {key: backup.get(key) for key in ["version", "algorithm", "auth_data", "count"]}
    backup_digest = hashlib.sha256(json.dumps(backup, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"issuer": metadata["issuer"], "user_id": user, "device_id": who["device_id"],
            "joined_rooms": joined, "cross_signing_keys_sha256": key_digest,
            "device_keys_sha256": device_digest, "backup_metadata_sha256": backup_digest,
            "has_cross_signing": all(identities.values()), "has_device_keys": bool(device_keys),
            "existing_token_accepted": True, "history_decryption_pass": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["capture", "check"])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--issuer", default="https://auth.reilly.asia/")
    parser.add_argument("--baseline")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = capture(args.base_url, Path(args.token_file).read_text().strip(), args.issuer)
        if args.mode == "check":
            baseline = json.loads(Path(args.baseline).read_text())
            fields = ["issuer", "user_id", "device_id", "joined_rooms", "cross_signing_keys_sha256", "device_keys_sha256", "backup_metadata_sha256"]
            mismatches = [field for field in fields if baseline[field] != result[field]]
            result.update(session_continuity_pass=not mismatches, mismatches=mismatches)
        with Path(args.output).open("x") as stream:
            json.dump(result, stream, indent=2)
        print("existing-session evidence captured" if args.mode == "capture" else
              "existing-session continuity " + ("passed" if result["session_continuity_pass"] else "failed"))
        if args.mode == "check" and not result["session_continuity_pass"]:
            raise SystemExit(1)
    except Exception as error:
        print("Existing-session probe failed: " + type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
