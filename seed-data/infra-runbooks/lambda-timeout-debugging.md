# Debugging Lambda Timeouts

## Overview

Lambda functions that hit their configured timeout emit a `Task timed out after X seconds` error and the invocation is counted as an error. This runbook covers finding the cause and fixing it.

## Step 1 — Confirm Timeouts Are Occurring

```bash
# Check for timeout errors in the last hour
aws logs filter-log-events \
  --log-group-name /aws/lambda/<function-name> \
  --filter-pattern "Task timed out" \
  --start-time $(($(date +%s) - 3600))000 \
  --query "events[*].message"
```

Also check CloudWatch metrics:

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=<function-name> \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Sum
```

## Step 2 — Find the Slow Operation

Enable Lambda X-Ray tracing if not already on:

```bash
aws lambda update-function-configuration \
  --function-name <function-name> \
  --tracing-config Mode=Active
```

Then look at X-Ray traces to pinpoint the slow subsegment (DB call, HTTP request, Bedrock invocation, etc.):

```bash
aws xray get-traces \
  --filter-expression 'responsetime > 25 AND resource.arn = "arn:aws:lambda:..."' \
  --start-time $(date -u -v-1H +%s) \
  --end-time $(date -u +%s)
```

## Step 3 — Common Root Causes

### Downstream Service Slow or Unavailable

If the timeout corresponds to an external call (RDS, OpenSearch, Bedrock, third-party API):

```bash
# Check if the downstream service is healthy
curl -sf https://<downstream-endpoint>/health

# Check for VPC DNS resolution issues (Lambda in VPC)
# In Lambda logs: look for "getaddrinfo ENOTFOUND" errors
```

For Lambda functions in a VPC: confirm the function's subnets have a route to a NAT Gateway (for internet-bound calls) or VPC endpoints (for AWS service calls).

### Cold Start Latency

Cold starts add 500–3000ms depending on runtime and package size. If timeouts happen intermittently after scale-up:

- Reduce deployment package size (remove unused dependencies)
- Enable Lambda SnapStart (for Java)
- Set a Provisioned Concurrency value to keep instances warm

```bash
aws lambda put-provisioned-concurrency-config \
  --function-name <function-name> \
  --qualifier <alias> \
  --provisioned-concurrent-executions 5
```

### Memory-Bound Execution

Insufficient memory causes Lambda to throttle CPU proportionally. Doubling memory doubles CPU:

```bash
aws lambda update-function-configuration \
  --function-name <function-name> \
  --memory-size 1024   # was 512
```

Use AWS Lambda Power Tuning to find the optimal memory/cost setting.

### Infinite Loop or Unhandled Retry

Check for a loop in the code triggered by a malformed input event. Look at the event payload in CloudWatch logs:

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/<function-name> \
  --filter-pattern "EVENT" \
  --start-time $(($(date +%s) - 3600))000
```

## Step 4 — Immediate Mitigation

If timeouts are causing a cascading failure, temporarily increase the timeout limit:

```bash
aws lambda update-function-configuration \
  --function-name <function-name> \
  --timeout 300   # max is 900 seconds
```

This buys time while the root cause is investigated, but is not a permanent fix.

## Step 5 — Verify Fix

After the code or configuration change, monitor for 10 minutes:

```bash
watch -n 30 "aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=<function-name> \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Sum"
```
