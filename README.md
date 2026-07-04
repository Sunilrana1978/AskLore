# AskLore

Internal tribal-knowledge RAG assistant built on AWS. Drop a markdown or PDF document into S3 and get grounded, cited answers back via a REST API — no hallucination, every answer traced to a source.

---

## How it works

```
S3 upload (asklore-raw)
  └─► ChunkingLambda       — parse markdown/PDF, split by heading, write chunks.json
        └─► EmbeddingLambda — call Bedrock Titan Embeddings v2, index into OpenSearch Serverless
              └─► RetrievalLambda ◄─ POST /query (API Gateway)
                    ├─ embed query → kNN search (top-5 chunks)
                    └─ Bedrock Claude → grounded answer + source citations
```

## AWS Services

| Role | Service |
|---|---|
| Document storage + event trigger | S3 + S3 Event Notifications |
| Embeddings & generation | Bedrock (Titan Embeddings v2, Claude 3.5 Sonnet) |
| Vector search | OpenSearch Serverless (VECTORSEARCH collection) |
| Compute | Lambda (Python 3.12) |
| API | API Gateway REST (POST `/query`) |
| IaC | CloudFormation (`template.yaml`) |

## Repository layout

```
template.yaml              # CloudFormation — all Phase 1 resources
lambda/
  chunking/handler.py      # S3 event → parse → chunks.json
  embedding/handler.py     # chunks.json → Bedrock embeddings → OpenSearch index
  retrieval/handler.py     # query → kNN search → Bedrock Claude → answer
seed-data/
  infra-runbooks/          # drop sample markdown docs here for local testing
```

## Deploy

**Prerequisites:** AWS CLI configured, Bedrock model access enabled for Titan Embeddings v2 and Claude 3.5 Sonnet.

```bash
# 1. Create an S3 bucket for CloudFormation artifacts (one-time)
aws s3 mb s3://asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)

# 2. Package — ZIPs Lambda code and uploads to S3
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket asklore-cfn-artifacts-<your-account> \
  --output-template-file template-packaged.yaml

# 3. Deploy
aws cloudformation deploy \
  --template-file template-packaged.yaml \
  --stack-name asklore-stack \
  --capabilities CAPABILITY_NAMED_IAM

# 4. View outputs (bucket names, AOSS endpoint, API URL)
aws cloudformation describe-stacks \
  --stack-name asklore-stack \
  --query "Stacks[0].Outputs" --output table
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
  "answer": "...",
  "sources": [
    { "doc_title": "ssl-cert-rotation", "source_key": "infra-runbooks/ssl-cert-rotation.md" }
  ]
}
```

## Ingest a document

Upload any markdown file to the raw bucket — the pipeline triggers automatically:

```bash
aws s3 cp seed-data/infra-runbooks/my-runbook.md \
  s3://asklore-raw-<account>-<region>/infra-runbooks/my-runbook.md
```

The S3 prefix (`infra-runbooks/`) becomes the **domain** tag on every chunk.

## Implementation phases

| Phase | Goal | Status |
|---|---|---|
| 1 | Single-domain MVP — upload → query with citations | 🚧 In progress |
| 2 | Multi-domain ingestion + hash-based dedup (DynamoDB) | Planned |
| 3 | Domain router + hybrid search (kNN + BM25) + Bedrock Rerank + recency weighting | Planned |
| 4 | Bedrock Guardrails, grounded prompts, groundedness scoring | Planned |
| 5 | RAGAS evaluation suite + CI regression gate | Planned |
| 6 | X-Ray tracing, CloudWatch dashboards, semantic cache, RBAC | Planned |
