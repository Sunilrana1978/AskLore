"""
Step 1.4 — ChunkingLambda
Triggered by s3:ObjectCreated on asklore-raw.
Parses the uploaded markdown (or PDF) file, chunks by heading/section,
attaches metadata, and writes chunks.json to asklore-processed.
"""

import json
import os
import re
from urllib.parse import unquote_plus

import boto3

s3 = boto3.client("s3")
PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]


def parse_markdown(content: str) -> list[dict]:
    """Split markdown by H1/H2/H3 headings; each heading + body = one chunk."""
    parts = re.split(r"(?=^#{1,3} )", content, flags=re.MULTILINE)
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^#{1,3} (.+)", part)
        chunks.append({
            "text": part,
            "section_title": m.group(1) if m else "Preamble",
        })
    return chunks


def handler(event, context):
    for record in event["Records"]:
        src_bucket = record["s3"]["bucket"]["name"]
        src_key = unquote_plus(record["s3"]["object"]["key"])

        # Domain is the first prefix component: infra-runbooks/foo.md → infra-runbooks
        domain = src_key.split("/")[0]
        doc_id = src_key.replace("/", "_").replace(" ", "_")

        obj = s3.get_object(Bucket=src_bucket, Key=src_key)
        raw = obj["Body"].read()

        if src_key.lower().endswith(".pdf"):
            # TODO Phase 1 Step 1.4: add PDF text extraction (pdfminer.six or pypdf)
            print(f"PDF parsing not yet implemented — skipping {src_key}")
            continue

        raw_chunks = parse_markdown(raw.decode("utf-8"))

        metadata = {
            "source_key": src_key,
            "domain": domain,
            "doc_title": src_key.split("/")[-1].removesuffix(".md"),
            "upload_date": record["eventTime"],
        }

        enriched = [{"chunk_id": i, **metadata, **chunk} for i, chunk in enumerate(raw_chunks)]
        out_key = f"{domain}/{doc_id}/chunks.json"

        s3.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=out_key,
            Body=json.dumps(enriched),
            ContentType="application/json",
        )
        print(f"Wrote {len(enriched)} chunks → s3://{PROCESSED_BUCKET}/{out_key}")
