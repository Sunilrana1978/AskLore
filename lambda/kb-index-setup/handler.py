"""
CloudFormation custom resource. Bedrock Knowledge Base does not create its own
OpenSearch Serverless index — it must already exist with the exact vector/text/
metadata field mapping the Knowledge Base expects. This creates that index on
Create/Update and leaves it in place on Delete (dropping it isn't safe to do
automatically since the Knowledge Base may still reference it during teardown).
"""

import json
import os
import urllib.request

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError
from requests_aws4auth import AWS4Auth

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
VECTOR_DIM = 1024  # Cohere Embed English v3 output dimension


def get_os_client() -> OpenSearch:
    # Credentials are fetched fresh each invocation — frozen creds expire after
    # ~1 hour and would 403 on warm Lambda containers that live longer than that.
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
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


def ensure_index(client: OpenSearch) -> None:
    if client.indices.exists(index=INDEX_NAME):
        return
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
                    },
                },
                "text": {"type": "text"},
                "metadata": {"type": "text", "index": False},
            }
        },
    }
    try:
        client.indices.create(index=INDEX_NAME, body=body)
    except RequestError as exc:
        if "resource_already_exists_exception" not in str(exc).lower():
            raise


def send_response(event: dict, context, status: str, reason: str = "") -> None:
    body = json.dumps({
        "Status": status,
        "Reason": reason or f"See CloudWatch Logs: {context.log_stream_name}",
        "PhysicalResourceId": event.get("PhysicalResourceId", INDEX_NAME),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
    }).encode()
    req = urllib.request.Request(
        url=event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": ""},
    )
    urllib.request.urlopen(req)


def handler(event: dict, context) -> dict:
    try:
        if event["RequestType"] in ("Create", "Update"):
            ensure_index(get_os_client())
        send_response(event, context, "SUCCESS")
        return {"Status": "SUCCESS"}
    except Exception as exc:
        print(f"[ERROR] {exc}")
        send_response(event, context, "FAILED", reason=str(exc))
        return {"Status": "FAILED", "Reason": str(exc)}
