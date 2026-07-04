"""
Step 1.5 — EmbeddingLambda
Triggered by s3:ObjectCreated (suffix=chunks.json) on asklore-processed.
For each chunk: calls Bedrock Titan Embeddings v2 to produce a 1024-dim
vector, then indexes vector + metadata into OpenSearch Serverless via
the opensearch-py client with AWS Signature V4 auth.
"""

import json
import os
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError
from requests_aws4auth import AWS4Auth

s3 = boto3.client("s3")
# adaptive mode: exponential backoff with jitter on ThrottlingException
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
)

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
VECTOR_DIM = 1024  # Titan Embeddings v2 default output dimension

# Module-level client cache — reused across warm Lambda invocations.
_os_client: OpenSearch | None = None


# ── OpenSearch client ─────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    global _os_client
    if _os_client is not None:
        return _os_client

    region = os.environ.get("AWS_REGION", "us-west-2")
    # get_frozen_credentials() returns a stable snapshot valid for this invocation.
    creds = boto3.session.Session().get_credentials().get_frozen_credentials()
    auth = AWS4Auth(
        creds.access_key,
        creds.secret_key,
        region,
        "aoss",                  # service name for OpenSearch Serverless
        session_token=creds.token,
    )

    host = OPENSEARCH_ENDPOINT.replace("https://", "").rstrip("/")
    _os_client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )
    return _os_client


# ── Index management ──────────────────────────────────────────────────────────

def ensure_index(client: OpenSearch) -> None:
    """Create the k-NN index if it does not already exist."""
    try:
        if client.indices.exists(index=INDEX_NAME):
            return
    except Exception:
        pass  # treat any check failure as "not found"

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
                # Full-text fields for future hybrid (BM25) search in Phase 3
                "text": {"type": "text"},
                "section_title": {"type": "text"},
                # Keyword fields for metadata filtering
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
        # Concurrent Lambda invocations may race to create the index — safe to ignore.
        if "resource_already_exists_exception" in str(exc).lower():
            print(f"[INFO] Index '{INDEX_NAME}' already exists (race condition — OK)")
        else:
            raise


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({
            "inputText": text[:8192],   # Titan v2 max input
            "dimensions": VECTOR_DIM,
            "normalize": True,          # unit-normalised vectors for cosine similarity
        }),
    )
    return json.loads(resp["body"].read())["embedding"]


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    client = get_os_client()
    ensure_index(client)

    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        obj = s3.get_object(Bucket=bucket, Key=key)
        chunks: list[dict] = json.loads(obj["Body"].read())
        print(f"Embedding {len(chunks)} chunks from {key}")

        indexed = 0
        for chunk in chunks:
            vector = embed(chunk["text"])

            client.index(
                index=INDEX_NAME,
                # Use chunk_id as the document ID so re-indexing the same
                # document is idempotent (overwrites rather than duplicates).
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
            indexed += 1

        print(f"[OK] {key} → {indexed} chunks indexed into '{INDEX_NAME}'")
