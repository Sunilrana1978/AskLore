# AskLore — Generation Layer Migration: AWS Bedrock → Gemini AI Studio

> **Status: implemented.** This doc originally described a generic migration plan that didn't match
> this codebase (it assumed a standalone `generation.py`/`llm_client.py` module, a Bedrock long-term
> API key auth failure, and Titan embeddings). It's been rewritten to describe what was actually
> built, so it stays a trustworthy record instead of a stale draft that contradicts the shipped code.

## Context

AskLore's `retrieval` Lambda previously called Bedrock's `RetrieveAndGenerate` — a single API call
that does vector search against the Knowledge Base *and* generation in one step (via Cohere Command
R+). That single call has been split into two:

1. Bedrock `Retrieve` (vector search only, top-5 chunks) — unchanged Knowledge Base, unchanged
   OpenSearch Serverless index, unchanged embeddings (Cohere Embed English v3).
2. Gemini (`gemini-2.5-flash`, via the `google-genai` SDK) generates the answer from those chunks.

This Lambda authenticates to Bedrock via its IAM role (`RetrievalLambdaRole`) — there was never a
Bedrock long-term API key (ABSK format) in this codebase's auth path. The move to Gemini was made
because retrieval and generation are decoupled in RAG architecture: the vector store doesn't care
which LLM consumes the retrieved context, so this required no re-embedding or re-indexing of the
OpenSearch corpus.

**Why Gemini specifically:** model access issues on the AWS free-tier account (Cohere Command R+
requires a separate AWS Marketplace subscription on top of model-access approval) made Bedrock
generation unreliable to depend on. Gemini AI Studio was chosen as the replacement to sidestep that
entirely — it needs only an API key, no AWS Marketplace subscription or model-access request.

## Scope

**Changed:**
- Generation: Bedrock `RetrieveAndGenerate` (Cohere Command R+) → Bedrock `Retrieve` + Gemini `generate_content`
- `lambda/retrieval/handler.py`, `lambda/retrieval/requirements.txt`, `pyproject.toml` dev group
- `template.yaml`: `GeminiModelId` parameter (replaces `GenerationModelId`), `GeminiApiKeySecret`
  (Secrets Manager), `RetrievalLambdaRole` IAM permissions, `RetrievalLambda` env vars, a
  `GeminiApiKeySecretArn` output
- `config/dev.json` / `test.json` / `prod.json`: `GeminiModelId` replaces `GenerationModelId`
- `tests/unit/retrieval/test_handler.py`

**Unchanged:**
- Cohere Embed English v3 embeddings, Knowledge Base ingestion, `FIXED_SIZE` chunking, OpenSearch
  Serverless, multi-domain routing, document indexing

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| SDK | `google-genai` (not the older `google-generativeai`) | Current, actively-maintained unified SDK |
| Model | `gemini-2.5-flash` (all environments, via `GeminiModelId`) | Cost/speed; revisit `gemini-2.5-pro` later if reasoning quality on complex queries is insufficient |
| Sources | All top-5 retrieved chunks, not filtered by which Gemini's answer cited | Gemini has no Bedrock-style `citations[]` to map back to S3 URIs; parsing citation markers out of Gemini's own response text would be fragile. Simpler, at the cost of a slightly weaker "only what was actually used" grounding guarantee than `RetrieveAndGenerate` provided |
| Key storage | Secrets Manager, `GeminiApiKeySecret` created empty by CloudFormation | The real key is set post-deploy via `aws secretsmanager put-secret-value`, never through a CFN parameter, `config/<env>.json`, or git |
| Retry strategy | `google-genai`'s built-in `HttpRetryOptions`, retrying only on HTTP 429, `attempts=3` | The SDK disables retries by default (one attempt, no backoff, unless configured). A 429 and a 400 both raise `genai.errors.ClientError` — there's no distinct rate-limit exception class, so retry is configured by `http_status_codes=[429]`, not by exception type. Sized to fit comfortably under the Lambda's 28s timeout / API Gateway's 29s hard ceiling alongside the `Retrieve` call's own latency |

## Implementation

`lambda/retrieval/handler.py` now has:
- `retrieve(query)` — Bedrock `Retrieve`, returns `resp["retrievalResults"]` (flat list of
  `{content: {text}, location: {s3Location: {uri}}}`, distinct from `RetrieveAndGenerate`'s nested
  `citations[].retrievedReferences[]` shape)
- `build_sources(results)` — dedups by S3 URI, same shape as before (`doc_title`, `source_key`)
- `_get_gemini_client()` — lazily fetches the API key from Secrets Manager on first invocation only
  (not at module import time) and caches the `genai.Client` at module scope across warm invocations
- `generate_answer(query, results)` — builds a context-grounded prompt from the retrieved chunks and
  calls `client.models.generate_content`; `response.text` can be `None` (blocked/empty candidates),
  guarded with `or ""`

IAM changes on `RetrievalLambdaRole`: kept `bedrock:Retrieve` (was already granted), dropped
`bedrock:RetrieveAndGenerate` and the `bedrock:InvokeModel` statement (Command R+ is no longer
called), added `secretsmanager:GetSecretValue` scoped to `GeminiApiKeySecret`'s ARN.

## Rollback

Rely on git history (`git revert`), not commented-out dead code — this repo doesn't keep archived
code paths in-file. If Gemini underperforms or the original Bedrock issue gets resolved, reverting
the relevant commits restores `RetrieveAndGenerate`, the `GenerationModelId` parameter, and the old
IAM statements in one step.
