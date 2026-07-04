# Restarting the Payment Service

## Overview

The payment service (`svc-payment`) runs on ECS Fargate behind an ALB. This runbook covers the safe restart procedure, which drains connections before terminating tasks to avoid dropped transactions.

**Never hard-stop payment tasks without draining.** In-flight payment requests that are cut off mid-transaction can leave orders in an inconsistent state requiring manual reconciliation.

## When to Restart

- Memory leak observed (RSS > 1.8 GB per task, CloudWatch alarm: `PaymentSvcHighMemory`)
- Deployment of a new image
- Application deadlock (tasks healthy in ECS but `/health` returning 503)
- Explicit instruction from payment team lead

## Prerequisites

- AWS Console or CLI access with `ecs:UpdateService`, `ecs:StopTask` permissions
- Confirm with payment team lead that a restart window is acceptable
- Check current transaction volume in Grafana (`Payment Transactions / min` dashboard) — avoid restarting during peak hours (11:00–13:00 and 18:00–20:00 UTC)

## Step 1 — Verify Current State

```bash
# Check running task count and status
aws ecs describe-services \
  --cluster prod-cluster \
  --services svc-payment \
  --query "services[0].{Running:runningCount,Desired:desiredCount,Status:status}"

# Check recent task health
aws ecs list-tasks --cluster prod-cluster --service-name svc-payment
```

## Step 2 — Trigger a Rolling Restart

ECS rolling restart replaces tasks one by one without reducing capacity below the minimum healthy percentage (currently 100%).

```bash
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-payment \
  --force-new-deployment
```

Monitor the replacement:

```bash
watch -n 10 "aws ecs describe-services --cluster prod-cluster --services svc-payment \
  --query 'services[0].{Running:runningCount,Pending:pendingCount,Deployments:deployments}'"
```

The old tasks will drain connections (deregistration delay: 30 seconds) before stopping. Expect full replacement in 3–5 minutes.

## Step 3 — Verify Health After Restart

```bash
# ALB target group health
aws elbv2 describe-target-health \
  --target-group-arn <payment-tg-arn> \
  --query "TargetHealthDescriptions[*].{Id:Target.Id,Health:TargetHealth.State}"

# Smoke test the payment health endpoint
curl -sf https://api.example.com/payment/health | jq .
```

Expected response: `{"status":"ok","db":"connected","stripe":"reachable"}`

## Step 4 — Confirm Transaction Flow

Check Grafana for a recovery in `Payment Transactions / min` within 2 minutes of task replacement. If the rate does not recover, escalate to the payment team lead immediately.

## Rollback

If the new tasks are unhealthy, force a rollback to the previous task definition:

```bash
# Get the previous task definition revision
aws ecs describe-task-definition --task-definition svc-payment | jq .taskDefinition.revision

# Update service to N-1
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-payment \
  --task-definition svc-payment:<previous-revision>
```

## Troubleshooting

**Tasks stuck in DEPROVISIONING:** A task holding a DB connection may be blocking drain. Check for long-running DB transactions via RDS Performance Insights before force-stopping.

**Health check failing after restart:** Confirm the new image tag in the task definition matches the deployed artifact. Check CloudWatch Logs group `/ecs/svc-payment` for startup errors.
