"""
Invoked by API Gateway POST /query.
Calls Bedrock's Retrieve against the Knowledge Base for vector search only,
then generates a grounded answer with Gemini using the retrieved chunks as
context. sources returned are the retrieved chunks themselves (not filtered
by which ones Gemini's answer actually drew on).
"""

import json
import os

import boto3
from google import genai
from google.genai import types

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
secretsmanager = boto3.client("secretsmanager")

KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
GEMINI_MODEL_ID = os.environ["GEMINI_MODEL_ID"]
GEMINI_API_KEY_SECRET_ARN = os.environ["GEMINI_API_KEY_SECRET_ARN"]

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        api_key = secretsmanager.get_secret_value(SecretId=GEMINI_API_KEY_SECRET_ARN)["SecretString"]
        _gemini_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=0.5,
                    max_delay=2.0,
                    http_status_codes=[429],
                )
            ),
        )
    return _gemini_client


def retrieve(query: str) -> list[dict]:
    resp = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
    )
    return resp.get("retrievalResults", [])


def build_sources(results: list[dict]) -> list[dict]:
    sources: list[dict] = []
    seen_uris: set[str] = set()
    for result in results:
        uri = result.get("location", {}).get("s3Location", {}).get("uri")
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        sources.append({"doc_title": uri.rsplit("/", 1)[-1], "source_key": uri})
    return sources


def generate_answer(query: str, results: list[dict]) -> str:
    context = "\n\n".join(r["content"]["text"] for r in results)
    prompt = (
        "Answer the question using only the context below. If the context "
        "doesn't contain the answer, say you don't have enough information.\n\n"
        f"Context:\n{context}\n\nQuestion: {query}\n\nAnswer:"
    )
    response = _get_gemini_client().models.generate_content(model=GEMINI_MODEL_ID, contents=prompt)
    return response.text or ""


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
        results = retrieve(query)
        answer = generate_answer(query, results)
        sources = build_sources(results)
        return _resp(200, {"answer": answer, "sources": sources})
    except Exception as exc:
        print(f"Unhandled error: {exc}")
        return _resp(500, {"error": "Internal server error"})
