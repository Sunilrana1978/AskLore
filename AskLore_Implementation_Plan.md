# AskLore — Implementation Plan
### Internal Tribal-Knowledge RAG Assistant (AWS Cloud-Native)

**Source of truth:** S3 (documents dropped into domain-based prefixes, simulating Slack/Notion/GitHub exports)
**Core stack:** Amazon Bedrock (Knowledge Base, Embeddings, Guardrails), OpenSearch Serverless, Lambda, CloudFormation

> **Migration note:** Phase 1's original custom `ChunkingLambda` + `EmbeddingLambda` + manual kNN/Command R+ pipeline has been replaced by an `AWS::Bedrock::KnowledgeBase` (managed chunking, embedding, and OpenSearch indexing) plus `RetrieveAndGenerate` for the query path. Steps 1.4–1.7 below are rewritten to reflect this; several Phase 2/3 items are now provided natively by the Knowledge Base rather than requiring custom code — see the inline notes.
>
> **Second migration layer:** explicit content-hash dedup (SHA-256 + DynamoDB) now runs ahead of the Knowledge Base's `asklore-raw` data source, via a new `asklore-raw-uploads` landing bucket and `DedupLambda` (Step 2.3). This supersedes the "Knowledge Base sync handles dedup automatically" assumption the original Step 2.3 made — that assumption only holds for unchanged-object tracking on a fixed key, not for identical content re-uploaded under a new filename. KB data-source sync's own object-tracking still runs; it's now a secondary backstop, not the primary dedup mechanism.
>
> **Third migration layer:** the query path no longer calls `RetrieveAndGenerate`. `RetrievalLambda` now calls Bedrock `Retrieve` (vector search only) and hands the retrieved chunks to Gemini (`gemini-2.5-flash` via the `google-genai` SDK) for generation — see `docs/asklore-gemini-migration-plan.md`. Step 1.6 below is rewritten to match; references to Cohere Command R+ elsewhere in this doc describe the superseded design.
>
> **Fourth addition (beyond original scope):** a local Streamlit chat UI (`ui/app.py`, `make ui`) now sits in front of `POST /query` — sample questions, conversation history, cited sources. Not part of the original phase plan; documented in `README.md`.

---

## 0. Prerequisites

- [x] AWS account with Bedrock model access enabled (Cohere Embed English v3 — generation moved to Gemini AI Studio, no longer Bedrock Command R+)
- [x] AWS CLI configured with sufficient IAM permissions (S3, Lambda, OpenSearch, Bedrock, DynamoDB, CloudFormation)
- [x] CloudFormation toolchain set up locally (raw YAML, not CDK)
- [x] Decided: raw CloudFormation YAML (`template.yaml` at repo root)
- [x] GitHub repo initialized for the project (`asklore`)

---

## Phase 1 — Foundation & Single-Domain MVP
**Timeline: Weeks 1–2**
**Goal:** Drop a markdown/PDF file into S3 → get a grounded, cited answer back.

### Step 1.1 — Core Infrastructure (IaC)
- [x] Create CloudFormation stack skeleton
- [x] Provision `asklore-raw` S3 bucket (versioning enabled)
- [x] Provision OpenSearch Serverless collection (vector search type), reused as the Knowledge Base's vector store
- [x] Create IAM roles: `KnowledgeBaseRole`, `AossKbIndexLambdaRole`, `IngestionTriggerLambdaRole`, `RetrievalLambdaRole`
- [x] Provision `asklore-raw-uploads` S3 bucket (no versioning — transient landing zone, see Step 2.3)
- [x] Provision `asklore-file-hashes` DynamoDB table (on-demand billing, PK `file_hash`)
- [x] Create IAM role `DedupLambdaRole`
- [x] Deploy `DedupLambda` + matching `AWS::Logs::LogGroup`
- [x] Deploy stack, verify resources in console (`asklore-dev`, confirmed via `aws cloudformation describe-stacks` outputs)

### Step 1.2 — Seed Data (Domain 1: Infra Runbooks)
- [x] Write/curate 20–30 realistic markdown documents (e.g., "How to rotate an SSL cert", "Restarting the payment service", "On-call escalation steps")
- [x] Store locally under `seed-data/infra-runbooks/`
- [x] Upload to `s3://asklore-raw/infra-runbooks/`

### Step 1.3 — S3 Event Trigger
- [x] Configure `s3:ObjectCreated:*` event notification on `asklore-raw`
- [x] Trigger target: `IngestionTriggerLambda` (calls `bedrock-agent:StartIngestionJob`)

### Step 1.4 — Knowledge Base Ingestion (supersedes the original custom Chunking/Embedding Lambdas) ✅
- [x] `AWS::Bedrock::KnowledgeBase` with `VectorKnowledgeBaseConfiguration` (Cohere Embed English v3, 1024-dim) over the OpenSearch Serverless collection
- [x] `AWS::Bedrock::DataSource` pointing at `asklore-raw`, `FIXED_SIZE` chunking (300 tokens, 20% overlap) — chosen over preserving the heading-based custom chunker, trading exact section boundaries for far less code to maintain
- [x] `kb-index-setup` custom-resource Lambda creates the AOSS `vector`/`text`/`metadata` index the Knowledge Base requires before it can be created
- [x] Confirm an ingestion job reaches `COMPLETE` after uploading a seed doc (verified against `asklore-dev` — queries return content from ingested seed docs)

### Step 1.5 — ~~Embedding Pipeline~~ (removed — owned by the Knowledge Base)
No longer a separate step: embedding happens internally during Knowledge Base data-source sync (Step 1.4), not via a dedicated Lambda calling `InvokeModel`.

### Step 1.6 — Retrieval + Generation via Bedrock `Retrieve` + Gemini ✅
Rewritten from the original `RetrieveAndGenerate` design — see the "Third migration layer" note above and `docs/asklore-gemini-migration-plan.md`.
- [x] `RetrievalLambda`: accept query → `bedrock-agent-runtime:Retrieve` (vector search only, top-5 chunks) → Gemini `generate_content` (`gemini-2.5-flash`) with those chunks as prompt context
- [x] Return `{answer, sources: [{doc_title, source_key}]}`, built from the retrieved chunks' S3 locations (not filtered to only what Gemini cited — Gemini doesn't return Bedrock-style citations)
- [x] Confirm end-to-end answer with citation against a deployed stack

### Step 1.7 — Minimal API
- [x] API Gateway REST endpoint (`POST /query`) → RetrievalLambda
- [x] Test via curl with 5–10 sample questions once deployed

### Step 1.8 — Chat UI (beyond original scope)
- [x] Streamlit chat frontend (`ui/app.py`) over `POST /query` — sample questions, conversation history, cited sources; run via `make ui`

**✅ Phase 1 Done:** Deployed to `asklore-dev`; upload → ingest → cited query answer works end-to-end via both curl and the Streamlit UI.

---

## Phase 2 — Multi-Domain Ingestion
**Timeline: Weeks 3–4**
**Goal:** Four domains flowing into one searchable index with consistent metadata.

### Step 2.1 — Additional Domain Seed Content
- [ ] Write seed docs for `incident-postmortems/` (5–10 postmortem docs)
- [ ] Write seed docs for `product-docs/` (10–15 docs)
- [ ] Write seed docs for `onboarding-wiki/` (10–15 docs)
- [ ] Upload each to its respective `s3://asklore-raw-uploads/<domain>/` prefix — once Step 2.3 ships, `DedupLambda` copies deduped content into `asklore-raw/<domain>/` automatically; already-uploaded Phase 1 seed content in `asklore-raw/infra-runbooks/` does not need to be re-uploaded

### Step 2.2 — Unified Metadata Schema
- [ ] Attach `<file>.metadata.json` sidecars in S3 (Knowledge Base convention) tagging `{domain, doc_type, author, created_date, last_updated}` — domain is no longer auto-derived by a custom chunking Lambda, so this must be written explicitly per upload
- [ ] Confirm Knowledge Base ingestion picks up the sidecar metadata as filterable attributes

### Step 2.3 — Content-Hash Deduplication
Explicit dedup ahead of Knowledge Base ingestion, catching identical content re-uploaded under a different filename or key — something KB data-source sync's own unchanged-object tracking does not catch (infra provisioned in Step 1.1):
- [x] `DedupLambda`: SHA-256-hash each object landing in `asklore-raw-uploads`, conditionally write `{file_hash, filename, domain, upload_date, s3_path}` to `asklore-file-hashes` (DynamoDB) via a conditional `PutItem` (`attribute_not_exists(file_hash)`) to avoid a check-then-act race between near-simultaneous identical uploads
- [x] New hash → copy into `asklore-raw` under the same key/domain prefix (fires the existing `IngestionTriggerLambda` unmodified via the copy's `ObjectCreated:Copy` event); duplicate hash → delete from the landing bucket, no ingestion triggered
- [x] `.metadata.json` sidecars pass through unhashed (KB metadata-filter convention — a sidecar must exist alongside its document regardless of content)
- [x] Unit tests in `tests/unit/dedup/test_handler.py`
- [ ] Manual verification: upload the same file twice (different filenames) to `asklore-raw-uploads`, confirm exactly one copy lands in `asklore-raw` and one ingestion job fires

### Step 2.4 — Seed Conflicting/Recency Test Cases
- [ ] Deliberately create 2–3 pairs of documents on the same topic with different `last_updated` dates (one stale, one fresh) — to be used in Phase 3 recency testing

**✅ Phase 2 Done When:** All 4 domains are ingested, deduped, and queryable with consistent metadata.

---

## Phase 3 — Domain Routing, Recency & Multi-Turn
**Timeline: Weeks 5–6**
**Goal:** Accurate, domain-aware, recency-aware retrieval.

### Step 3.1 — Tier 1: Domain Classification Router
- [ ] Build Bedrock agent (or simple Lambda + Claude prompt) to classify incoming query into one or more domains
- [ ] Pass a `retrievalConfiguration.vectorSearchConfiguration.filter` on `domain` in the `RetrieveAndGenerate` call (Knowledge Base metadata filter, not a raw OpenSearch query)

### Step 3.2 — Tier 2: Hybrid Retrieval — superseded by Knowledge Base
`RetrieveAndGenerate`'s vector search already combines semantic retrieval over the managed index; no custom OpenSearch hybrid (kNN + BM25) query needs to be hand-built.

### Step 3.3 — Reranking — superseded by Knowledge Base
`retrievalConfiguration` supports an inline Bedrock Rerank reranking model config; set it there instead of a separate top-20-then-rerank Lambda step.

### Step 3.4 — Recency Weighting
- [ ] Implement custom score adjustment: boost score using `last_updated` (e.g., decay function)
- [ ] Test against the conflicting-doc pairs seeded in Phase 2 — verify fresher doc wins

### Step 3.5 — Multi-Turn Query Rewriting
- [ ] Lambda step: given conversation history + new query, rewrite ambiguous references ("what about staging?") into a standalone query before retrieval
- [ ] Store short conversation state in DynamoDB (session-based)

**✅ Phase 3 Done When:** Test queries prove domain routing, hybrid search, reranking, and recency weighting each work as designed.

---

## Phase 4 — Groundedness, Guardrails & Attribution
**Timeline: Weeks 7–8**
**Goal:** Every answer is safe, grounded, and traceable to a source.

### Step 4.1 — Bedrock Guardrails
- [ ] Create a Guardrail: PII detection/redaction, off-topic filtering, prompt-injection defense
- [ ] Attach Guardrail to the generation Lambda's Bedrock invocation

### Step 4.2 — Grounded Prompt Template
- [ ] Update system prompt: "Answer only using the provided context. If insufficient, say so explicitly."
- [ ] Require the model to output inline citations referencing chunk IDs

### Step 4.3 — Source Attribution in Response
- [ ] Map cited chunk IDs back to `{doc_title, source_key, section}`
- [ ] Return structured response: `{answer, sources: [...]}`

### Step 4.4 — Groundedness Scoring
- [ ] Post-generation Lambda: use Bedrock (Claude) as a judge to score whether each claim in the answer is supported by retrieved context
- [ ] Log score to DynamoDB for later analysis; flag low-groundedness answers

**✅ Phase 4 Done When:** Every response includes verifiable citations and a groundedness score.

---

## Phase 5 — Evaluation Framework
**Timeline: Weeks 9–10**
**Goal:** Quantify retrieval and generation quality; prevent regressions.

### Step 5.1 — Golden Dataset
- [ ] Write 30–50 Q&A pairs with known correct answers and expected source documents (leverage the seed docs you authored)
- [ ] Store as JSON in `asklore-eval` S3 bucket or repo

### Step 5.2 — RAGAS Integration
- [ ] Set up RAGAS (or equivalent) evaluation script
- [ ] Metrics: faithfulness, answer relevance, context precision, context recall
- [ ] Run as a Lambda or Step Function job against the golden dataset

### Step 5.3 — CI/CD Regression Gate
- [ ] Set up CodePipeline + CodeBuild
- [ ] On each deploy, run eval suite; fail pipeline if scores drop below threshold

### Step 5.4 — Feedback Loop
- [ ] Add thumbs up/down capture in API response handling
- [ ] Store feedback in DynamoDB, linked to trace ID / query ID

**✅ Phase 5 Done When:** You have a repeatable eval report and a CI gate that blocks bad deploys.

---

## Phase 6 — Observability, Cost & Production Hardening
**Timeline: Weeks 11–12**
**Goal:** Operable, monitorable, cost-aware system.

### Step 6.1 — Tracing
- [ ] Enable X-Ray across all Lambdas in the request chain (router → retrieval → generation)

### Step 6.2 — Dashboards
- [ ] Build CloudWatch dashboard: latency per stage, error rates, OpenSearch OCU usage, Bedrock token consumption

### Step 6.3 — Caching
- [ ] Add semantic cache layer (DynamoDB or ElastiCache) keyed on normalized query embeddings
- [ ] Skip retrieval+generation for near-duplicate recent queries

### Step 6.4 — Rate Limiting
- [ ] Configure API Gateway usage plans and throttling per API key/user

### Step 6.5 — Simulated Access Control
- [ ] Tag a subset of documents (e.g., under `onboarding-wiki/hr-sensitive/`) as restricted via `.metadata.json` sidecar files
- [ ] Pass a mock user role in the API request; enforce filtering via Knowledge Base metadata filters in the `RetrieveAndGenerate` call

### Step 6.6 — Cost Monitoring
- [ ] Set up AWS Budgets with alerts on Bedrock + OpenSearch Serverless spend
- [ ] Tag resources per domain for cost attribution

**✅ Phase 6 Done When:** The system runs continuously with visibility into cost, latency, and failures.

---

## AWS Service Reference Map

| Function | Service |
|---|---|
| Document landing/dedup | S3 (`asklore-raw-uploads`) + S3 Event Notifications + DynamoDB (`asklore-file-hashes`) |
| Document storage/trigger | S3 (`asklore-raw`) + S3 Event Notifications |
| Ingestion orchestration | Bedrock Knowledge Base + Data Source |
| Vector search | OpenSearch Serverless (managed by the Knowledge Base) |
| Embeddings & generation | Bedrock (Cohere Embed English v3, Cohere Command R+ via `RetrieveAndGenerate`) |
| Change detection | Explicit SHA-256 hash Lambda (`DedupLambda`) + DynamoDB (`asklore-file-hashes`); Knowledge Base data-source sync remains a secondary backstop |
| Metadata/cache/trace store | DynamoDB (Phase 3+ conversation state, Phase 4 groundedness scores) |
| IaC | CloudFormation |
| CI/CD + eval gating | CodePipeline + CodeBuild |
| Observability | CloudWatch, X-Ray |
| Guardrails | Bedrock Guardrails |
| API layer | API Gateway + Lambda |

---

## S3 Bucket Layout Reference

```
s3://asklore-raw-uploads/
└── <domain>/       # transient landing zone — DedupLambda copies new content
                     # into asklore-raw and deletes the landing copy within
                     # the same invocation (duplicates never leave this bucket)

s3://asklore-raw/
├── infra-runbooks/
├── incident-postmortems/
├── product-docs/
└── onboarding-wiki/
    └── hr-sensitive/       # used for Phase 6 access-control testing

s3://asklore-eval/
└── golden-qa-dataset.json
```

`asklore-processed/` no longer exists — the Knowledge Base ingests directly from `asklore-raw` and there is no intermediate `chunks.json` output.

---

## Suggested Next Action

Phase 1 is done — `asklore-dev` is deployed, ingestion is confirmed reaching `COMPLETE`, and end-to-end cited answers work via both curl and the Streamlit UI.

Phase 2 is in progress: `DedupLambda` is built, unit-tested, and deployed (Step 2.3), but only `infra-runbooks` has seed content. Next:
1. Close out Step 2.3's manual verification: upload the same file twice (different filenames) to `asklore-raw-uploads`, confirm via CloudWatch Logs (`/aws/lambda/asklore-dev-dedup`) that only the first copy lands in `asklore-raw` and triggers ingestion, and confirm the second is deleted from the landing bucket with a matching item in `asklore-file-hashes`.
2. **Step 2.1** — write and upload seed content for the remaining three domains (`incident-postmortems/`, `product-docs/`, `onboarding-wiki/`).
3. **Step 2.2** — attach `.metadata.json` sidecars for the unified metadata schema.
4. **Step 2.4** — seed conflicting/recency test-case doc pairs for Phase 3.
