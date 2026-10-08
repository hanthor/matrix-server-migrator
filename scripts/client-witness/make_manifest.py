#!/usr/bin/env python3
"""Hash a private trusted client's decrypted reference; never print plaintext."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    os.umask(0o077)
    try:
        ref = json.loads(Path(args.reference).read_text())
        user = ref["user_id"]
        identities = {name: ref["source_keys_query"][name][user]["keys"]
                      for name in ["master_keys", "self_signing_keys", "user_signing_keys"]}
        samples = []
        for event in ref["decrypted_events"]:
            # Restrict to simple text; HTML, edits/replies, redactions, and rich media
            # have renderer-specific body representations and need an SDK witness.
            assert event["original_type"] == "m.room.encrypted"
            assert event["type"] == "m.room.message"
            content = event["content"]
            assert content["msgtype"] == "m.text"
            assert not any(k in content for k in ["formatted_body", "m.relates_to", "m.new_content"])
            samples.append({"room_id": event["room_id"], "event_id": event["event_id"],
                            "body_sha256": hashlib.sha256(content["body"].encode()).hexdigest()})
        assert samples and ref["provenance"]
        result = {"user_id": user, "provenance": ref["provenance"],
                  "cross_signing_keys_sha256": hashlib.sha256(json.dumps(
                      identities, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  "samples": samples}
        with Path(args.output).open("x") as output:
            json.dump(result, output, indent=2)
        print("Trusted reference hashed; no server contacted")
    except Exception as error:
        print("Reference rejected: " + type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
