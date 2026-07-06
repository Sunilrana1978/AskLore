# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AskLore is a greenfield internal tribal-knowledge RAG assistant built entirely on AWS cloud-native services. It ingests markdown/PDF documents dropped into S3 and serves grounded, cited answers via a REST API.

## Architecture

**Ingestion pipeline:** S3 upload (`asklore-raw`) → S3 Event → `ChunkingLambda` → `asklore-processed` → S3 Event → `EmbeddingLambda` → OpenSearch Serverless (`asklore-knowledge` index)

**Query pipeline:** `POST /query` → API Gateway → `RetrievalLambda` → Cohere Embed v3 (query vector) → OpenSearch kNN (top-5) → Cohere Command R+ (grounded generation with `documents[]`) → `{answer, sources}`

**Key AWS services:**

| Role | Service |
|---|---|
| Document storage + trigger | S3 (`asklore-raw`) + S3 Event Notifications |
| Chunked output | S3 (`asklore-processed`) — `<domain>/<doc_id>/chunks.json` |
| Embeddings | Bedrock Cohere Embed English v3 (`cohere.embed-english-v3`, 1024-dim) |
| Generation | Bedrock Cohere Command R+ (`cohere.command-r-plus-v1:0`) |
| Vector search | OpenSearch Serverless — VECTORSEARCH collection `asklore` |
| IaC | CloudFormation (`template.yaml` at repo root) — never CDK/SAM/Terraform |
| Observability | CloudWatch + X-Ray (Phase 6) |
| Guardrails | Bedrock Guardrails (Phase 4) |

**Domain** is auto-derived from the first S3 prefix component (e.g. `infra-runbooks/` → domain `infra-runbooks`).

## Build & Deploy

**Prerequisites:**
- [`uv`](https://github.com/astral-sh/uv) installed
- AWS CLI configured
- Bedrock model access enabled for **Cohere Embed English v3** and **Cohere Command R+** (Bedrock console → Model access → Modify model access)

```bash
# One-time: create S3 bucket for CloudFormation artifacts
aws s3 mb s3://asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)

# Full build + deploy (rebuilds build/ from lambda/*/; installs deps via uv)
bash scripts/build-and-deploy.sh

# Build only (no deploy)
bash scripts/build-and-deploy.sh --build

# Deploy only (assumes build/ already exists)
bash scripts/build-and-deploy.sh --deploy

# View stack outputs (bucket names, AOSS endpoint, API URL)
aws cloudformation describe-stacks \
  --stack-name asklore-stack \
  --query "Stacks[0].Outputs" --output table
```

The build script (`scripts/build-and-deploy.sh`) cleans `build/`, copies each `lambda/*/handler.py`, installs `requirements.txt` deps via `uv pip install --target`, then runs `cloudformation package` + `deploy`. **Always run the full script after editing any Lambda handler or `requirements.txt`** — the deployed code lives in `build/`, not `lambda/` directly.

## Lambda Functions

| Function | Trigger | Key logic |
|---|---|---|
| `ChunkingLambda` | S3 ObjectCreated on `asklore-raw` | Splits markdown by heading / PDF by page; MIN 80 chars, MAX 4000 chars per chunk; writes `chunks.json` to `asklore-processed` |
| `EmbeddingLambda` | S3 ObjectCreated (`chunks.json`) on `asklore-processed` | Calls Cohere Embed v3 with `input_type=search_document`; bulk-indexes via AOSS `_bulk` API |
| `RetrievalLambda` | API Gateway `POST /query` | Embeds query with `input_type=search_query`; kNN top-5; passes chunks as `documents[]` to Command R+; returns only actually-cited sources from `citations[]` |

`EmbeddingLambdaInvokeConfig` sets `MaximumRetryAttempts: 0` — Lambda will not auto-retry on Bedrock throttle.

## S3 Layout

```
asklore-raw-<account>-<region>/
├── infra-runbooks/
├── incident-postmortems/
├── product-docs/
└── onboarding-wiki/
    └── hr-sensitive/       # Phase 6 access-control testing

asklore-processed-<account>-<region>/
└── <domain>/<doc_id>/chunks.json

asklore-eval-<account>-<region>/
└── golden-qa-dataset.json  # Phase 5
```

## Key Design Decisions

- **IaC:** Raw CloudFormation YAML only (`template.yaml`). Never CDK, SAM, or Terraform.
- **Chunking:** Split by heading/section boundary, not fixed character count. Metadata attached: `{source_key, domain, doc_title, upload_date}`.
- **Embedding model:** Cohere Embed English v3 (`input_type` differs between indexing and retrieval — `search_document` vs `search_query`).
- **Generation model:** Cohere Command R+ uses the native `documents[]` + `preamble` schema (not a `messages[]` schema like Claude/Nova). Its `citations[]` response maps `doc_0`, `doc_1`, … back to the input documents array — sources returned are only chunks actually cited.
- **AOSS explicit IDs:** OpenSearch Serverless does not support explicit document IDs in single-document index calls; bulk-index auto-generates IDs. Idempotent upserts by `chunk_id` are deferred to Phase 2.
- **Dedup (Phase 2):** Content hash on S3 event → compare against DynamoDB `DocumentHashes` → skip re-embedding if unchanged.
- **Retrieval (Phase 3+):** top-20 hybrid search (kNN + BM25) → Bedrock Rerank → top-5 to generation.
- **Access control (Phase 6):** Enforced at OpenSearch query layer via metadata filters on user role, not at the API/UI layer.

## Implementation Phases

Detailed progress tracked in `AskLore_Implementation_Plan.md`.

- **Phase 1** — Foundation MVP: single domain, end-to-end query with citations (Steps 1.1–1.5 ✅; 1.6–1.7 pending Command R+ model access)
- **Phase 2** — Multi-domain ingestion + hash-based dedup (DynamoDB)
- **Phase 3** — Domain-classification router + hybrid search + Bedrock Rerank + recency weighting + multi-turn query rewriting
- **Phase 4** — Bedrock Guardrails, grounded prompts, groundedness scoring (Claude-as-judge)
- **Phase 5** — RAGAS evaluation suite, golden dataset, CI regression gate
- **Phase 6** — X-Ray tracing, CloudWatch dashboards, semantic cache, rate limiting, simulated RBAC, cost budgets
