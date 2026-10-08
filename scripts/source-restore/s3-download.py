#!/usr/bin/env python3
"""Secret-env S3 download: bounded stdout stream with exact expected size/hash."""
import argparse
import hashlib
import json
import os
import sys

CHUNK = 1024 * 1024


def download(client, bucket, key, stream, expected_bytes, expected_sha256):
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    digest, count = hashlib.sha256(), 0
    try:
        if response.get("ContentLength") != expected_bytes:
            raise ValueError("object size changed")
        while True:
            chunk = body.read(CHUNK)
            if not chunk:
                break
            count += len(chunk)
            if count > expected_bytes:
                raise ValueError("object exceeded expected bounded size")
            digest.update(chunk)
            stream.write(chunk)
        stream.flush()
    finally:
        body.close()
    if count != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ValueError("object hash/size mismatch")
    return {"key": key, "bytes": count, "sha256": digest.hexdigest(), "download_verified": True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--key", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--bytes", required=True, type=int)
    args = p.parse_args()
    if not args.key or ".." in args.key.split("/") or args.bytes < 1:
        raise SystemExit("invalid expected object")
    if len(args.sha256) != 64 or any(c not in "0123456789abcdef" for c in args.sha256):
        raise SystemExit("invalid expected digest")
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-north-1"),
                              config=Config(retries={"max_attempts": 5, "mode": "standard"}))
        report = download(client, os.environ["S3_BUCKET"], args.key, sys.stdout.buffer, args.bytes, args.sha256)
        print(json.dumps(report), file=sys.stderr)
    except Exception:
        raise SystemExit("S3 source download failed; credentials and payloads suppressed") from None


if __name__ == "__main__":
    main()
