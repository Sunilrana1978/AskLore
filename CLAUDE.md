# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AskLore is a greenfield internal tribal-knowledge RAG assistant built entirely on AWS cloud-native services. It ingests documents (Slack/Notion/GitHub exports simulated as markdown/PDF files dropped into S3) and serves grounded, cited answers via an API.

## Architecture

The pipeline flows: S3 upload → S3 event notification → ChunkingLambda → EmbeddingLambda → OpenSearch Serverless (vector index) → RetrievalLambda (kNN + BM25 hybrid search + Bedrock Rerank) → GenerationLambda (Bedrock Claude with Guardrails) → API Gateway.

**Key AWS services and their roles:**

| Role | Service |
|---|---|
| Document storage + trigger | S3 (`asklore-raw`) + S3 Event Notifications |
| Chunked output storage | S3 (`asklore-processed`) |
| Embeddings & generation | Bedrock (Titan Embeddings v2, Claude) |
| Vector + hybrid search | OpenSearch Serverless (vector type collection) |
| Orchestration | Bedrock AgentCore (Phase 3+) |
| Change detection / dedup | Lambda + DynamoDB (`DocumentHashes` table) |
| Conversation state / cache / traces | DynamoDB |
| IaC | CloudFormation (`template.yaml` at repo root) |
| CI/CD + eval gating | CodePipeline + CodeBuild |
| Observability | CloudWatch + X-Ray |
| Guardrails | Bedrock Guardrails (PII, off-topic, prompt injection) |
| API layer | API Gateway + Lambda |

## Deploy Commands

```bash
# One-time: create an S3 bucket for CloudFormation artifacts
aws s3 mb s3://asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)

# Package (ZIP Lambda code dirs, upload to S3, rewrite template)
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket asklore-cfn-artifacts-<your-account> \
  --output-template-file template-packaged.yaml

# Deploy (first time or update)
aws cloudformation deploy \
  --template-file template-packaged.yaml \
  --stack-name asklore-stack \
  --capabilities CAPABILITY_NAMED_IAM

# View outputs (bucket names, AOSS endpoint, API URL)
aws cloudformation describe-stacks \
  --stack-name asklore-stack \
  --query "Stacks[0].Outputs" --output table
```

Lambda functions that need non-runtime dependencies (embedding, retrieval) require a `requirements.txt` in their directory and Docker-based bundling before the package step — see the TODO comments in those handlers.

## S3 Layout

```
s3://asklore-raw/
├── infra-runbooks/
├── incident-postmortems/
├── product-docs/
└── onboarding-wiki/
    └── hr-sensitive/       # access-control testing (Phase 6)

s3://asklore-processed/
└── <domain>/<doc_id>/chunks.json

s3://asklore-eval/
└── golden-qa-dataset.json
```

Domain is auto-derived from the S3 prefix (first path component after the bucket).

## Implementation Phases

The detailed plan lives in `AskLore_Implementation_Plan.md`. High-level milestones:

- **Phase 1** — Foundation MVP: single domain (infra-runbooks), end-to-end query with citations
- **Phase 2** — Multi-domain ingestion with dedup (hash-based, DynamoDB)
- **Phase 3** — Domain-classification router + hybrid search (kNN + BM25) + Bedrock Rerank + recency weighting + multi-turn query rewriting
- **Phase 4** — Bedrock Guardrails, grounded prompts, source attribution, groundedness scoring (Claude-as-judge)
- **Phase 5** — RAGAS evaluation suite, golden dataset (30–50 Q&A pairs), CI regression gate
- **Phase 6** — X-Ray tracing, CloudWatch dashboards, semantic query cache, rate limiting, simulated RBAC, cost budgets

## Key Design Decisions

- **Chunking strategy:** split by heading/section, not fixed character count; attach metadata `{source_key, domain, doc_type, author, created_date, last_updated}`
- **Dedup:** compute content hash on S3 event; skip re-embedding if hash matches stored value in `DocumentHashes`
- **Retrieval:** top-20 candidates from hybrid search → Bedrock Rerank → top-5 to generation
- **Recency:** score-boost using `last_updated` decay so newer documents outrank stale ones on the same topic
- **Grounding:** system prompt constrains model to retrieved context only; inline citations required; groundedness scored post-generation by a Claude judge
- **Access control:** enforced at the OpenSearch query layer via metadata filters on user role (not at the UI layer)
