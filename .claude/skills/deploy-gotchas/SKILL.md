---
name: deploy-gotchas
description: Use when an AskLore CloudFormation deploy fails or hangs — especially around AossKbIndex, kb-index-setup, AuthorizationException, "no such index", engine type errors, or LogGroup AlreadyExists conflicts. Also use before retrying a failed deploy.
---

# AskLore Deploy Gotchas

These failure modes were already surfaced and fixed once during this project's first real deploy. The code fixes already live in `template.yaml` and `lambda/kb-index-setup/handler.py` — this skill is for recognizing the symptom fast and confirming the fix is intact, not re-debugging from scratch.

## Symptom → cause map

**`AuthorizationException` from OpenSearch Serverless during index create**
AOSS data access policy (`AossAccessPolicy`) reports `CREATE_COMPLETE` in CloudFormation before AOSS's data plane actually starts honoring it — there's a propagation delay of a few seconds. `kb-index-setup` should already retry these for up to ~60s (`AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS` / `_BACKOFF_SECONDS`). If you see this fail outright, check those retry constants weren't removed.

**`AskLoreKnowledgeBase` fails with "no such index" right after `AossKbIndex` reports success**
Index creation succeeding isn't the same as the index being visible on every AOSS read path (including Bedrock's own validation). `kb-index-setup` sleeps `INDEX_SETTLE_SECONDS` (30s) after a fresh create before reporting `SUCCESS` to CloudFormation. Don't remove or shorten this sleep to "speed up" deploys.

**"engine type is invalid" at `AskLoreKnowledgeBase` creation**
OpenSearch Serverless only supports the `faiss` k-NN engine, not `nmslib`. Check the index body in `lambda/kb-index-setup/handler.py` specifies `faiss`.

**`AlreadyExists` on `AossKbIndexFunctionLogGroup` during stack create**
`AossKbIndex` invokes `AossKbIndexFunction` synchronously as a custom resource. If that invocation happens before CloudFormation creates the explicit `AWS::Logs::LogGroup`, Lambda auto-creates an untracked log group on first execution, and the explicit resource then collides. Fix: `AossKbIndex` must `DependsOn` `AossKbIndexFunctionLogGroup`. This same pattern applies to any *new* Lambda invoked synchronously by a CloudFormation custom resource during create/update — Lambdas triggered later by S3/API Gateway don't need it.

**Retrying a failed deploy hits `AlreadyExists` again on `/aws/lambda/asklore-kb-index-setup`**
Rollback invokes the custom resource Lambda once more for its `Delete` lifecycle event; Lambda's async log delivery can recreate the log group right after CloudFormation deletes it. Before retrying:
```bash
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/asklore-kb-index-setup
```
Delete the orphaned log group if present, then retry the deploy.

**`ValidationException` mentioning `aws-marketplace:Subscribe` from `RetrieveAndGenerate`**
Not a code or IAM-policy issue. Bedrock model access for Cohere Command R+ requires completing an AWS Marketplace subscription in the console (Bedrock → Model access → Modify model access), separate from just enabling the model.

## Before retrying any failed deploy

1. Read the actual CloudFormation failure reason (`aws cloudformation describe-stack-events --stack-name <stack> --max-items 20`), don't guess from the symptom list alone.
2. If the failure involves `AossKbIndex` / `kb-index-setup`, check for the orphaned log group first (see above).
3. Never shorten the propagation-retry or settle-sleep constants to work around a failure — they exist because the underlying AWS eventual-consistency delay is real, not because the original values were arbitrary.
