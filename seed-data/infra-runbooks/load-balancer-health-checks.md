# ALB Health Check Failures

## Overview

When ALB targets are marked unhealthy, traffic stops routing to those instances or ECS tasks. This runbook covers diagnosing why targets are failing health checks and restoring service.

## Step 1 — Identify Unhealthy Targets

```bash
# List all target groups and their unhealthy counts
aws elbv2 describe-target-groups \
  --query "TargetGroups[*].TargetGroupArn" --output text | \
  xargs -I {} aws elbv2 describe-target-health --target-group-arn {} \
  --query "TargetHealthDescriptions[?TargetHealth.State!='healthy']"
```

Check the reason code on each unhealthy target:

| Reason | Meaning |
|---|---|
| `Target.ResponseCodeMismatch` | Health check path returned unexpected HTTP status |
| `Target.Timeout` | Health check request timed out |
| `Target.ConnectionError` | Could not connect to the target on the health check port |
| `Target.NotRegistered` | Target is draining or deregistered |
| `Elb.InternalError` | ALB internal issue — contact AWS Support |

## Step 2 — Verify Health Check Configuration

```bash
aws elbv2 describe-target-groups \
  --target-group-arns <tg-arn> \
  --query "TargetGroups[0].{Path:HealthCheckPath,Port:HealthCheckPort,Protocol:HealthCheckProtocol,Threshold:HealthyThresholdCount,Timeout:HealthCheckTimeoutSeconds,Interval:HealthCheckIntervalSeconds}"
```

Confirm:
- The health check path (`/health`) returns HTTP 200 when the service is healthy
- The port matches the port the application is listening on
- Timeout < Interval (e.g., timeout 5s, interval 30s)

## Step 3 — Test the Health Check Endpoint Directly

Connect to one of the unhealthy instances/tasks and test manually:

```bash
# For ECS tasks — use ECS Exec
aws ecs execute-command \
  --cluster prod-cluster \
  --task <task-id> \
  --container app \
  --interactive \
  --command "/bin/sh"

# Inside the container:
curl -v http://localhost:8080/health
```

Expected response: HTTP 200 with body `{"status":"ok"}`. If the endpoint is returning 5xx or not responding, the application itself is the problem.

## Step 4 — Check Application Logs

```bash
aws logs filter-log-events \
  --log-group-name /ecs/svc-api \
  --filter-pattern "health OR error OR exception" \
  --start-time $(($(date +%s) - 600))000 \
  --query "events[*].message"
```

Common causes found in logs:
- DB connection failure on startup (the `/health` endpoint checks DB connectivity)
- Missing environment variable causing application crash on init
- Out of memory during startup — task exits before health check succeeds

## Step 5 — Security Group Verification

The ALB security group must be allowed to reach the target on the health check port:

```bash
# Get the ALB security group
aws elbv2 describe-load-balancers --names prod-alb \
  --query "LoadBalancers[0].SecurityGroups"

# Verify the target security group allows inbound from the ALB SG
aws ec2 describe-security-groups --group-ids <target-sg-id> \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`8080\`]"
```

## Step 6 — Adjust Health Check Thresholds (Temporary)

If the application takes longer to start than expected (e.g., loading a large ML model):

```bash
aws elbv2 modify-target-group \
  --target-group-arn <tg-arn> \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3
```

This gives the application more time to become healthy before the ALB marks it unhealthy.

## Step 7 — Force Re-registration

If targets are stuck in an unhealthy state after the application is confirmed healthy:

```bash
# Deregister the target
aws elbv2 deregister-targets \
  --target-group-arn <tg-arn> \
  --targets Id=<instance-id>

# Wait 30 seconds, then re-register
aws elbv2 register-targets \
  --target-group-arn <tg-arn> \
  --targets Id=<instance-id>,Port=8080
```

For ECS tasks, let ECS manage registration — force a new deployment instead of manually deregistering.
