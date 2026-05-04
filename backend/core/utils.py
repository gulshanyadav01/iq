import os
from typing import Optional
from uuid import uuid4
from mimetypes import guess_extension

import boto3
from botocore.config import Config


def upload_bytes_to_minio_and_get_url(
    data: bytes,
    content_type: str,
    object_key: Optional[str] = None,
    object_key_prefix: str = "gpt-images/",
) -> str:
    """Upload bytes to a MinIO/S3 bucket and return a public or presigned URL.

    Environment variables used:
    - AWS_STORAGE_BUCKET_NAME (required)
    - AWS_S3_ENDPOINT_URL (optional; set for MinIO, blank for real S3)
    - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (optional; blank in prod uses
      boto3's default credential chain — EKS Pod Identity, IAM role, etc.)
    - AWS_REGION (optional)
    - AWS_S3_PUBLIC_ENDPOINT_URL (optional). If not provided, a presigned URL is returned
    - AWS_S3_PRESIGN_EXPIRES (optional, seconds). Default: 604800 (7 days)
    """

    endpoint_url = os.environ.get("AWS_S3_ENDPOINT_URL") or None
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or None
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or None
    bucket_name = os.environ.get("AWS_STORAGE_BUCKET_NAME")
    region_name = os.environ.get("AWS_REGION")
    public_base = os.environ.get("AWS_S3_PUBLIC_ENDPOINT_URL")
    presign_expires_raw = os.environ.get("AWS_S3_PRESIGN_EXPIRES")

    if not bucket_name:
        raise ValueError("AWS_STORAGE_BUCKET_NAME must be set.")

    presign_expires = 604800  # 7 days
    if presign_expires_raw:
        try:
            presign_expires = int(presign_expires_raw)
        except ValueError:
            pass

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region_name,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    # Determine file extension from content type
    def _infer_extension_from_content_type(ct: str) -> str:
        # Handle common cases explicitly
        if ct == "image/jpeg":
            return ".jpg"
        inferred = guess_extension(ct) or ""
        if inferred == ".jpe":
            return ".jpg"
        if inferred:
            return inferred
        subtype = ct.split("/", 1)[-1]
        return f".{subtype}" if subtype.isalnum() else ".bin"

    ext = _infer_extension_from_content_type(content_type)

    if object_key:
        # Append extension if missing
        if "." not in object_key.rsplit("/", 1)[-1]:
            key = f"{object_key}{ext}"
        else:
            key = object_key
    else:
        key = f"{object_key_prefix}{uuid4()}{ext}"

    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
    )

    # Build public URL if base provided, else presigned URL
    public_base = public_base.rstrip("/") if public_base else ""
    if public_base:
        return f"{public_base}/{bucket_name}/{key}"

    return s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=presign_expires,
    )
