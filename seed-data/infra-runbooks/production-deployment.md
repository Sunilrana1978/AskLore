# Production Deployment Guide

## Overview

All production deployments go through the CI/CD pipeline (CodePipeline → CodeBuild → ECS blue/green). Direct deploys bypassing the pipeline are prohibited. This runbook covers the standard deployment flow and manual override steps for emergencies.

## Pre-Deployment Checklist

- [ ] PR merged to `main` and all CI checks green
- [ ] Staging deployment completed and smoke tests passed
- [ ] No active SEV-1 or SEV-2 incidents
- [ ] Not within a merge freeze window (check `#eng-announcements`)
- [ ] Feature flags configured if the change is behind a flag
- [ ] Deployment communicated in `#deployments` Slack channel

## Standard Deployment Flow

### Step 1 — Trigger the Pipeline

Merging to `main` triggers CodePipeline automatically. To monitor:

```bash
aws codepipeline get-pipeline-state --name prod-deploy-pipeline \
  --query "stageStates[*].{Stage:stageName,Status:latestExecution.status}"
```

Or watch in the AWS Console under CodePipeline → `prod-deploy-pipeline`.

### Step 2 — Approve the Production Stage

The pipeline pauses at the manual approval gate before deploying to production. The deploying engineer must approve in CodePipeline (or via CLI):

```bash
# Get the approval token
TOKEN=$(aws codepipeline get-pipeline-state --name prod-deploy-pipeline \
  --query "stageStates[?stageName=='ApproveProduction'].actionStates[0].latestExecution.token" \
  --output text)

aws codepipeline put-approval-result \
  --pipeline-name prod-deploy-pipeline \
  --stage-name ApproveProduction \
  --action-name ManualApproval \
  --result summary="Approved by $(whoami)",status=Approved \
  --token "$TOKEN"
```

### Step 3 — Monitor Blue/Green Deployment

ECS performs a blue/green deployment via CodeDeploy:

```bash
aws deploy get-deployment \
  --deployment-id <id-from-codedeploy> \
  --query "deploymentInfo.{Status:status,Overview:deploymentOverview}"
```

Traffic shifts from blue (old) to green (new) over 10 minutes using a canary strategy (10% → 100%). Monitor error rate in CloudWatch during the shift.

### Step 4 — Verify Deployment

```bash
# Confirm new task definition is running
aws ecs describe-services --cluster prod-cluster --services svc-api \
  --query "services[0].deployments"

# Smoke test
curl -sf https://api.example.com/health | jq .
```

## Emergency Direct Deploy (Break-Glass)

For critical hotfixes when the pipeline is broken:

```bash
# Build and push image directly
docker build -t <ecr-repo>:hotfix-$(git rev-parse --short HEAD) .
docker push <ecr-repo>:hotfix-$(git rev-parse --short HEAD)

# Force new deployment with the hotfix image
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-api \
  --force-new-deployment
```

**Required:** Post in `#incidents` explaining why the pipeline was bypassed. Create a follow-up ticket to backfill the pipeline within 24 hours.

## Rollback

If a deployment causes errors, rollback immediately — do not attempt to fix forward under pressure.

```bash
aws deploy stop-deployment --deployment-id <id> --auto-rollback-enabled
```

Or via ECS:

```bash
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-api \
  --task-definition svc-api:<previous-revision>
```

## Post-Deployment

1. Monitor CloudWatch dashboard for 15 minutes after full traffic shift.
2. Post deployment summary in `#deployments`: what shipped, any issues observed.
3. Resolve any feature flags that were used as a safety net.
