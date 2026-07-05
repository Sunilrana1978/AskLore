"""
Step 1.6 — RetrievalLambda
Invoked by API Gateway POST /query.
Embeds the query, runs kNN search on OpenSearch Serverless (top-5),
passes retrieved chunks to Cohere Command R+ via Bedrock, returns {answer, sources}.
Command R+ accepts a documents[] array and returns citations[] that map directly
back to those documents — sources in the response are only actually-cited docs.
"""

import json
import os

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

bedrock = boto3.client("bedrock-runtime")

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
GENERATION_MODEL_ID = os.environ["GENERATION_MODEL_ID"]

PREAMBLE = (
    "You are AskLore, an internal knowledge assistant. "
    "Answer ONLY using the provided documents. "
    "If the documents are insufficient, say so explicitly. "
    "Always cite the documents you used."
)

_os_client: OpenSearch | None = None


# ── OpenSearch client ─────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    global _os_client
    if _os_client is not None:
        return _os_client

    region = os.environ.get("AWS_REGION", "us-east-1")
    creds = boto3.session.Session().get_credentials().get_frozen_credentials()
    auth = AWS4Auth(
        creds.access_key,
        creds.secret_key,
        region,
        "aoss",
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


# ── Core operations ───────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """Cohere Embed English v3 — input_type=search_query for retrieval."""
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({
            "texts": [text[:2048]],
            "input_type": "search_query",
        }),
    )
    return json.loads(resp["body"].read())["embeddings"][0]


def knn_search(vector: list[float], top_k: int = 5) -> list[dict]:
    client = get_os_client()
    resp = client.search(
        index=INDEX_NAME,
        body={
            "size": top_k,
            "query": {
                "knn": {
                    "vector": {
                        "vector": vector,
                        "k": top_k,
                    }
                }
            },
            "_source": ["text", "doc_title", "source_key", "section_title", "domain", "chunk_id"],
        },
    )
    hits = resp.get("hits", {}).get("hits", [])
    return [hit["_source"] for hit in hits]


def generate(query: str, chunks: list[dict]) -> tuple[str, list[int]]:
    """Call Command R+ with grounded documents; return (answer, cited_chunk_indices)."""
    documents = [
        {"title": c.get("doc_title", "Unknown"), "snippet": c["text"]}
        for c in chunks
    ]
    resp = bedrock.invoke_model(
        modelId=GENERATION_MODEL_ID,
        body=json.dumps({
            "message": query,
            "preamble": PREAMBLE,
            "documents": documents,
            "max_tokens": 1024,
            "temperature": 0.1,
        }),
    )
    body = json.loads(resp["body"].read())
    # citations[].document_ids are strings like "doc_0", "doc_1", …
    cited = {
        int(doc_id.split("_")[1])
        for citation in body.get("citations", [])
        for doc_id in citation.get("document_ids", [])
    }
    return body["text"], sorted(cited)


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    query = body.get("query", "").strip()
    if not query:
        return _resp(400, {"error": "query is required"})

    try:
        vector = embed(query)
        chunks = knn_search(vector)
        if not chunks:
            return _resp(200, {"answer": "No relevant documents found for your query.", "sources": []})
        answer, cited_indices = generate(query, chunks)
        sources = [
            {"doc_title": chunks[i].get("doc_title"), "source_key": chunks[i].get("source_key")}
            for i in cited_indices if i < len(chunks)
        ]
        return _resp(200, {"answer": answer, "sources": sources})
    except Exception as exc:
        print(f"Unhandled error: {exc}")
        return _resp(500, {"error": "Internal server error"})
