#!/usr/bin/env python3
"""Read-only client-surface evidence; ciphertext reads do not prove decryption."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    assert manifest["user_id"].startswith("@"), "user_id must be a Matrix ID"
    assert manifest.get("provenance"), "record trusted pre-migration sample provenance"
    assert manifest["samples"], "at least one real imported encrypted event is required"
    assert manifest.get("cross_signing_keys_sha256"), "trusted pre-migration identity digest required"
    assert len({s["event_id"] for s in manifest["samples"]}) == len(manifest["samples"])
    for sample in manifest["samples"]:
        assert sample["room_id"].startswith("!") and sample["event_id"].startswith("$")
        assert len(sample["body_sha256"]) == 64
        int(sample["body_sha256"], 16)
    return manifest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run(base_url, token_file, manifest):
    url = urllib.parse.urlparse(base_url)
    assert url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not url.query and not url.fragment and url.path in {"", "/"}
    token = Path(token_file).read_text().strip()
    assert token, "empty token"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(path, body=None):
        req = urllib.request.Request(base_url.rstrip("/") + path,
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        with opener.open(req, timeout=45) as response:
            return json.load(response)

    who = call("/_matrix/client/v3/account/whoami")
    assert who["user_id"] == manifest["user_id"], "wrong imported user"
    joined = set(call("/_matrix/client/v3/joined_rooms")["joined_rooms"])
    assert {s["room_id"] for s in manifest["samples"]} <= joined, "sample room not joined"
    keys = call("/_matrix/client/v3/keys/query", {"device_keys": {who["user_id"]: []}})
    identities = {name: keys.get(name, {}).get(who["user_id"], {}).get("keys", {})
                  for name in ["master_keys", "self_signing_keys", "user_signing_keys"]}
    assert all(identities.values()), "cross-signing public keys missing"
    digest = hashlib.sha256(json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == manifest["cross_signing_keys_sha256"], "cross-signing identity changed"
    samples = []
    for sample in manifest["samples"]:
        room, event = [urllib.parse.quote(sample[k], safe="") for k in ["room_id", "event_id"]]
        result = call(f"/_matrix/client/v3/rooms/{room}/event/{event}")
        assert result.get("event_id") == sample["event_id"]
        assert result.get("type") == "m.room.encrypted", "sample is not encrypted ciphertext"
        assert result.get("content", {}).get("algorithm") == "m.megolm.v1.aes-sha2"
        samples.append({"room_id": sample["room_id"], "event_id": sample["event_id"], "encrypted": True})
    return {"surface_pass": True, "history_decryption_pass": False,
            "user_id": who["user_id"], "device_id": who.get("device_id"),
            "joined_rooms": len(joined), "cross_signing_keys_sha256": digest, "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--token-file")
    parser.add_argument("--output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        manifest = load_manifest(args.manifest)
        if args.validate_only:
            print("manifest valid; no server contacted")
            return
        assert args.base_url and args.token_file and args.output, "connection and output required"
        result = run(args.base_url, args.token_file, manifest)
        with Path(args.output).open("x") as output:
            json.dump(result, output, indent=2)
        print("client surface passed; existing-history decryption remains unproven")
    except Exception as error:
        # Do not dump response bodies, tokens, plaintext, or redirect URLs.
        print("client preflight failed: " + type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
