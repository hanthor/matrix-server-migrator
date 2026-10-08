#!/usr/bin/env python3
"""Bounded multipart upload with conditional completion and full stream reread."""
import argparse
import hashlib
import json
import os
import sys

import boto3
from botocore.config import Config

CHUNK = 16 * 1024 * 1024


def upload(client, bucket, key, stream):
    upload_id = client.create_multipart_upload(Bucket=bucket, Key=key)['UploadId']
    parts, count, digest = [], 0, hashlib.sha256()
    completed = False
    try:
        while True:
            data = stream.read(CHUNK)
            if not data:
                break
            if len(parts) >= 10000:
                raise ValueError('multipart part limit exceeded')
            count += len(data)
            digest.update(data)
            result = client.upload_part(Bucket=bucket, Key=key, UploadId=upload_id,
                                        PartNumber=len(parts)+1, Body=data)
            parts.append({'PartNumber': len(parts)+1, 'ETag': result['ETag']})
        if count == 0:
            raise ValueError('empty source stream')
        client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id,
                                         MultipartUpload={'Parts': parts}, IfNoneMatch='*')
        completed = True
        body = client.get_object(Bucket=bucket, Key=key)['Body']
        received, actual = 0, hashlib.sha256()
        try:
            while True:
                data = body.read(1024 * 1024)
                if not data:
                    break
                received += len(data)
                actual.update(data)
        finally:
            body.close()
        if received != count or actual.hexdigest() != digest.hexdigest():
            raise ValueError('uploaded object stream hash/size mismatch')
        report = {'key': key, 'bytes': count, 'sha256': digest.hexdigest(),
                  'full_object_reread_verified': True, 'overwrite_prevented': True}
        manifest = json.dumps(report, indent=2).encode() + b'\n'
        client.put_object(Bucket=bucket, Key=key+'.sha256.json', Body=manifest,
                          ContentType='application/json', IfNoneMatch='*')
        remote = client.get_object(Bucket=bucket, Key=key+'.sha256.json')['Body']
        try:
            if remote.read() != manifest:
                raise ValueError('remote hash manifest differs')
        finally:
            remote.close()
        return report
    finally:
        if not completed:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, help='Fresh immutable prefix and artifact name')
    args = parser.parse_args()
    if not args.key.startswith('postgres/spindle-cutover/') or '..' in args.key.split('/'):
        raise SystemExit('Use a fresh postgres/spindle-cutover/ prefix')
    try:
        client = boto3.client('s3', region_name=os.environ.get('AWS_DEFAULT_REGION', 'eu-north-1'),
                              config=Config(retries={'max_attempts': 5, 'mode': 'standard'}))
        report = upload(client, os.environ['S3_BUCKET'], args.key, sys.stdin.buffer)
    except Exception:
        # SDK failures can include request/auth context; retain no credentials in output.
        raise SystemExit('S3 stream upload or verification failed; credential contents suppressed') from None
    print(json.dumps(report))


if __name__ == '__main__':
    main()
