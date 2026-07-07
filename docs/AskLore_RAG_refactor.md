> **Superseded:** the dedup pipeline design here has been implemented and folded
> into `AskLore_Implementation_Plan.md` (Step 2.3) and `CLAUDE.md`, with a few
> deliberate adaptations to minimize rework of the already-built stack —
> `asklore-raw` (not `asklore-kb-documents`) remains the clean/production
> bucket, domain folders stay flat (`infra-runbooks/`, not `domain-<name>/`),
> and the eval harness in §7 is superseded by Phase 5's RAGAS suite rather than
> built alongside it. Kept here for historical context on the dedup design's
> motivation. Treat `AskLore_Implementation_Plan.md` as the source of truth.

# AskLore RAG POC — Implementation Plan

## 1. Goal
Build a POC RAG system on AWS Bedrock, starting with the managed Knowledge Base, with a clean upload/deduplication pipeline, then later spike a custom OpenSearch pipeline to compare.

---

## 2. Architecture Overview

```
User Upload
   │
   ▼
[Raw Landing S3 Bucket]  --(S3 Event Notification)-->  [Dedup Lambda]
                                                              │
                                                    Compute file hash
                                                              │
                                                    Check DynamoDB (hash lookup)
                                                              │
                                        ┌─────────────────────┴─────────────────────┐
                                        │                                           │
                                  Hash exists (duplicate)                   Hash new
                                        │                                           │
                                Delete from raw bucket                  Copy to Clean/Production bucket
                                                                                     │
                                                                          Insert hash record in DynamoDB
                                                                                     │
                                                                     Trigger Bedrock Knowledge Base Sync
                                                                                     │
                                                                          Knowledge Base re-embeds & indexes
```

---

## 3. S3 Bucket Setup

- **Raw landing bucket**: `asklore-raw-uploads`
  - Users/apps upload here first.
  - S3 Event Notification (ObjectCreated) → triggers Dedup Lambda.
- **Clean/production bucket**: `asklore-kb-documents`
  - Organized by domain subfolders, e.g.:
    ```
    asklore-kb-documents/
      domain-engineering/
      domain-hr/
      domain-security/
    ```
  - This is the bucket Bedrock Knowledge Base is pointed at.
- Enable **versioning** on the clean bucket for history/rollback safety.

---

## 4. Deduplication Pipeline

### DynamoDB Table: `asklore-file-hashes`
| Attribute | Type | Notes |
|---|---|---|
| `file_hash` (PK) | String | SHA-256 hash of file content |
| `filename` | String | Original filename |
| `domain` | String | Which domain folder it belongs to |
| `upload_date` | String (ISO) | When it was first ingested |
| `s3_path` | String | Final location in clean bucket |

**Why hash as PK:** enforces uniqueness at the content level — if two different filenames have identical content, the second write is naturally rejected as a duplicate.

### Lambda Logic (`dedup-lambda`)
1. Triggered by S3 `ObjectCreated` event on raw bucket.
2. Read the file from raw bucket.
3. Compute SHA-256 hash of content.
4. Query DynamoDB for `file_hash`.
   - **If found** → duplicate → delete object from raw bucket, log skip, stop.
   - **If not found** → proceed.
5. Copy file to clean bucket under the correct domain subfolder (domain can be inferred from raw upload path or metadata tag).
6. Write new record to DynamoDB (`file_hash`, `filename`, `domain`, `upload_date`, `s3_path`).
7. Trigger Bedrock Knowledge Base `StartIngestionJob` (sync).
8. Delete original from raw bucket (optional — keep if you want an audit trail).

**Known limitation:** hash-level dedup only catches exact full-file duplicates. Partial overlap (file B = file A + extra content) will still create some duplicate chunks in Bedrock's managed KB, since Bedrock has no chunk-level dedup. Acceptable for POC; flag as a reason to consider custom OpenSearch later.

---

## 5. Bedrock Knowledge Base Setup

1. Create Knowledge Base pointed at `asklore-kb-documents`.
2. Use Bedrock's default managed vector store and default chunking (not configurable — no control over chunk size/overlap in managed mode).
3. Sync behavior: **every sync re-processes the entire bucket**, not just changed files/folders. Keep this in mind as document volume grows — sync time will increase.
4. Automate sync: Dedup Lambda calls `bedrock-agent:StartIngestionJob` after a new file lands in the clean bucket, instead of manual console syncs.

---

## 6. Conversational Layer (App Side)

- Bedrock KB queries are stateless — no memory between calls.
- Your backend must:
  - Store conversation history (DB or session store).
  - On each new user message, pass prior turns + new question into the Bedrock call so the LLM has context.

---

## 7. Evaluation Harness

Build a small Python evaluation script:

1. Create a test set: domain questions + manually labeled "ground truth" relevant chunks/answers.
2. Run each question through the KB, capture retrieved chunks + generated answer.
3. Compute:
   - **Precision** = relevant chunks retrieved / total chunks retrieved
   - **Recall** = relevant chunks retrieved / total relevant chunks that exist
   - **Mean Reciprocal Rank (MRR)** = 1 / rank position of first relevant chunk
4. Store results (CSV or DynamoDB) to track quality over time as you tune prompts/docs.
5. Use failures to decide what to fix, in this order:
   - Prompt/system instructions first
   - Document content/quality second
   - Retrieval/chunking strategy last (signal to consider custom pipeline)

---

## 8. Ops & Monitoring

- **CloudWatch**: track query latency and Bedrock API cost.
- Set up a simple dashboard: avg response time, error rate, daily query volume.
- Periodically re-run the evaluation harness after doc updates to catch regressions.

---

## 9. POC → Custom Pipeline (Phase 2, for comparison)

Once managed Bedrock POC is working, spike a custom pipeline to compare:
- Lambda-based chunking (configurable size/overlap) per domain folder.
- Embeddings written to **OpenSearch Serverless**, one index per domain (recommended over single shared index for isolation and independent tuning).
- Custom retrieval logic with a **two-tier router**: classify query → domain → query that domain's index (or fan out across domains + merge/rank for cross-domain questions).
- Chunk-level dedup becomes possible here (hash per chunk, not just per file).

---

## 10. Naming Conventions Used
- `asklore-raw-uploads` — landing bucket
- `asklore-kb-documents` — clean/production bucket (Bedrock KB source)
- `asklore-file-hashes` — DynamoDB dedup table
- Domain subfolders: `domain-<name>/`

---

## 11. Tomorrow's Build Checklist
- [ ] Create `asklore-raw-uploads` and `asklore-kb-documents` S3 buckets
- [ ] Create `asklore-file-hashes` DynamoDB table
- [ ] Write `dedup-lambda` (hash check, copy, DynamoDB write, trigger sync)
- [ ] Wire S3 event notification (raw bucket → Lambda)
- [ ] Create Bedrock Knowledge Base pointed at clean bucket
- [ ] Test end-to-end: upload → dedup → sync → query
- [ ] Build small eval script with 10-15 test questions per domain
- [ ] Log precision/recall/MRR baseline
