# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AskLore is a greenfield internal tribal-knowledge RAG assistant built entirely on AWS cloud-native services. It ingests markdown/PDF documents dropped into S3 and serves grounded, cited answers via a REST API.

## Architecture

**Ingestion pipeline:** S3 upload (`asklore-raw-uploads`) → S3 Event → `DedupLambda` (SHA-256 hash, conditional `asklore-file-hashes` DynamoDB write) → new content copied into `asklore-raw` / duplicate deleted from the landing bucket → S3 Event → `IngestionTriggerLambda` → Bedrock `StartIngestionJob` → Knowledge Base data source sync (KB-managed `FIXED_SIZE` chunking + Cohere Embed v3 embedding) → OpenSearch Serverless (`asklore-kb-index`)

**Query pipeline:** `POST /query` → API Gateway → `RetrievalLambda` → Bedrock `Retrieve` (KB vector search only, top-5 chunks) → Gemini `generate_content` (`gemini-2.5-flash`, grounded generation from those chunks) → `{answer, sources}`. `sources` are the retrieved chunks themselves, not filtered by which ones Gemini's answer cited — Gemini doesn't return Bedrock-style citations, so this trades some grounding precision for a much simpler integration (see Key Design Decisions).

**Key AWS services:**

| Role | Service |
|---|---|
| Document landing + dedup | S3 (`asklore-raw-uploads`) + S3 Event Notifications + DynamoDB (`asklore-file-hashes`) |
| Document storage + trigger | S3 (`asklore-raw`) + S3 Event Notifications |
| Ingestion orchestration | Bedrock Knowledge Base + Data Source (`asklore-kb`) |
| Embeddings | Bedrock Cohere Embed English v3 (`cohere.embed-english-v3`, 1024-dim) |
| Retrieval | Bedrock `Retrieve` — vector search only, against the Knowledge Base |
| Generation | Gemini AI Studio (`gemini-2.5-flash`) via the `google-genai` SDK; API key in Secrets Manager |
| Vector search | OpenSearch Serverless — VECTORSEARCH collection `asklore`, index `asklore-kb-index` |
| Secrets | AWS Secrets Manager (`<stack>/gemini-api-key`) — created empty by CloudFormation, populated post-deploy via CLI |
| IaC | CloudFormation (`template.yaml` at repo root) — never CDK/SAM/Terraform |
| Observability | CloudWatch + X-Ray (Phase 6) |
| Guardrails | Bedrock Guardrails (Phase 4) |

Chunking, embedding, and index management are owned by the Bedrock Knowledge Base — there is no custom chunking Lambda or explicit `domain` metadata tagging in Phase 1. Domain-based filtering (if needed) would be reintroduced later via `.metadata.json` S3 sidecar files, not derived implicitly from the prefix.

## Build & Deploy

**Prerequisites:**
- [`uv`](https://github.com/astral-sh/uv) installed
- AWS CLI configured
- Bedrock model access enabled for **Cohere Embed English v3** (Bedrock console → Model access → Modify model access) — used by the Knowledge Base for embeddings only; generation no longer calls Bedrock.
- A Gemini API key from Google AI Studio, populated post-deploy into the stack's `GeminiApiKeySecret` (Secrets Manager) — see Deployment Rules below. Never put the raw key in `template.yaml`, `config/<env>.json`, or git.

```bash
# One-time: create S3 bucket for CloudFormation artifacts
aws s3 mb s3://asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)

# Full build + deploy (rebuilds build/ from lambda/*/; installs deps via uv)
# AOSS_ADMIN_PRINCIPAL_ARN defaults to current caller — override for CI/CD roles:
#   AOSS_ADMIN_PRINCIPAL_ARN=arn:aws:iam::123456789:role/MyRole bash scripts/build-and-deploy.sh
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

### Known Deploy Gotchas

Surfaced on the first real deploy of this stack. The fixes already live in `template.yaml` and `lambda/kb-index-setup/handler.py` — documented here so a future session (or a deploy to a fresh account/region) doesn't re-debug them from scratch:

- **AOSS data access policy propagation delay:** `AossAccessPolicy` reports `CREATE_COMPLETE` in CloudFormation as soon as the policy document is accepted, but OpenSearch Serverless's data plane takes a few seconds to actually start honoring it. `kb-index-setup` retries `AuthorizationException`s for up to ~60s (`AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS` / `_BACKOFF_SECONDS`) rather than failing immediately.
- **Index visibility settle delay:** even after the index create call succeeds, `AskLoreKnowledgeBase` (`DependsOn: AossKbIndex`) can still fail with "no such index" if it starts right away — AOSS needs a moment before the index is visible on every read path, including Bedrock's own validation. `kb-index-setup` sleeps `INDEX_SETTLE_SECONDS` (30s) after a fresh create before reporting `SUCCESS` to CloudFormation.
- **OpenSearch Serverless only supports the `faiss` k-NN engine, not `nmslib`.** Bedrock rejects the index at `AskLoreKnowledgeBase` creation with "engine type is invalid" if the wrong one is used.
- **A Lambda invoked synchronously by a CloudFormation custom resource can race its own explicit `AWS::Logs::LogGroup`.** `AossKbIndex` invokes `AossKbIndexFunction` during stack create; if that happens before CloudFormation creates `AossKbIndexFunctionLogGroup`, Lambda auto-creates an untracked log group on first execution, and the explicit `AWS::Logs::LogGroup` resource then fails with `AlreadyExists`. Fixed by adding `AossKbIndexFunctionLogGroup` to `AossKbIndex`'s `DependsOn`. Any new Lambda invoked synchronously during stack create/update (as opposed to triggered later by S3/API Gateway, which is safe) needs the same treatment.
- **Rollback can leave that same log group orphaned.** On rollback, the custom resource's Lambda gets invoked once more for its `Delete` lifecycle event, and Lambda's asynchronous log delivery can recreate `/aws/lambda/<stack-name>-kb-index-setup` right after CloudFormation deletes it. Before retrying a failed deploy: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/<stack-name>-kb-index-setup` (e.g. `asklore-dev-kb-index-setup` for `STACK_NAME=asklore-dev`) and delete it if present, or the retry hits the same `AlreadyExists` conflict.

## Lambda Functions

| Function | Trigger | Key logic |
|---|---|---|
| `kb-index-setup` | CloudFormation custom resource | Creates the AOSS vector index (`vector`/`text`/`metadata` fields) the Knowledge Base reads from — KB does not create it itself |
| `dedup` | S3 ObjectCreated on `asklore-raw-uploads` | SHA-256-hashes content, conditionally writes to DynamoDB (`asklore-file-hashes`); new hash copies into `asklore-raw` under the same key, duplicate hash deletes from the landing bucket; `.metadata.json` sidecars pass through unhashed |
| `ingestion-trigger` | S3 ObjectCreated on `asklore-raw` | Calls Bedrock `StartIngestionJob`; swallows `ConflictException` since only one ingestion job can run per data source at a time |
| `retrieval` | API Gateway `POST /query` | Calls Bedrock `Retrieve` against the Knowledge Base for top-5 chunks, then Gemini `generate_content` for the answer; returns all retrieved chunks as `sources` |

## S3 Layout

```
asklore-raw-uploads-<account>-<region>/
└── <domain>/       # transient landing zone, deduped by DedupLambda

asklore-raw-<account>-<region>/
├── infra-runbooks/
├── incident-postmortems/
├── product-docs/
└── onboarding-wiki/
    └── hr-sensitive/       # Phase 6 access-control testing

asklore-eval-<account>-<region>/
└── golden-qa-dataset.json  # Phase 5
```

## Key Design Decisions

- **IaC:** Raw CloudFormation YAML only (`template.yaml`). Never CDK, SAM, or Terraform.
- **Chunking:** Owned by the Bedrock Knowledge Base's `FIXED_SIZE` chunking strategy, not a custom heading-based Lambda. This trades the earlier heading-boundary design for far less code to maintain.
- **Embedding model:** Cohere Embed English v3 (1024-dim), invoked internally by the Knowledge Base during data-source sync — no Lambda calls `InvokeModel` for embeddings directly anymore.
- **Generation model:** Gemini AI Studio (`gemini-2.5-flash`, configurable via the `GeminiModelId` CloudFormation parameter / `config/<env>.json`), invoked via the `google-genai` SDK's `generate_content`, given the Bedrock-retrieved chunks as prompt context — not Bedrock `RetrieveAndGenerate`/`InvokeModel`. Switched off Bedrock generation because retrieval and generation are decoupled in RAG architecture (the vector store doesn't care which LLM consumes retrieved context), so this didn't require re-embedding/re-indexing anything. Trade-off: Gemini doesn't return Bedrock-style `citations[]`, so `sources` in the response are simply all retrieved chunks, not filtered to only those the answer actually cited. The Gemini API key lives in Secrets Manager (`GeminiApiKeySecret`), created empty by CloudFormation and populated post-deploy via `aws secretsmanager put-secret-value` — never through a CFN parameter, so it never touches parameter history, `config/<env>.json`, or git. The `google-genai` SDK disables HTTP retries by default; `_get_gemini_client()` in `lambda/retrieval/handler.py` explicitly configures `HttpRetryOptions` to retry only on 429s, sized to fit the Lambda's 28s timeout / API Gateway's 29s hard ceiling.
- **AOSS vector index:** Created by a CloudFormation custom resource (`kb-index-setup` Lambda) before the Knowledge Base is created — CloudFormation has no native resource type for AOSS index creation, and Bedrock Knowledge Base does not create the index for you. Uses the `faiss` k-NN engine (OpenSearch Serverless doesn't support `nmslib`) and retries/settles through AOSS's propagation delay before reporting success — see Known Deploy Gotchas.
- **Dedup:** Explicit content-addressed dedup — `DedupLambda` SHA-256-hashes every object landing in `asklore-raw-uploads`, conditionally writes `{file_hash, filename, domain, upload_date, s3_path}` to DynamoDB (`asklore-file-hashes`) via a conditional `PutItem`, and only copies genuinely new content into `asklore-raw` (duplicates are deleted from the landing bucket, never reaching the Knowledge Base data source). Knowledge Base data-source sync's own object-level change tracking still runs on top of this as a secondary backstop, not the primary dedup mechanism.
- **Retrieval:** Bedrock `Retrieve`'s built-in vector search (KB vector search only, no generation) covers what Phase 3 originally planned as custom hybrid search + Bedrock Rerank; that phase item is superseded, not custom-built.
- **Access control (Phase 6):** Still planned — would use Knowledge Base metadata filtering (via `.metadata.json` S3 sidecar files) rather than the OpenSearch-layer filter originally envisioned.

## Implementation Phases

Detailed progress tracked in `AskLore_Implementation_Plan.md`.

- **Phase 1** — Foundation MVP: single domain, end-to-end query via Bedrock Knowledge Base + `Retrieve` + Gemini generation (see `docs/asklore-gemini-migration-plan.md` for the switch off Bedrock `RetrieveAndGenerate`)
- **Phase 2** — Multi-domain ingestion; explicit SHA-256 dedup via `DedupLambda` + DynamoDB ahead of Knowledge Base ingestion
- **Phase 3** — Domain-classification router + multi-turn query rewriting (hybrid search + rerank are now covered natively by Bedrock `Retrieve`)
- **Phase 4** — Bedrock Guardrails, grounded prompts, groundedness scoring (Claude-as-judge)
- **Phase 5** — RAGAS evaluation suite, golden dataset, CI regression gate
- **Phase 6** — X-Ray tracing, CloudWatch dashboards, semantic cache, rate limiting, simulated RBAC (via KB metadata filtering), cost budgets

---

## Development Rules

These rules are enforced for all code changes in this repo. Claude Code must follow them without exception.

### Project Structure

```
./
├── docs/                        # Planning docs, ADRs, architecture decisions (create when needed)
├── config/                      # Per-env CloudFormation parameter overrides (dev.json/test.json/prod.json)
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
- Every Lambda function must have a matching `AWS::Logs::LogGroup` resource in the template with `RetentionInDays: !Ref LogRetentionDays` (defaults to 30, overridable per environment — see below).
- Stack name convention: `asklore-<env>` (e.g., `asklore-dev`, `asklore-prod`). Pass via `STACK_NAME` env var, never hardcode.
- Environment-specific CloudFormation parameter values (Bedrock model IDs, log retention, API throttling) live in `config/<env>.json`, one file per `STACK_NAME` suffix (`asklore-dev` → `config/dev.json`). `scripts/build-and-deploy.sh` derives `<env>` from `STACK_NAME` and passes the matching file via `--parameter-overrides file://config/<env>.json` automatically; falls back to the `Parameters` block's `Default:` values if no matching file exists. `AossAdminPrincipalArn` never goes in these files — it's computed dynamically from the caller identity, not a static per-env constant.
- Every physical resource name in `template.yaml` (IAM roles, Lambda function names, the DynamoDB table, AOSS collection/policy/index names, the Bedrock Knowledge Base/Data Source, S3 buckets, the API Gateway) is derived from `!Sub "${AWS::StackName}-..."` rather than a literal `asklore-` prefix — this is what makes multiple environments (`asklore-dev`/`asklore-test`/`asklore-prod`) deployable side by side in the same account/region without name collisions. Never reintroduce a literal `asklore-` physical name; always derive it from `AWS::StackName`.
- Never create or modify AWS resources manually in the console — always go through CloudFormation.
- `DependsOn` order: Lambda Permissions → S3 Buckets (so S3 can verify invocation rights at notification registration time).
- A Lambda invoked synchronously by a CloudFormation custom resource must `DependsOn` its own `AWS::Logs::LogGroup` — see Known Deploy Gotchas above.

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
- After deploying (or redeploying) a stack, the Gemini API key must be (re-)populated in Secrets Manager — CloudFormation only creates `GeminiApiKeySecret` empty: `aws secretsmanager put-secret-value --secret-id <GeminiApiKeySecretArn from stack outputs> --secret-string '<key>'`. A stack replacement (not update) of the secret resource would wipe this value; check stack outputs after any deploy that touches `GeminiApiKeySecret`.

### Python Code Rules

- **Python 3.12.** Type hints required on all function signatures.
- **`ruff check lambda/ scripts/`** must pass before committing. Config is in `pyproject.toml`. Auto-fix formatting with `uv run ruff format lambda/ scripts/`.
- **No frozen credentials in module-level state.** `get_frozen_credentials()` at module load time snapshots credentials that expire after ~1 hour on warm Lambda containers. Calling it inside the handler function (per-invocation) is fine. `boto3.client()` at module level is also fine — the client refreshes credentials internally.
- **No hardcoded strings** for account IDs, regions, endpoints, bucket names, or model IDs. Every external reference comes from `os.environ`.
- **No comments explaining what the code does** — use descriptive names. Only add a comment when the *why* is non-obvious (a workaround, a hidden constraint, a subtle invariant).
- **Handler signature:** every Lambda entry point is `def handler(event: dict, context) -> dict`.
- **Per-record error isolation:** when iterating `event["Records"]`, wrap each record in `try/except` so one bad record doesn't fail the whole batch.

### Testing Rules

- Local dev env setup (clone → `uv sync` → editor interpreter → verify): see README's [Local development setup](README.md#local-development-setup). `uv sync` installs the `[dependency-groups] dev` list in `pyproject.toml` — keep that list in sync with every `lambda/*/requirements.txt` plus `pytest`/`ruff` whenever a Lambda's dependencies change, or `uv run pytest`/`uv run ruff` will drift from what's actually deployed.
- Unit tests live in `tests/unit/<function>/test_<module>.py`.
- Tests must not make real AWS calls — mock `boto3` clients at the boundary.
- Run all tests: `make test` | Run a single file: `uv run pytest tests/unit/<function>/test_handler.py -v` | Run one test: `uv run pytest -k <test_name> -v`
- **`lambda` is a Python reserved keyword** — handlers cannot be imported with a normal `import`. Use `load_handler("<function-name>")` from `tests/conftest.py`, which loads the handler via `importlib` under a unique module name. See any existing test for the pattern.
- When adding a new Lambda function, add at least one unit test for the core logic before considering the step complete.
