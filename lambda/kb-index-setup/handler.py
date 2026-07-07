"""
CloudFormation custom resource. Bedrock Knowledge Base does not create its own
OpenSearch Serverless index — it must already exist with the exact vector/text/
metadata field mapping the Knowledge Base expects. This creates that index on
Create/Update and leaves it in place on Delete (dropping it isn't safe to do
automatically since the Knowledge Base may still reference it during teardown).
"""

import json
import os
import time
import urllib.request
from collections.abc import Callable

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import AuthorizationException, RequestError
from requests_aws4auth import AWS4Auth

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
VECTOR_DIM = 1024  # Cohere Embed English v3 output dimension

# AossAccessPolicy reports CREATE_COMPLETE in CloudFormation as soon as the
# policy document is accepted, but OpenSearch Serverless's data plane takes a
# few seconds to actually start honoring it — calling the index API right
# away reliably 403s even though the policy already grants this role access.
AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS = 6
AUTHORIZATION_PROPAGATION_BACKOFF_SECONDS = 10

# A freshly created index reports success to this Lambda before it's visible
# on every read path in AOSS. AskLoreKnowledgeBase (DependsOn: AossKbIndex)
# does its own existence check against the index when it's created, and that
# check 404s ("no such index") if it runs too soon after creation — so this
# custom resource must not report SUCCESS to CloudFormation until the index
# has had time to settle, not just until the create call itself returned.
INDEX_SETTLE_SECONDS = 30


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


def _call_with_authorization_retry[T](func: Callable[[], T]) -> T:
    for attempt in range(1, AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS + 1):
        try:
            return func()
        except AuthorizationException:
            if attempt == AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS:
                raise
            print(f"[INFO] AOSS data access policy not yet propagated, retrying ({attempt}/{AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS})")
            time.sleep(AUTHORIZATION_PROPAGATION_BACKOFF_SECONDS)


def ensure_index(client: OpenSearch) -> None:
    if _call_with_authorization_retry(lambda: client.indices.exists(index=INDEX_NAME)):
        return
    _create_index(client)
    time.sleep(INDEX_SETTLE_SECONDS)


def _create_index(client: OpenSearch) -> None:
    body = {
        "settings": {"index.knn": True},
        "mappings": {
            "properties": {
                "vector": {
                    "type": "knn_vector",
                    "dimension": VECTOR_DIM,
                    "method": {
                        "name": "hnsw",
                        # OpenSearch Serverless's k-NN plugin only ships the faiss
                        # engine — nmslib requires bundled native libraries that
                        # aren't available in the serverless runtime, and Bedrock
                        # rejects the index at KnowledgeBase-creation time if used.
                        "engine": "faiss",
                        "space_type": "cosinesimil",
                    },
                },
                "text": {"type": "text"},
                "metadata": {"type": "text", "index": False},
            }
        },
    }

    def _create() -> None:
        try:
            client.indices.create(index=INDEX_NAME, body=body)
        except RequestError as exc:
            if "resource_already_exists_exception" not in str(exc).lower():
                raise

    _call_with_authorization_retry(_create)


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
