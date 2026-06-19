#!/usr/bin/env python3
"""
Migrate commission_intake files from local MinIO to AWS S3.

Copies every object under the `commission_intake/` prefix from a source
MinIO bucket to a destination S3 bucket, preserving keys 1:1. This matches
what `commission_intake.models.CommissionDocument.file` (FileField with
upload_to="commission_intake/%Y/%m/%d/") and `IndexedContent.attachment_keys`
(commission_intake/attachments/<doc_id>/<filename>) reference, so the dumped
DB rows will resolve against S3 unchanged.

Usage
-----
  ./scripts/migrate-minio-to-s3.py
  ./scripts/migrate-minio-to-s3.py --dry-run
  ./scripts/migrate-minio-to-s3.py --prefix commission_intake/2025/

Configuration:

  Source (MinIO) — read from project .env (AWS_* keys mapped to MinIO):
    MINIO_ENDPOINT_URL       (default: http://localhost:9900)
    MINIO_ACCESS_KEY         (default from .env AWS_ACCESS_KEY_ID, else "minio")
    MINIO_SECRET_KEY         (default from .env AWS_SECRET_ACCESS_KEY, else "miniosecret")
    MINIO_BUCKET             (default from .env AWS_STORAGE_BUCKET_NAME, else "insur-iq")
    MINIO_REGION             (default: us-east-1)

  Destination (AWS S3) — uses your aws-cli credential chain (~/.aws/...).
  No .env values are used. Override the bucket / region via:
    S3_BUCKET                (default: pbpartners-insuriq)
    AWS_PROFILE              (optional named profile)

Before copying, the script DELETES every object under the same prefix
in the destination bucket so the destination is a clean mirror of the
source. `--dry-run` lists the deletes and copies that would happen
without performing either.
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict — does NOT touch os.environ."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def make_minio_client(dotenv: dict[str, str]):
    """Source client. Creds come from .env (AWS_* there points at MinIO)."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9900"),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", dotenv.get("AWS_ACCESS_KEY_ID", "minio")),
        aws_secret_access_key=os.environ.get(
            "MINIO_SECRET_KEY", dotenv.get("AWS_SECRET_ACCESS_KEY", "miniosecret")
        ),
        region_name=os.environ.get("MINIO_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def make_s3_session() -> boto3.session.Session:
    """Destination session — uses default aws-cli credential chain."""
    return boto3.session.Session(profile_name=os.environ.get("AWS_PROFILE") or None)


def detect_bucket_region(session: boto3.session.Session, bucket: str) -> str:
    """Ask AWS where this bucket lives. Region mismatch causes opaque 403s."""
    probe = session.client("s3", region_name="us-east-1")
    loc = probe.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    return loc or "us-east-1"


def list_keys(client, bucket: str, prefix: str):
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            yield obj["Key"], obj["Size"]


def copy_one(minio, s3, src_bucket: str, dst_bucket: str, key: str, size: int) -> str:
    body = minio.get_object(Bucket=src_bucket, Key=key)["Body"]
    s3.upload_fileobj(body, dst_bucket, key)
    return f"copy  {key} ({size}B)"


def clear_prefix(s3, bucket: str, prefix: str) -> int:
    """Delete every object under prefix in the destination bucket. Returns count."""
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        # delete_objects accepts up to 1000 keys per call; pages are <=1000.
        resp = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": o["Key"]} for o in contents], "Quiet": True},
        )
        for err in resp.get("Errors", []) or []:
            print(f"  delete failed: {err.get('Key')}: {err.get('Message')}", file=sys.stderr)
        deleted += len(contents)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", default="commission_intake/", help="Source key prefix to copy")
    parser.add_argument("--dry-run", action="store_true", help="List what would be copied without uploading")
    parser.add_argument("--workers", type=int, default=8, help="Parallel transfer workers")
    args = parser.parse_args()

    dotenv = read_dotenv(Path(__file__).resolve().parent.parent / ".env")

    src_bucket = os.environ.get("MINIO_BUCKET", dotenv.get("AWS_STORAGE_BUCKET_NAME", "insur-iq"))
    dst_bucket = os.environ.get("S3_BUCKET", "pbpartners-insuriq")

    minio = make_minio_client(dotenv)
    s3 = None
    dst_region = "ap-south-1"
    if not args.dry_run:
        session = make_s3_session()
        try:
            dst_region = detect_bucket_region(session, dst_bucket)
        except ClientError as e:
            print(f"ERROR: cannot reach destination bucket {dst_bucket}: {e}", file=sys.stderr)
            print("Check `aws s3 ls s3://{0}` works with your current credentials.".format(dst_bucket), file=sys.stderr)
            return 1
        s3 = session.client("s3", region_name=dst_region, config=Config(signature_version="s3v4"))

    print(f"Source:      minio bucket={src_bucket} prefix={args.prefix}")
    print(f"Destination: s3   bucket={dst_bucket} region={dst_region}")
    if args.dry_run:
        print("(dry-run — no uploads)")
    print()

    try:
        keys = list(list_keys(minio, src_bucket, args.prefix))
    except ClientError as e:
        print(f"ERROR listing source: {e}", file=sys.stderr)
        return 1

    if not keys:
        print("No objects found at source prefix.")
        return 0

    total_bytes = sum(s for _, s in keys)
    print(f"Found {len(keys)} objects, {total_bytes / 1_000_000:.1f} MB total")
    print()

    if args.dry_run:
        try:
            existing = list(list_keys(make_s3_session().client("s3", region_name=dst_region, config=Config(signature_version="s3v4")), dst_bucket, args.prefix))
        except ClientError as e:
            print(f"(could not list destination for dry-run: {e})", file=sys.stderr)
            existing = []
        for key, size in existing:
            print(f"would delete  {key} ({size}B)")
        for key, size in keys:
            print(f"would copy    {key} ({size}B)")
        return 0

    print(f"Clearing destination prefix s3://{dst_bucket}/{args.prefix} ...")
    deleted = clear_prefix(s3, dst_bucket, args.prefix)
    print(f"Deleted {deleted} existing objects.")
    print()

    copied = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(copy_one, minio, s3, src_bucket, dst_bucket, k, sz): k for k, sz in keys}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                msg = fut.result()
                print(msg)
                copied += 1
            except Exception as exc:
                failed += 1
                print(f"FAIL  {key}: {exc}", file=sys.stderr)

    print()
    print(f"Done. deleted={deleted} copied={copied} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
