"""
Step 1.6 — RetrievalLambda
Invoked by API Gateway POST /query.
Embeds the query, runs kNN search on OpenSearch (top-5), passes retrieved
chunks to Bedrock Claude with a grounded prompt, returns {answer, sources}.

Before deploying, add opensearch-py and requests-aws4auth to
lambda/retrieval/requirements.txt and re-package.
"""

import json
import os

import boto3

bedrock = boto3.client("bedrock-runtime")

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
GENERATION_MODEL_ID = os.environ["GENERATION_MODEL_ID"]

SYSTEM_PROMPT = (
    "You are AskLore, an internal knowledge assistant. "
    "Answer ONLY using the provided context. "
    "If the context is insufficient, say so explicitly. "
    "Cite the source document(s) you used in your answer."
)


def embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text[:8192]}),
    )
    return json.loads(resp["body"].read())["embedding"]


def knn_search(vector: list[float], top_k: int = 5) -> list[dict]:
    # TODO: implement using opensearch-py + requests-aws4auth
    # client.search(index=INDEX_NAME, body={
    #   "size": top_k,
    #   "query": {"knn": {"vector": {"vector": vector, "k": top_k}}}
    # })
    raise NotImplementedError("OpenSearch kNN search not yet implemented")


def generate(query: str, chunks: list[dict]) -> str:
    context = "\n\n".join(
        f"[{i + 1}] {c.get('doc_title', 'Unknown')} ({c.get('source_key', '')})\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    resp = bedrock.invoke_model(
        modelId=GENERATION_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query}",
                }
            ],
        }),
    )
    body = json.loads(resp["body"].read())
    return body["content"][0]["text"]


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
        answer = generate(query, chunks)
        sources = [
            {"doc_title": c.get("doc_title"), "source_key": c.get("source_key")}
            for c in chunks
        ]
        return _resp(200, {"answer": answer, "sources": sources})
    except NotImplementedError as exc:
        return _resp(501, {"error": str(exc)})
    except Exception as exc:
        print(f"Unhandled error: {exc}")
        return _resp(500, {"error": "Internal server error"})
