"""
Step 1.5 — EmbeddingLambda
Triggered by s3:ObjectCreated (suffix=chunks.json) on asklore-processed.
For each chunk: calls Bedrock Cohere Embed English v3 (1024-dim),
then indexes vector + metadata into OpenSearch Serverless.
"""

import json
import os
import time
from urllib.parse import unquote_plus

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError
from requests_aws4auth import AWS4Auth

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
VECTOR_DIM = 1024  # Cohere Embed English v3 output dimension

# ── OpenSearch client ─────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    # Credentials are fetched fresh each invocation — frozen creds expire after
    # ~1 hour and would 403 on warm Lambda containers that live longer than that.
    region = os.environ.get("AWS_REGION", "us-west-2")
    creds = boto3.session.Session().get_credentials().get_frozen_credentials()
    auth = AWS4Auth(
        creds.access_key,
        creds.secret_key,
        region,
        "aoss",
        session_token=creds.token,
    )
    host = OPENSEARCH_ENDPOINT.replace("https://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


# ── Index management ──────────────────────────────────────────────────────────

def ensure_index(client: OpenSearch) -> None:
    """Create the k-NN index if it does not already exist."""
    try:
        if client.indices.exists(index=INDEX_NAME):
            return
    except Exception:
        pass

    body = {
        "settings": {"index.knn": True},
        "mappings": {
            "properties": {
                "vector": {
                    "type": "knn_vector",
                    "dimension": VECTOR_DIM,
                    "method": {
                        "name": "hnsw",
                        "engine": "nmslib",
                        "space_type": "cosinesimil",
                        "parameters": {"ef_construction": 512, "m": 16},
                    },
                },
                "text": {"type": "text"},
                "section_title": {"type": "text"},
                "chunk_id": {"type": "keyword"},
                "source_key": {"type": "keyword"},
                "domain": {"type": "keyword"},
                "doc_title": {"type": "keyword"},
                "upload_date": {"type": "keyword"},
            }
        },
    }
    try:
        client.indices.create(index=INDEX_NAME, body=body)
        print(f"[OK] Created index '{INDEX_NAME}'")
    except RequestError as exc:
        if "resource_already_exists_exception" in str(exc).lower():
            print(f"[INFO] Index '{INDEX_NAME}' already exists (race condition — OK)")
        else:
            raise


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """Cohere Embed English v3 — input_type=search_document for indexing."""
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({
            "texts": [text[:2048]],
            "input_type": "search_document",
        }),
    )
    return json.loads(resp["body"].read())["embeddings"][0]


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    client = get_os_client()
    ensure_index(client)

    for record in event["Records"]:
        key = unquote_plus(record["s3"]["object"]["key"])
        try:
            bucket = record["s3"]["bucket"]["name"]

            obj = s3.get_object(Bucket=bucket, Key=key)
            chunks: list[dict] = json.loads(obj["Body"].read())
            print(f"Embedding {len(chunks)} chunks from {key}")

            # AOSS does not support explicit document IDs; let it auto-generate.
            # Idempotent upsert by chunk_id is deferred to Phase 2 dedup logic.
            bulk_body = []
            for chunk in chunks:
                vector = embed(chunk["text"])
                bulk_body.append({"index": {"_index": INDEX_NAME}})
                bulk_body.append({
                    "vector": vector,
                    "text": chunk["text"],
                    "chunk_id": chunk["chunk_id"],
                    "source_key": chunk["source_key"],
                    "domain": chunk["domain"],
                    "doc_title": chunk["doc_title"],
                    "section_title": chunk.get("section_title", ""),
                    "upload_date": chunk.get("upload_date", ""),
                })

            resp = client.bulk(body=bulk_body)
            errors = [item for item in resp.get("items", []) if "error" in item.get("index", {})]
            if errors:
                print(f"[WARN] {len(errors)} bulk index errors: {errors[:2]}")
            indexed = len(bulk_body) // 2 - len(errors)
            print(f"[OK] {key} → {indexed} chunks indexed into '{INDEX_NAME}'")
        except Exception as exc:
            print(f"[ERROR] Failed to process {key}: {exc}")
