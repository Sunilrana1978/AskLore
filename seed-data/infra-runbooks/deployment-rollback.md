# Deployment Rollback Procedure

## Overview

Roll back immediately if a deployment causes elevated error rates, latency spikes, or broken functionality. Speed matters — do not spend more than 5 minutes diagnosing before rolling back if user impact is confirmed.

## Decision Criteria

Roll back if, within 15 minutes of a deployment:
- HTTP 5xx error rate > 1% (baseline: < 0.1%)
- p99 latency > 2× pre-deployment baseline
- Any payment transaction failures attributed to the new code
- A critical bug is confirmed by QA or a customer report

## Step 1 — Confirm the Deployment Caused the Issue

```bash
# Check deployment timeline vs error spike
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HTTPCode_Target_5XX_Count \
  --dimensions Name=LoadBalancer,Value=<alb-id> \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Sum
```

If errors began within 5 minutes of the deployment, assume causation and roll back.

## Step 2 — Rollback via CodeDeploy (Preferred)

If the blue/green deployment is still in progress or the CodeDeploy deployment ID is known:

```bash
aws deploy stop-deployment \
  --deployment-id <codedeploy-deployment-id> \
  --auto-rollback-enabled
```

This stops traffic shifting and reverts to the previous (blue) task set. Traffic is fully on blue within 60 seconds.

## Step 3 — Rollback via ECS Task Definition

If CodeDeploy is no longer active, roll back by pinning the previous task definition:

```bash
# Find the previous revision
aws ecs describe-services --cluster prod-cluster --services svc-api \
  --query "services[0].taskDefinition"

# The current is N, roll back to N-1
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-api \
  --task-definition svc-api:<N-1>
```

Monitor the replacement:

```bash
watch -n 10 "aws ecs describe-services --cluster prod-cluster --services svc-api \
  --query 'services[0].{Running:runningCount,Desired:desiredCount}'"
```

## Step 4 — Verify Recovery

```bash
# Monitor 5xx rate — should drop to baseline within 2 minutes
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HTTPCode_Target_5XX_Count \
  --dimensions Name=LoadBalancer,Value=<alb-id> \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Sum

# Confirm health endpoint
curl -sf https://api.example.com/health
```

## Step 5 — Communicate and Follow Up

1. Post in `#incidents` and `#deployments`: rollback completed, reason, time.
2. Revert the merged PR (create a revert PR, merge immediately).
3. Open a bug ticket with CloudWatch logs, error samples, and timeline.
4. Do not re-deploy the same code until the bug is fixed and the fix is reviewed.

## Troubleshooting

**ECS not replacing tasks:** Check for a deployment circuit breaker — if the new tasks were also unhealthy, ECS may have already stopped the deployment. Verify task logs in CloudWatch.

**Both blue and green unhealthy:** This indicates a deeper infrastructure issue, not just the new code. Escalate to SEV-1 and page the on-call lead.
