#!/usr/bin/env python3
"""
One-shot local indexer: reads all chunks.json files from asklore-processed,
embeds each chunk via Bedrock Cohere Embed v3, and indexes into OpenSearch Serverless.

Runs sequentially with a 1s delay between Bedrock calls to stay under TPS quota.
Use this to seed the index without fighting Lambda concurrency limits.

Usage:
    # Read values from live stack outputs (recommended)
    python scripts/index-all.py

    # Or override via env vars
    REGION=us-east-1 python scripts/index-all.py
"""

import json
import os
import random
import subprocess
import sys
import time

import boto3
from botocore.config import Config
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError
from requests_aws4auth import AWS4Auth

STACK_NAME = os.environ.get("STACK_NAME", "asklore-stack")
REGION = os.environ.get("REGION") or boto3.session.Session().region_name or "us-west-2"
INDEX_NAME = os.environ.get("INDEX_NAME", "asklore-knowledge")
EMBEDDING_MODEL_ID = os.environ.get("EMBEDDING_MODEL_ID", "cohere.embed-english-v3")
VECTOR_DIM = 1024  # Cohere Embed English v3


def _stack_output(key: str) -> str:
    """Read a CloudFormation stack output by key."""
    result = subprocess.run(
        [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", STACK_NAME,
            "--region", REGION,
            "--query", f"Stacks[0].Outputs[?OutputKey==`{key}`].OutputValue",
            "--output", "text",
        ],
        capture_output=True, text=True, check=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"Stack output '{key}' not found in stack '{STACK_NAME}'")
    return value


def _resolve_config() -> tuple[str, str]:
    """Return (processed_bucket, opensearch_endpoint), preferring env vars over stack outputs."""
    bucket = os.environ.get("PROCESSED_BUCKET") or _stack_output("ProcessedBucketName")
    endpoint = os.environ.get("OPENSEARCH_ENDPOINT") or _stack_output("CollectionEndpoint")
    return bucket, endpoint


PROCESSED_BUCKET, OPENSEARCH_ENDPOINT = _resolve_config()

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION,
                       config=Config(retries={"max_attempts": 3, "mode": "standard"}))


def get_os_client() -> OpenSearch:
    creds = boto3.session.Session().get_credentials().get_frozen_credentials()
    auth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "aoss",
                    session_token=creds.token)
    host = OPENSEARCH_ENDPOINT.replace("https://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


def ensure_index(client: OpenSearch) -> None:
    try:
        if client.indices.exists(index=INDEX_NAME):
            print(f"[INFO] Index '{INDEX_NAME}' already exists")
            return
    except Exception:
        pass
    body = {
        "settings": {"index.knn": True},
        "mappings": {"properties": {
            "vector": {"type": "knn_vector", "dimension": VECTOR_DIM,
                       "method": {"name": "hnsw", "engine": "nmslib",
                                  "space_type": "cosinesimil",
                                  "parameters": {"ef_construction": 512, "m": 16}}},
            "text": {"type": "text"},
            "section_title": {"type": "text"},
            "chunk_id": {"type": "keyword"},
            "source_key": {"type": "keyword"},
            "domain": {"type": "keyword"},
            "doc_title": {"type": "keyword"},
            "upload_date": {"type": "keyword"},
        }},
    }
    try:
        client.indices.create(index=INDEX_NAME, body=body)
        print(f"[OK] Created index '{INDEX_NAME}'")
    except RequestError as e:
        if "resource_already_exists_exception" in str(e).lower():
            print(f"[INFO] Index already exists")
        else:
            raise


def embed(text: str) -> list[float]:
    delay = 3.0
    for attempt in range(8):
        try:
            resp = bedrock.invoke_model(
                modelId=EMBEDDING_MODEL_ID,
                body=json.dumps({"texts": [text[:2048]], "input_type": "search_document"}),
            )
            return json.loads(resp["body"].read())["embeddings"][0]
        except bedrock.exceptions.ThrottlingException:
            if attempt == 7:
                raise
            wait = delay + random.uniform(0, delay * 0.5)
            print(f"  [THROTTLED] attempt {attempt+1}/8, sleeping {wait:.1f}s", flush=True)
            time.sleep(wait)
            delay = min(delay * 2, 60.0)


def list_chunk_keys() -> list[str]:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=PROCESSED_BUCKET):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("chunks.json"):
                keys.append(obj["Key"])
    return sorted(keys)


def main():
    client = get_os_client()
    ensure_index(client)

    keys = list_chunk_keys()
    print(f"\nFound {len(keys)} chunks.json files to index\n")

    total_indexed = 0
    for i, key in enumerate(keys, 1):
        obj = s3.get_object(Bucket=PROCESSED_BUCKET, Key=key)
        chunks = json.loads(obj["Body"].read())
        print(f"[{i:02d}/{len(keys)}] {key} — {len(chunks)} chunk(s)")

        for chunk in chunks:
            vector = embed(chunk["text"])
            client.index(
                index=INDEX_NAME,
                id=chunk["chunk_id"],
                body={
                    "vector": vector,
                    "text": chunk["text"],
                    "chunk_id": chunk["chunk_id"],
                    "source_key": chunk["source_key"],
                    "domain": chunk["domain"],
                    "doc_title": chunk["doc_title"],
                    "section_title": chunk.get("section_title", ""),
                    "upload_date": chunk.get("upload_date", ""),
                },
            )
            total_indexed += 1
            print(f"    indexed chunk_id={chunk['chunk_id']}", flush=True)
            time.sleep(1.0)  # stay under Bedrock TPS quota

        print(f"    [OK] {len(chunks)} chunks indexed\n")

    print(f"Done — {total_indexed} chunks indexed into '{INDEX_NAME}'")

    # Verify doc count
    count = client.count(index=INDEX_NAME)["count"]
    print(f"Index '{INDEX_NAME}' now contains {count} documents")


if __name__ == "__main__":
    main()
