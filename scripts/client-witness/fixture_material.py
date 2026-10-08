#!/usr/bin/env python3
"""Export retained fixture credentials and trusted hashes privately, without login."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", choices=list("abc"), default="b")
    parser.add_argument("--output", required=True)
    parser.add_argument("--pg-pod", default="rehearsal-pg-6bbb6fb9cc-qp4fz")
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(args.output)
    out.mkdir(mode=0o700)
    kube = ["kubectl", "--kubeconfig", "/home/ubuntu/.kube/config-aws-migration",
            "--context", "admin@aws-migration", "-n", "spindle-rehearsal"]
    def read(command):
        result = subprocess.run(kube + command, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("retained fixture read failed")
        return result.stdout
    try:
        rig = json.loads(json.loads(read(["get", "configmap", "spindle-mig-rig-manifest", "-o", "json"]))["data"]["manifest.json"])
        secrets = json.loads(read(["get", "secret", "spindle-mig-rig", "-o", "json"]))["data"]
        user_id = rig["users"][args.user]
        assert user_id == f"@spindle-mig-{args.user}:reilly.asia"
        sql = "SELECT json_object_agg(keytype,keydata::jsonb->'keys') FROM e2e_cross_signing_keys WHERE user_id='" + user_id + "';"
        keys = json.loads(read(["exec", args.pg_pod, "--", "psql", "-U", "postgres", "-d", "synapse_dark", "-Atc", sql]))
        identities = {dst: keys[src] for src, dst in [("master", "master_keys"), ("self_signing", "self_signing_keys"), ("user_signing", "user_signing_keys")]}
        edited = {event["relates_to"] for room in rig["rooms"] for event in room["events"]
                  if event["kind"] == "edit" and event.get("relates_to")}
        samples = [{"room_id": room["room_id"], "event_id": event["event_id"], "body_sha256": event["sha256"]}
                   for room in rig["rooms"] for event in room["events"]
                   if event["kind"] == "message" and event["encrypted"] and args.user in event["readable_by"]
                   and event["event_id"] not in edited]
        assert samples
        manifest = {"user_id": user_id,
                    "provenance": "Preserved pre-migration synthetic synapse_dark fixture; spindle-mig-rig-manifest original plaintext hashes; current source public cross-signing keys",
                    "cross_signing_keys_sha256": hashlib.sha256(json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "samples": samples}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (out / "original-rig-manifest.json").write_text(json.dumps(rig, indent=2))
        for name in ["password", "recovery-key"]:
            (out / name).write_bytes(base64.b64decode(secrets[f"{name}-{args.user}"]))
        print(f"Retained fixture {args.user} prepared privately; {len(samples)} trusted encrypted-message hashes; no login performed")
    except Exception as error:
        print("Fixture preparation failed: " + type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
