# CloudWatch Log Analysis Guide

## Overview

This guide covers efficiently searching and analyzing logs across services using CloudWatch Logs Insights, the AWS CLI, and structured log patterns.

## Log Group Inventory

| Service | Log Group |
|---|---|
| API service | `/ecs/svc-api` |
| Payment service | `/ecs/svc-payment` |
| ChunkingLambda | `/aws/lambda/asklore-chunking` |
| EmbeddingLambda | `/aws/lambda/asklore-embedding` |
| RetrievalLambda | `/aws/lambda/asklore-retrieval` |
| VPC Flow Logs | `/aws/vpc/flowlogs` |
| RDS PostgreSQL | `/aws/rds/cluster/prod-db-cluster/postgresql` |
| CloudTrail | `aws-cloudtrail-logs-<account>` (S3, use Athena) |

## CloudWatch Logs Insights Queries

### Find All Errors in the Last Hour

```sql
fields @timestamp, @message
| filter @message like /ERROR|Exception|error/
| sort @timestamp desc
| limit 100
```

### Count Errors by Type

```sql
fields @timestamp, @message
| parse @message '"level":"*"' as level
| filter level = "ERROR"
| stats count() by bin(5m)
```

### Trace a Specific Request by Request ID

All services log a `requestId` field. To trace a request end-to-end:

```sql
fields @timestamp, @logStream, @message
| filter @message like /req-abc123/
| sort @timestamp asc
```

### Find Slow Requests (> 1000ms)

```sql
fields @timestamp, @message
| parse @message '"duration_ms":*,' as duration
| filter duration > 1000
| sort duration desc
| limit 50
```

### Payment Errors in the Last 30 Minutes

```sql
fields @timestamp, @message
| filter @log like /svc-payment/
| filter @message like /FAILED|declined|error/
| sort @timestamp desc
| limit 50
```

## CLI Log Search

For quick searches without opening the console:

```bash
# Search for errors in the last 10 minutes
aws logs filter-log-events \
  --log-group-name /ecs/svc-api \
  --filter-pattern "ERROR" \
  --start-time $(($(date +%s) - 600))000 \
  --query "events[*].message" \
  --output text | head -30

# Tail logs in real time (poll every 5 seconds)
aws logs tail /ecs/svc-api --follow --format short

# Search across multiple log streams for a correlation ID
aws logs filter-log-events \
  --log-group-name /ecs/svc-api \
  --filter-pattern '"correlationId":"abc-123"' \
  --start-time $(($(date +%s) - 3600))000
```

## RDS PostgreSQL Log Analysis

Enable slow query logging on RDS (if not already enabled):

```bash
aws rds modify-db-cluster-parameter-group \
  --db-cluster-parameter-group-name prod-db-params \
  --parameters "ParameterName=log_min_duration_statement,ParameterValue=1000,ApplyMethod=immediate"
```

Then search for slow queries:

```bash
aws logs filter-log-events \
  --log-group-name /aws/rds/cluster/prod-db-cluster/postgresql \
  --filter-pattern "duration:" \
  --start-time $(($(date +%s) - 3600))000 \
  --query "events[*].message"
```

## Lambda Cold Start Analysis

```sql
fields @timestamp, @message, @initDuration
| filter @type = "REPORT"
| filter @initDuration > 0
| stats avg(@initDuration), max(@initDuration), count() by bin(1h)
```

## Setting Up a Log Metric Filter

To create a CloudWatch metric from a log pattern (e.g., count payment failures):

```bash
aws logs put-metric-filter \
  --log-group-name /ecs/svc-payment \
  --filter-name PaymentFailures \
  --filter-pattern '"status":"FAILED"' \
  --metric-transformations \
    metricName=PaymentFailureCount,metricNamespace=AskLore/Business,metricValue=1
```

Then create an alarm on this metric.

## Log Retention Policy

All log groups should have retention set to avoid unbounded storage costs:

```bash
# Set 90-day retention on a log group
aws logs put-retention-policy \
  --log-group-name /ecs/svc-api \
  --retention-in-days 90
```

Standard retention policy: 90 days for application logs, 365 days for security/audit logs.
