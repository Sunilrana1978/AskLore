"""
Step 1.5 — EmbeddingLambda
Triggered by s3:ObjectCreated (suffix=chunks.json) on asklore-processed.
Reads each chunk, calls Bedrock Titan Embeddings v2, and indexes the
embedding + metadata into OpenSearch Serverless.

Before deploying, add opensearch-py and requests-aws4auth to
lambda/embedding/requirements.txt and re-package with
`aws cloudformation package`.
"""

import json
import os
from urllib.parse import unquote_plus

import boto3

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX_NAME = os.environ["INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]


def embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text[:8192]}),  # Titan v2 max input tokens
    )
    return json.loads(resp["body"].read())["embedding"]


def index_chunk(chunk: dict, vector: list[float]):
    # TODO: implement using opensearch-py + requests-aws4auth
    # from opensearchpy import OpenSearch, RequestsHttpConnection
    # from requests_aws4auth import AWS4Auth
    #
    # auth = AWS4Auth(region=os.environ["AWS_REGION"], service="aoss", ...)
    # client = OpenSearch(hosts=[OPENSEARCH_ENDPOINT], http_auth=auth,
    #                     use_ssl=True, connection_class=RequestsHttpConnection)
    # client.index(index=INDEX_NAME, body={"vector": vector, **chunk})
    raise NotImplementedError("OpenSearch indexing not yet implemented")


def handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        obj = s3.get_object(Bucket=bucket, Key=key)
        chunks = json.loads(obj["Body"].read())
        print(f"Embedding {len(chunks)} chunks from {key}")

        for chunk in chunks:
            vector = embed(chunk["text"])
            try:
                index_chunk(chunk, vector)
            except NotImplementedError as exc:
                print(f"Skipping index write: {exc}")
                break
