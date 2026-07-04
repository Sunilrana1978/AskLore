# RDS Database Failover Procedure

## Overview

This runbook covers both automatic and manual failover for the primary RDS PostgreSQL cluster (`prod-db-cluster`). The cluster runs Multi-AZ with a standby replica in a different AZ. Failover typically completes in 60–120 seconds.

## When to Trigger Manual Failover

- Scheduled maintenance on the primary AZ
- Primary instance showing sustained high latency (> 500 ms p99) with a healthy replica
- AWS health event affecting the primary instance's AZ
- Pre-planned DR drill

Do **not** trigger manual failover for application-level errors — verify the issue is at the DB layer first.

## Step 1 — Verify Current Primary

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier prod-db-cluster \
  --query "DBClusters[0].{Writer:DBClusterMembers[?IsClusterWriter].DBInstanceIdentifier|[0], Endpoint:Endpoint, Status:Status}"
```

Also check replication lag before failing over:

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name AuroraReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=prod-db-replica-1 \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average \
  --query "Datapoints[*].Average"
```

Replica lag should be < 100 ms before triggering failover. If lag is high, wait or investigate replication issues first.

## Step 2 — Notify Stakeholders

Before manual failover, post in `#incidents`:

```
⚠️ Planned DB failover in 5 minutes.
Services will experience ~60–120s of connection errors during switchover.
IC: @your-name
```

Notify the payment team lead separately — payment processing must be paused or retried during failover.

## Step 3 — Trigger Failover

```bash
aws rds failover-db-cluster --db-cluster-identifier prod-db-cluster
```

Monitor progress:

```bash
watch -n 5 "aws rds describe-db-clusters \
  --db-cluster-identifier prod-db-cluster \
  --query 'DBClusters[0].{Writer:DBClusterMembers[?IsClusterWriter].DBInstanceIdentifier|[0],Status:Status}'"
```

Status will transition: `available` → `failing-over` → `available`. The cluster DNS endpoint (`prod-db-cluster.cluster-<id>.rds.amazonaws.com`) automatically points to the new primary — no application config change needed.

## Step 4 — Verify Application Recovery

```bash
# Check application DB connection errors in CloudWatch
aws logs filter-log-events \
  --log-group-name /ecs/svc-api \
  --filter-pattern "connection refused OR could not connect" \
  --start-time $(($(date +%s) - 300))000
```

Application services use the cluster endpoint and will reconnect automatically. Connection pool exhaustion may cause a brief spike — check `DatabaseConnections` CloudWatch metric on the new primary.

## Step 5 — Post-Failover Checks

1. Confirm the new primary instance is in a different AZ than the old one.
2. Verify a new standby replica has been created (Multi-AZ auto-restores the replica).
3. Check RDS Performance Insights for any long-running queries that were killed during failover.
4. Update the post-mortem if this was an unplanned failover.

## Troubleshooting

**Failover not completing after 5 minutes:** Check the RDS event log in the console. If the replica was too far behind, AWS may abort the failover — verify lag and retry.

**Application not reconnecting:** Some connection pools do not handle DNS TTL changes properly. Restart the affected service if connections remain stale after 3 minutes.

**Read replica still pointing to old primary:** Aurora reader endpoint updates automatically; if using a manually constructed endpoint, update it to point to the new reader.
