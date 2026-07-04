# RDS Connection Troubleshooting

## Overview

This runbook covers diagnosing RDS PostgreSQL connection failures, connection pool exhaustion, and authentication errors for `prod-db-cluster`.

## Common Symptoms

- Application errors: `too many connections`, `connection refused`, `SSL connection has been closed unexpectedly`
- CloudWatch alarm: `RDSConnectionsHigh` (threshold: > 900 connections; max: 1000)
- Sudden connection drop in CloudWatch `DatabaseConnections` metric

## Step 1 — Check Current Connection Count

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBClusterIdentifier,Value=prod-db-cluster \
  --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Maximum
```

## Step 2 — Identify Connection Sources

Connect to the DB (use read replica to avoid adding load to primary):

```bash
psql -h prod-db-replica-1.cluster-ro-<id>.rds.amazonaws.com \
  -U dbadmin -d proddb
```

```sql
-- Count connections by application name and state
SELECT application_name, state, count(*)
FROM pg_stat_activity
GROUP BY application_name, state
ORDER BY count(*) DESC;

-- Find long-running idle connections
SELECT pid, application_name, state, state_change,
       now() - state_change AS idle_duration
FROM pg_stat_activity
WHERE state = 'idle'
  AND now() - state_change > interval '10 minutes'
ORDER BY idle_duration DESC;

-- Find blocking queries
SELECT pid, query, wait_event_type, wait_event
FROM pg_stat_activity
WHERE wait_event IS NOT NULL;
```

## Step 3 — Connection Pool Exhaustion

If an application is holding too many idle connections, terminate them:

```sql
-- Terminate idle connections from a specific application
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = 'svc-api'
  AND state = 'idle'
  AND now() - state_change > interval '5 minutes';
```

Then restart the affected service to reset its connection pool to the correct size.

Check the application's pool configuration:
- `svc-api`: PgBouncer pool size = 20 per pod, max overflow = 5
- Expected max connections from 10 pods = 250

If pool size was recently changed, confirm it matches the RDS `max_connections` parameter.

## Step 4 — Cannot Connect (Connection Refused / Timeout)

### Check RDS Instance Status

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier prod-db-cluster \
  --query "DBClusters[0].{Status:Status,Endpoint:Endpoint,Port:Port}"
```

### Check Security Group

The application's security group must have an inbound rule allowing port 5432 from the ECS task security group.

```bash
aws ec2 describe-security-groups \
  --group-ids <rds-sg-id> \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\`]"
```

### Check VPC Routing

Confirm the ECS tasks and RDS are in the same VPC, or that VPC peering is configured correctly:

```bash
aws rds describe-db-subnet-groups \
  --db-subnet-group-name prod-db-subnet-group \
  --query "DBSubnetGroups[0].VpcId"
```

## Step 5 — SSL / Authentication Errors

RDS requires SSL. If the application throws SSL-related errors:

1. Confirm the RDS CA bundle is present in the application container at `/etc/ssl/rds-ca.pem`.
2. Verify the connection string includes `sslmode=verify-full`.
3. Check if the RDS certificate was recently rotated — applications may need to reload the CA bundle.

## Escalation

If connections cannot be established after verifying security groups, routing, and instance status, page the on-call data engineer. Do not attempt to modify RDS parameter groups in production without approval.
