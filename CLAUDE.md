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

---

## Development Rules

These rules are enforced for all code changes in this repo. Claude Code must follow them without exception.

### Project Structure

```
asklore/
├── docs/                        # Planning docs, ADRs, architecture decisions
├── lambda/
│   └── <function>/
│       ├── handler.py           # Lambda entry point only
│       └── requirements.txt     # Deps scoped to this function; no shared reqs file
├── scripts/                     # Operational one-off scripts (build, seed, index)
├── seed-data/                   # Demo documents for POC; never real customer data
├── tests/
│   └── unit/
│       └── <function>/          # Unit tests mirror lambda/ layout
├── build/                       # Generated — never commit
├── template.yaml                # IaC — single source of truth
├── template-packaged.yaml       # Generated — never commit
├── Makefile                     # Wraps all common commands
└── pyproject.toml               # Python project config + ruff lint settings
```

**Where things go:**
- Business logic shared between Lambdas → does not exist yet; design a shared Lambda Layer if needed in Phase 3+
- New AWS resources → `template.yaml` only; never create resources manually in console
- Planning notes, design decisions → `docs/`; not at repo root
- Operational scripts that run locally → `scripts/`; must read config from env vars or stack outputs, never hardcode

### Git Rules

**Never commit:**
- `build/` — Lambda packages generated by the build script
- `template-packaged.yaml` — contains S3 presigned URLs tied to a specific deploy run
- `.claude/settings.local.json` — may contain personal API URLs or paths
- `.env` / `.env.*` — credentials or environment-specific config
- Any file containing an AWS account ID, resource ARN, or endpoint URL as a literal string

**In code and scripts:** use `${AWS::AccountId}` / `${AWS::Region}` in CloudFormation; read runtime values from `os.environ` or `boto3` in Python; read resource names from CloudFormation stack outputs in scripts.

### IaC Rules

- **IaC lives in `template.yaml` at repo root. Never CDK, SAM, or Terraform.**
- Every Lambda function must have a matching `AWS::Logs::LogGroup` resource in the template with `RetentionInDays: 30`.
- Stack name convention: `asklore-<env>` (e.g., `asklore-dev`, `asklore-prod`). Pass via `STACK_NAME` env var, never hardcode.
- Never create or modify AWS resources manually in the console — always go through CloudFormation.
- `DependsOn` order: Lambda Permissions → S3 Buckets (so S3 can verify invocation rights at notification registration time).

### Deployment Rules

Always use the Makefile or build script. In order:

```bash
make validate       # 1. Validate template syntax first
make build-deploy   # 2. Build packages + deploy stack
```

- **Always run `make validate` before `make deploy`** — catches YAML/resource errors before touching AWS.
- `--no-fail-on-empty-changeset` is always passed to `cloudformation deploy` — a deploy with no changes is a success, not an error.
- Never run `--deploy` alone after manually editing a Lambda handler — build/ will be stale. Always do `make build-deploy`.
- To target a non-default stack: `STACK_NAME=asklore-dev make build-deploy`.

### Python Code Rules

- **Python 3.12.** Type hints required on all function signatures.
- **`ruff check lambda/ scripts/`** must pass before committing. Config is in `pyproject.toml`.
- **No frozen credentials in module-level state.** `get_frozen_credentials()` snapshots credentials that expire after ~1 hour on warm Lambda containers. Rebuild auth objects per-invocation or use the live session credentials.
- **No hardcoded strings** for account IDs, regions, endpoints, bucket names, or model IDs. Every external reference comes from `os.environ`.
- **No comments explaining what the code does** — use descriptive names. Only add a comment when the *why* is non-obvious (a workaround, a hidden constraint, a subtle invariant).
- **Handler signature:** every Lambda entry point is `def handler(event: dict, context) -> dict`.
- **Per-record error isolation:** when iterating `event["Records"]`, wrap each record in `try/except` so one bad record doesn't fail the whole batch.

### Testing Rules

- Unit tests live in `tests/unit/<function>/test_<module>.py`.
- Tests must not make real AWS calls — mock `boto3` clients at the boundary.
- Run with: `make test`
- When adding a new Lambda function, add at least one unit test for the core logic (chunking algorithm, response parsing, etc.) before considering the step complete.
