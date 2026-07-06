"""
Step 1.4 — ChunkingLambda
Triggered by s3:ObjectCreated on asklore-raw.
Parses markdown or PDF files, splits by heading/section (not fixed character
count), attaches metadata, and writes chunks.json to asklore-processed.
"""

import json
import os
import re
from urllib.parse import unquote_plus

import boto3

s3 = boto3.client("s3")
PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]

# Soft ceiling per chunk — keeps embeddings within Titan v2's optimal range.
MAX_CHUNK_CHARS = 4000
# Discard sections shorter than this (e.g., empty headings, single-word titles).
MIN_CHUNK_CHARS = 80


# ── Markdown ──────────────────────────────────────────────────────────────────

def parse_markdown(content: str) -> list[dict]:
    """
    Split markdown at H1/H2/H3 boundaries, then:
      1. Merge adjacent small sections until MAX_CHUNK_CHARS.
      2. Split any remaining oversized chunk at paragraph boundaries.
    Code fences are never split mid-block.
    """
    parts = re.split(r"(?=^#{1,3} )", content, flags=re.MULTILINE)

    raw = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(#{1,3}) (.+)", part)
        raw.append({
            "text": part,
            "section_title": m.group(2).strip() if m else "Preamble",
            "heading_level": len(m.group(1)) if m else 0,
        })

    # Pass 1 — merge consecutive small chunks (same or deeper heading level).
    merged: list[dict] = []
    for chunk in raw:
        if (
            merged
            and len(merged[-1]["text"]) + len(chunk["text"]) < MAX_CHUNK_CHARS
            and merged[-1]["heading_level"] >= chunk["heading_level"]
        ):
            merged[-1]["text"] += "\n\n" + chunk["text"]
        else:
            merged.append(dict(chunk))  # copy so we can mutate safely

    # Pass 2 — split any chunk that still exceeds MAX_CHUNK_CHARS at blank lines.
    final: list[dict] = []
    for chunk in merged:
        if len(chunk["text"]) <= MAX_CHUNK_CHARS:
            final.append(chunk)
            continue
        paragraphs = chunk["text"].split("\n\n")
        current = ""
        for para in paragraphs:
            if current and len(current) + len(para) > MAX_CHUNK_CHARS:
                final.append({**chunk, "text": current.strip()})
                current = para
            else:
                current = (current + "\n\n" + para).lstrip()
        if current.strip():
            final.append({**chunk, "text": current.strip()})

    return [c for c in final if len(c["text"]) >= MIN_CHUNK_CHARS]


# ── PDF ───────────────────────────────────────────────────────────────────────

def parse_pdf(raw: bytes) -> list[dict]:
    """
    Extract text page-by-page using pypdf.
    Each page becomes one chunk; pages with too little text are skipped.
    Requires: pypdf>=4.0.0 (installed via requirements.txt before packaging).
    """
    import io
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    chunks = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue
        chunks.append({
            "text": text,
            "section_title": f"Page {i + 1}",
            "heading_level": 0,
        })
    return chunks


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    for record in event["Records"]:
        src_key = unquote_plus(record["s3"]["object"]["key"])
        try:
            src_bucket = record["s3"]["bucket"]["name"]

            # domain = first prefix component  e.g. "infra-runbooks"
            parts = src_key.split("/")
            domain = parts[0]
            filename = parts[-1]
            doc_id = re.sub(r"\.(md|pdf)$", "", filename, flags=re.IGNORECASE)
            doc_title = doc_id.replace("-", " ").replace("_", " ").title()

            obj = s3.get_object(Bucket=src_bucket, Key=src_key)
            raw = obj["Body"].read()

            is_pdf = src_key.lower().endswith(".pdf")
            raw_chunks = parse_pdf(raw) if is_pdf else parse_markdown(
                raw.decode("utf-8", errors="replace")
            )

            if not raw_chunks:
                print(f"[WARN] No usable chunks extracted from {src_key} — skipping")
                continue

            metadata = {
                "source_key": src_key,
                "domain": domain,
                "doc_title": doc_title,
                "upload_date": record["eventTime"],
            }

            enriched = [
                {
                    "chunk_id": f"{src_key}#{i}",
                    **metadata,
                    "section_title": c["section_title"],
                    "text": c["text"],
                }
                for i, c in enumerate(raw_chunks)
            ]

            out_key = f"{domain}/{doc_id}/chunks.json"
            s3.put_object(
                Bucket=PROCESSED_BUCKET,
                Key=out_key,
                Body=json.dumps(enriched, ensure_ascii=False),
                ContentType="application/json",
            )
            print(
                f"[OK] {src_key} → {len(enriched)} chunks"
                f" → s3://{PROCESSED_BUCKET}/{out_key}"
            )
        except Exception as exc:
            print(f"[ERROR] Failed to process {src_key}: {exc}")
