"""
Invoked by API Gateway POST /query.
Calls Bedrock's RetrieveAndGenerate against the Knowledge Base: it embeds the
query, runs vector search over the Knowledge Base's OpenSearch Serverless
index, and generates a grounded answer in one call. citations[] map back to
the S3 objects actually used — sources in the response are only those.
"""

import json
import os

import boto3

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")

KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
GENERATION_MODEL_ARN = os.environ["GENERATION_MODEL_ARN"]


def retrieve_and_generate(query: str) -> tuple[str, list[dict]]:
    resp = bedrock_agent_runtime.retrieve_and_generate(
        input={"text": query},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                "modelArn": GENERATION_MODEL_ARN,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 5}
                },
            },
        },
    )
    answer = resp["output"]["text"]

    sources: list[dict] = []
    seen_uris: set[str] = set()
    for citation in resp.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            uri = ref.get("location", {}).get("s3Location", {}).get("uri")
            if not uri or uri in seen_uris:
                continue
            seen_uris.add(uri)
            sources.append({
                "doc_title": uri.rsplit("/", 1)[-1],
                "source_key": uri,
            })

    return answer, sources


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "request body must be valid JSON"})
    query = body.get("query", "").strip()
    if not query:
        return _resp(400, {"error": "query is required"})

    try:
        answer, sources = retrieve_and_generate(query)
        return _resp(200, {"answer": answer, "sources": sources})
    except Exception as exc:
        print(f"Unhandled error: {exc}")
        return _resp(500, {"error": "Internal server error"})
