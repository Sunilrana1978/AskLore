# AskLore

Internal tribal-knowledge RAG assistant built on AWS. Drop a markdown or PDF document into S3 and get grounded, cited answers back via a REST API — no hallucination, every answer traced to a source.

---

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'clusterBkg': '#F1F5F9', 'clusterBorder': '#CBD5E1', 'lineColor': '#64748B', 'fontSize': '14px'}}}%%
flowchart TB

    classDef s3      fill:#FEF9C3,stroke:#CA8A04,color:#1C1917,font-weight:bold
    classDef lambda  fill:#FEE2E2,stroke:#B91C1C,color:#1C1917,font-weight:bold
    classDef bedrock fill:#D1FAE5,stroke:#065F46,color:#1C1917,font-weight:bold
    classDef os      fill:#EDE9FE,stroke:#5B21B6,color:#1C1917,font-weight:bold
    classDef person  fill:#E0F2FE,stroke:#0369A1,color:#1C1917
    classDef output  fill:#F0FDF4,stroke:#15803D,color:#1C1917,font-weight:bold

    subgraph ING["📥  Ingestion Pipeline"]
        direction LR
        UADM(["👤 Admin / CI"]):::person
        RAWB["S3  asklore-raw"]:::s3
        CLMB["ChunkingLambda"]:::lambda
        PROC["S3  asklore-processed"]:::s3
        EMBL["EmbeddingLambda + Cohere Embed v3"]:::bedrock
        UADM -->|.md / .pdf| RAWB -->|S3 Event| CLMB -->|chunks.json| PROC -->|S3 Event| EMBL
    end

    subgraph QRY["🔍  Query Pipeline"]
        direction TB
        subgraph RET["🔎  Retrieval"]
            direction LR
            UQRY(["👤 User"]):::person
            APIG["API Gateway"]:::lambda
            RLMB["RetrievalLambda"]:::lambda
            CEMB["Cohere Embed v3  (search_query)"]:::bedrock
            UQRY -->|POST /query| APIG --> RLMB --> CEMB
        end
        OSLS[("OpenSearch Serverless — asklore-knowledge")]:::os
        subgraph AUG["📝  Augmentation"]
            direction LR
            PAUG["Prompt Augmentation  (top-5 chunks + preamble)"]:::lambda
        end
        subgraph GEN["💬  Generation"]
            direction LR
            GLLM["Cohere Command R+"]:::bedrock
            GOUT(["Grounded Answer + citations[ ]"]):::output
            GLLM --> GOUT
        end
        CEMB ==>|kNN top-5| OSLS
        OSLS ==>|top-5 chunks| PAUG
        PAUG ==> GLLM
    end

    EMBL ==>|bulk index| OSLS
```

**Ingestion flow:** A `.md` or `.pdf` file dropped into S3 triggers `ChunkingLambda`, which splits by heading boundary (min 80 / max 4 000 chars), attaches domain metadata, and writes `chunks.json` to `asklore-processed`. That event triggers `EmbeddingLambda`, which calls Cohere Embed v3 (`search_document`, 1024-dim) and bulk-indexes the vectors into OpenSearch Serverless.

**Query flow:** `POST /query` → `RetrievalLambda` embeds the question with Cohere Embed v3 (`search_query`), runs a kNN top-5 search, injects the retrieved chunks into a grounded prompt, calls Cohere Command R+, and returns only the chunks actually referenced in `citations[]` as `sources`.

---

## AWS Services

| Role | Service |
|---|---|
| Document storage + event trigger | S3 + S3 Event Notifications |
| Embeddings | Bedrock Cohere Embed English v3 (`cohere.embed-english-v3`, 1024-dim) |
| Generation | Bedrock Cohere Command R+ (`cohere.command-r-plus-v1:0`) |
| Vector search | OpenSearch Serverless — VECTORSEARCH collection `asklore` |
| Compute | Lambda (Python 3.12) |
| API | API Gateway REST — `POST /query` |
| IaC | CloudFormation (`template.yaml`) |

## Repository layout

```
template.yaml                   # CloudFormation — all Phase 1 resources
lambda/
  chunking/
    handler.py                  # S3 event → parse markdown/PDF → chunks.json
    requirements.txt
  embedding/
    handler.py                  # chunks.json → Cohere Embed v3 → OpenSearch bulk index
    requirements.txt
  retrieval/
    handler.py                  # query → kNN search → Command R+ → cited answer
    requirements.txt
scripts/
  build-and-deploy.sh           # build Lambda packages with uv, package, deploy
  index-all.py                  # one-shot local indexer (bypasses Lambda concurrency)
seed-data/
  infra-runbooks/               # 18 sample markdown runbooks (domain 1)
```

Lambda source dirs contain only `handler.py` + `requirements.txt`. Installed packages are generated into `build/` by `scripts/build-and-deploy.sh` and gitignored.

## Local setup

Requires [uv](https://github.com/astral-sh/uv) and AWS CLI v2.

```bash
# Create project venv (Python 3.12, matches Lambda runtime)
uv venv .venv --python 3.12
source .venv/bin/activate

# Install local tooling (boto3, opensearch-py for scripts/)
uv pip install boto3 opensearch-py requests-aws4auth requests
```

## Deploy

**Prerequisites:** Bedrock model access enabled for **Cohere Embed English v3** and **Cohere Command R+** (Bedrock console → Model access → Modify model access).

```bash
# One-time: create an S3 bucket for CloudFormation artifacts
aws s3 mb s3://asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)

# Build Lambda packages, package template, and deploy (all in one)
bash scripts/build-and-deploy.sh

# View outputs (bucket names, AOSS endpoint, API URL)
aws cloudformation describe-stacks \
  --stack-name asklore-stack \
  --query "Stacks[0].Outputs" --output table
```

Build flags:
```bash
bash scripts/build-and-deploy.sh --build    # build only, skip deploy
bash scripts/build-and-deploy.sh --deploy   # deploy only (assumes build/ exists)
```

## Ingest documents

Upload any markdown or PDF to the raw bucket — the pipeline triggers automatically:

```bash
aws s3 cp my-runbook.md \
  s3://asklore-raw-<account>-<region>/infra-runbooks/my-runbook.md
```

The S3 prefix (`infra-runbooks/`) becomes the **domain** tag on every chunk.

To seed all 18 sample runbooks at once:

```bash
aws s3 sync seed-data/infra-runbooks/ \
  s3://asklore-raw-<account>-<region>/infra-runbooks/
```

## Initial index population

For the first load (or any bulk re-index), use the local indexer instead of waiting for Lambda events — it processes files sequentially to stay within Bedrock TPS limits:

```bash
source .venv/bin/activate
python scripts/index-all.py
```

## Query the API

```bash
curl -X POST <ApiUrl from stack outputs> \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I rotate an SSL certificate?"}'
```

Response:
```json
{
  "answer": "To rotate an SSL certificate...",
  "sources": [
    { "doc_title": "ssl-cert-rotation", "source_key": "infra-runbooks/ssl-cert-rotation.md" }
  ]
}
```

Command R+ returns `citations[]` that reference the exact documents used — `sources` in the response contains only chunks the model actually cited, not all retrieved candidates.

## Implementation phases

| Phase | Goal | Status |
|---|---|---|
| 1 | Single-domain MVP — upload → query with citations | 🔄 In progress |
| 2 | Multi-domain ingestion + hash-based dedup (DynamoDB) | Planned |
| 3 | Domain router + hybrid search (kNN + BM25) + Bedrock Rerank + recency weighting | Planned |
| 4 | Bedrock Guardrails, grounded prompts, groundedness scoring | Planned |
| 5 | RAGAS evaluation suite + CI regression gate | Planned |
| 6 | X-Ray tracing, CloudWatch dashboards, semantic cache, RBAC | Planned |

See [`AskLore_Implementation_Plan.md`](AskLore_Implementation_Plan.md) for detailed step-by-step progress.
