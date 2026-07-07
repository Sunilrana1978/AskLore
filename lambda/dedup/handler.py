"""
Triggered by s3:ObjectCreated on asklore-raw-uploads.
SHA-256-hashes each object and does a conditional DynamoDB write keyed on the
hash. New content is copied into asklore-raw (which fires the existing
IngestionTriggerLambda unmodified); duplicate content is deleted from the
landing bucket without ever reaching the Knowledge Base data source.
.metadata.json sidecars pass through unhashed, since Bedrock KB requires them
to exist alongside their document at the same key regardless of content.
If copy_object fails after put_item succeeds, the hash record exists but the
file never reaches asklore-raw — acceptable for a POC, not engineered around.
"""

import hashlib
import os
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")

FILE_HASHES_TABLE = os.environ["FILE_HASHES_TABLE"]
CLEAN_BUCKET = os.environ["CLEAN_BUCKET"]

METADATA_SUFFIX = ".metadata.json"


def handler(event: dict, context) -> dict:
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        try:
            _process_record(bucket, key)
        except Exception as exc:
            print(f"[ERROR] Failed to process {key}: {exc}")

    return {"statusCode": 200}


def _process_record(bucket: str, key: str) -> None:
    if key.endswith(METADATA_SUFFIX):
        _copy_and_delete(bucket, key)
        print(f"[OK] Passed through metadata sidecar {key}")
        return

    content = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    file_hash = hashlib.sha256(content).hexdigest()

    try:
        dynamodb.put_item(
            TableName=FILE_HASHES_TABLE,
            Item={
                "file_hash": {"S": file_hash},
                "filename": {"S": key.rsplit("/", 1)[-1]},
                "domain": {"S": _domain_from_key(key)},
                "upload_date": {"S": datetime.now(UTC).isoformat()},
                "s3_path": {"S": f"s3://{CLEAN_BUCKET}/{key}"},
            },
            ConditionExpression="attribute_not_exists(file_hash)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            s3.delete_object(Bucket=bucket, Key=key)
            print(f"[INFO] Duplicate content for {key} (hash {file_hash}), deleted from landing bucket")
            return
        raise

    _copy_and_delete(bucket, key)
    print(f"[OK] Copied {key} to {CLEAN_BUCKET} (hash {file_hash})")


def _copy_and_delete(bucket: str, key: str) -> None:
    s3.copy_object(Bucket=CLEAN_BUCKET, Key=key, CopySource={"Bucket": bucket, "Key": key})
    s3.delete_object(Bucket=bucket, Key=key)


def _domain_from_key(key: str) -> str:
    return key.split("/", 1)[0] if "/" in key else "uncategorized"
