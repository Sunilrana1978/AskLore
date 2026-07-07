"""
Triggered by s3:ObjectCreated on asklore-raw.
Starts a Bedrock Knowledge Base ingestion job so the new/changed object gets
chunked, embedded, and indexed. Bedrock allows only one running ingestion job
per data source — a ConflictException means a sync is already in flight and
will pick up this object on its next run, so it is not treated as a failure.
"""

import os

import boto3
from botocore.exceptions import ClientError

bedrock_agent = boto3.client("bedrock-agent")

KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
DATA_SOURCE_ID = os.environ["DATA_SOURCE_ID"]


def handler(event: dict, context) -> dict:
    for record in event["Records"]:
        key = record["s3"]["object"]["key"]
        try:
            bedrock_agent.start_ingestion_job(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                dataSourceId=DATA_SOURCE_ID,
            )
            print(f"[OK] Started ingestion job for {key}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConflictException":
                print(f"[INFO] Ingestion job already running, {key} will be picked up on next sync")
            else:
                print(f"[ERROR] Failed to start ingestion job for {key}: {exc}")

    return {"statusCode": 200}
