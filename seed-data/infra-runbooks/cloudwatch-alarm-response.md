# Responding to CloudWatch Alarms

## Overview

CloudWatch alarms are routed to PagerDuty via an SNS topic. This runbook maps the most common alarms to their investigation and resolution steps.

## Alarm Inventory

| Alarm Name | Threshold | Likely Cause |
|---|---|---|
| `EC2HighCPU` | CPU > 85% for 10 min | Runaway process or traffic spike |
| `RDSConnectionsHigh` | Connections > 900 | Pool leak or traffic spike |
| `LambdaErrorRate` | Errors > 5% for 5 min | Code bug or downstream failure |
| `ALBHighLatency` | p99 > 2s for 5 min | Slow DB queries or large payloads |
| `ALB5xxRate` | 5xx > 1% for 3 min | Application errors or deploy issue |
| `PaymentSvcHighMemory` | Memory > 1.8 GB | Memory leak — restart needed |
| `OpenSearchIndexingLag` | Lag > 60s | Ingestion pipeline backed up |
| `S3BucketSizeHigh` | Size > 500 GB | Unexpected data volume |

## General Response Steps

### Step 1 — Check Alarm History

```bash
aws cloudwatch describe-alarm-history \
  --alarm-name <alarm-name> \
  --history-item-type StateUpdate \
  --start-date $(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ) \
  --query "AlarmHistoryItems[*].{Time:Timestamp,State:historyData}" \
  --output table
```

Determine if this is a new alarm or a recurring one. Recurring alarms indicate a systemic issue.

### Step 2 — Cross-Reference with Deployments

Check if a deployment happened in the 30 minutes before the alarm triggered:

```bash
aws codepipeline list-pipeline-executions \
  --pipeline-name prod-deploy-pipeline \
  --max-results 5 \
  --query "pipelineExecutionSummaries[*].{Status:status,Started:startTime}"
```

If a deployment is correlated, consider rollback (see Deployment Rollback runbook).

### Step 3 — Check the Affected Metric

```bash
aws cloudwatch get-metric-statistics \
  --namespace <namespace> \
  --metric-name <metric-name> \
  --dimensions Name=<dim-name>,Value=<dim-value> \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average Maximum
```

## Specific Alarm Responses

### ALB5xxRate

See: Deployment Rollback or RDS Connection Troubleshooting runbooks.

Quick check:

```bash
aws logs filter-log-events \
  --log-group-name /ecs/svc-api \
  --filter-pattern "\"status\":5" \
  --start-time $(($(date +%s) - 600))000 \
  --query "events[*].message" | head -20
```

### LambdaErrorRate

See: Lambda Timeout Debugging runbook.

Check if the error is throttling (concurrency limit hit):

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Throttles \
  --dimensions Name=FunctionName,Value=<function-name> \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Sum
```

If throttled, request a concurrency limit increase:

```bash
aws lambda put-function-concurrency \
  --function-name <function-name> \
  --reserved-concurrent-executions 200
```

### OpenSearchIndexingLag

The embedding pipeline has fallen behind. Check:

1. EmbeddingLambda error rate (see LambdaErrorRate steps above)
2. Number of unprocessed messages in the SQS DLQ (Phase 2+)
3. OpenSearch collection health via the console

### PaymentSvcHighMemory

```bash
# Trigger a rolling restart immediately
aws ecs update-service \
  --cluster prod-cluster \
  --service svc-payment \
  --force-new-deployment
```

See: Payment Service Restart runbook for full procedure.

## After the Alarm Clears

1. Document what caused the alarm and how it was resolved in the incident notes.
2. If the alarm is noisy (false positives), adjust the threshold with the team before silencing it.
3. Never suppress an alarm without understanding why it fired.
