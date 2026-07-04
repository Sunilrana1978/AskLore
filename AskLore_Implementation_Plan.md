# AskLore — Implementation Plan
### Internal Tribal-Knowledge RAG Assistant (AWS Cloud-Native)

**Source of truth:** S3 (documents dropped into domain-based prefixes, simulating Slack/Notion/GitHub exports)
**Core stack:** Amazon Bedrock (AgentCore, Embeddings, Guardrails), OpenSearch Serverless, Lambda, CloudFormation

---

## 0. Prerequisites

- [ ] AWS account with Bedrock model access enabled (Titan Embeddings, Claude, Rerank)
- [ ] AWS CLI configured with sufficient IAM permissions (S3, Lambda, OpenSearch, Bedrock, DynamoDB, CloudFormation)
- [ ] CDK or CloudFormation toolchain set up locally
- [ ] Decide: CDK (TypeScript/Python) vs. raw CloudFormation YAML — recommend CDK for faster iteration
- [ ] GitHub repo initialized for the project (`asklore`)

---

## Phase 1 — Foundation & Single-Domain MVP
**Timeline: Weeks 1–2**
**Goal:** Drop a markdown/PDF file into S3 → get a grounded, cited answer back.

### Step 1.1 — Core Infrastructure (IaC)
- [x] Create CloudFormation/CDK stack skeleton
- [x] Provision `asklore-raw` S3 bucket (versioning enabled)
- [x] Provision `asklore-processed` S3 bucket (for chunked/tagged output)
- [x] Provision OpenSearch Serverless collection (vector search type)
- [x] Create IAM roles: `IngestionLambdaRole`, `RetrievalLambdaRole`, `OpenSearchAccessRole`
- [x] Deploy stack, verify resources in console

### Step 1.2 — Seed Data (Domain 1: Infra Runbooks)
- [x] Write/curate 20–30 realistic markdown documents (e.g., "How to rotate an SSL cert", "Restarting the payment service", "On-call escalation steps")
- [x] Store locally under `seed-data/infra-runbooks/`
- [x] Upload to `s3://asklore-raw/infra-runbooks/`

### Step 1.3 — S3 Event Trigger
- [x] Configure `s3:ObjectCreated:*` event notification on `asklore-raw`
- [x] Trigger target: `ChunkingLambda`

### Step 1.4 — Chunking Lambda
- [ ] Parse incoming file (markdown/PDF text extraction)
- [ ] Chunk by heading/section (not fixed character count)
- [ ] Attach metadata: `{source_key, domain, doc_title, upload_date}`
- [ ] Write chunked JSON to `asklore-processed`

### Step 1.5 — Embedding Pipeline
- [ ] Lambda reads processed chunks
- [ ] Call Bedrock Titan Embeddings per chunk
- [ ] Index embedding + metadata into OpenSearch Serverless

### Step 1.6 — Basic Retrieval + Generation
- [ ] Lambda: accept query → embed query → OpenSearch kNN search (top-5)
- [ ] Pass retrieved chunks + query to Bedrock Claude with a grounded prompt
- [ ] Return answer + source `doc_title`/`source_key`

### Step 1.7 — Minimal API
- [ ] API Gateway REST endpoint → Retrieval Lambda
- [ ] Test via Postman/curl with 5–10 sample questions

**✅ Phase 1 Done When:** You can upload a markdown file and query it end-to-end with a cited answer.

---

## Phase 2 — Multi-Domain Ingestion
**Timeline: Weeks 3–4**
**Goal:** Four domains flowing into one searchable index with consistent metadata.

### Step 2.1 — Additional Domain Seed Content
- [ ] Write seed docs for `incident-postmortems/` (5–10 postmortem docs)
- [ ] Write seed docs for `product-docs/` (10–15 docs)
- [ ] Write seed docs for `onboarding-wiki/` (10–15 docs)
- [ ] Upload each to its respective `s3://asklore-raw/<domain>/` prefix

### Step 2.2 — Unified Metadata Schema
- [ ] Extend chunking Lambda to tag: `{source_key, domain, doc_type, author, created_date, last_updated}`
- [ ] Ensure domain is auto-derived from S3 prefix path

### Step 2.3 — Change Detection / Dedup
- [ ] On S3 event, compute content hash (MD5/SHA) of new object
- [ ] Compare against stored hash (DynamoDB table `DocumentHashes`)
- [ ] Skip re-chunking/re-embedding if unchanged
- [ ] Re-embed only changed/new documents

### Step 2.4 — Seed Conflicting/Recency Test Cases
- [ ] Deliberately create 2–3 pairs of documents on the same topic with different `last_updated` dates (one stale, one fresh) — to be used in Phase 3 recency testing

**✅ Phase 2 Done When:** All 4 domains are ingested, deduped, and queryable with consistent metadata.

---

## Phase 3 — Two-Tier Routing & Hybrid Search
**Timeline: Weeks 5–6**
**Goal:** Accurate, domain-aware, recency-aware retrieval.

### Step 3.1 — Tier 1: Domain Classification Router
- [ ] Build Bedrock AgentCore agent (or simple Lambda + Claude prompt) to classify incoming query into one or more domains
- [ ] Route query to OpenSearch with a metadata filter on `domain`

### Step 3.2 — Tier 2: Hybrid Retrieval
- [ ] Configure OpenSearch hybrid search (k-NN + BM25) on the index
- [ ] Tune weighting between semantic and keyword scores per domain (e.g., runbooks favor keyword match on exact commands)

### Step 3.3 — Reranking
- [ ] Retrieve top-20 candidates from hybrid search
- [ ] Call Bedrock Rerank API to reorder
- [ ] Pass top-5 reranked chunks to generation step

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
- [ ] Enable X-Ray across all Lambdas in the request chain (router → retrieval → rerank → generation)

### Step 6.2 — Dashboards
- [ ] Build CloudWatch dashboard: latency per stage, error rates, OpenSearch OCU usage, Bedrock token consumption

### Step 6.3 — Caching
- [ ] Add semantic cache layer (DynamoDB or ElastiCache) keyed on normalized query embeddings
- [ ] Skip retrieval+generation for near-duplicate recent queries

### Step 6.4 — Rate Limiting
- [ ] Configure API Gateway usage plans and throttling per API key/user

### Step 6.5 — Simulated Access Control
- [ ] Tag a subset of documents (e.g., under `onboarding-wiki/hr-sensitive/`) as restricted
- [ ] Pass a mock user role in the API request; enforce filtering at the OpenSearch query layer (not just UI)

### Step 6.6 — Cost Monitoring
- [ ] Set up AWS Budgets with alerts on Bedrock + OpenSearch Serverless spend
- [ ] Tag resources per domain for cost attribution

**✅ Phase 6 Done When:** The system runs continuously with visibility into cost, latency, and failures.

---

## AWS Service Reference Map

| Function | Service |
|---|---|
| Document storage/trigger | S3 + S3 Event Notifications |
| Orchestration/agents | Bedrock AgentCore |
| Vector + hybrid search | OpenSearch Serverless |
| Embeddings & generation | Bedrock (Titan Embeddings, Claude) |
| Chunking/processing compute | Lambda (light) / Fargate (large PDFs) |
| Change detection | S3 versioning + hash comparison (Lambda + DynamoDB) |
| Metadata/cache/trace store | DynamoDB |
| IaC | CloudFormation or CDK |
| CI/CD + eval gating | CodePipeline + CodeBuild |
| Observability | CloudWatch, X-Ray |
| Guardrails | Bedrock Guardrails |
| API layer | API Gateway + Lambda |

---

## S3 Bucket Layout Reference

```
s3://asklore-raw/
├── infra-runbooks/
├── incident-postmortems/
├── product-docs/
└── onboarding-wiki/
    └── hr-sensitive/       # used for Phase 6 access-control testing

s3://asklore-processed/
└── <domain>/<doc_id>/chunks.json

s3://asklore-eval/
└── golden-qa-dataset.json
```

---

## Suggested Next Action

Start with **Phase 1, Step 1.1** — scaffold the CloudFormation/CDK stack and get the S3 buckets + OpenSearch Serverless collection provisioned before writing any Lambda code.
