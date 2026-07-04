# Redis Cache Flush Procedure

## Overview

This runbook covers safely flushing the Redis cache (ElastiCache cluster `prod-redis`). A cache flush forces all clients to re-fetch data from the database, causing a temporary load spike on RDS. Always assess DB capacity before flushing.

## When to Flush the Cache

- Stale data is causing incorrect behaviour that cannot wait for TTL expiry
- A bad cache write has poisoned a key with corrupt data
- Post-deployment when cached data structures have changed incompatibly
- Security incident: sensitive data must be purged from cache

## When NOT to Flush the Cache

- Do not flush during peak hours (11:00–13:00 and 18:00–20:00 UTC)
- Do not flush if RDS CPU is already > 60% — the cache miss storm will cause a DB overload
- Do not flush the entire cache if only a single key or key pattern is affected — use targeted deletion instead

## Step 1 — Assess DB Load Before Flushing

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBClusterIdentifier,Value=prod-db-cluster \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average
```

Proceed only if RDS CPU < 40%. If higher, scale up the RDS instance or wait for off-peak.

## Step 2 — Targeted Key Deletion (Preferred)

Delete only the affected keys instead of flushing everything:

```bash
# Connect to Redis via an EC2 bastion or ECS task with network access
redis-cli -h prod-redis.abc123.ng.0001.usw2.cache.amazonaws.com -p 6379

# Find matching keys (use SCAN, not KEYS, to avoid blocking)
SCAN 0 MATCH "session:*" COUNT 100

# Delete a specific key
DEL session:user:12345

# Delete all keys matching a pattern using a shell loop
redis-cli -h prod-redis... SCAN 0 MATCH "session:*" COUNT 1000 | \
  tail -n +2 | xargs redis-cli -h prod-redis... DEL
```

## Step 3 — Full Cache Flush

If targeted deletion is not feasible:

```bash
# Notify the team first
# Post in #incidents: "Flushing Redis cache in 2 minutes — expect DB load spike"

redis-cli -h prod-redis.abc123.ng.0001.usw2.cache.amazonaws.com -p 6379 FLUSHALL ASYNC
```

The `ASYNC` flag is critical — it returns immediately and flushes in the background, avoiding a blocking operation.

## Step 4 — Monitor DB Load After Flush

Watch RDS CPU for the next 5 minutes:

```bash
watch -n 15 "aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBClusterIdentifier,Value=prod-db-cluster \
  --start-time $(date -u -v-2M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average \
  --query 'Datapoints[*].Average'"
```

Also watch Redis hit rate — it should rise back to normal (> 80%) within 5–10 minutes as the cache warms:

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name CacheHitRate \
  --dimensions Name=ReplicationGroupId,Value=prod-redis \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average
```

## Step 5 — If DB Load Spikes Dangerously (> 80% CPU)

Enable the circuit breaker in the application config to temporarily return cached defaults rather than hitting the DB:

```bash
aws ssm put-parameter \
  --name /prod/svc-api/cache-circuit-breaker \
  --value "true" \
  --type String \
  --overwrite
```

The application reads this parameter on each request (1-minute poll interval). This reduces DB hits while the cache warms up.

## Troubleshooting

**AUTH error when connecting:** Redis in prod requires an auth token. Retrieve it from Secrets Manager:

```bash
aws secretsmanager get-secret-value \
  --secret-id prod/redis/auth-token \
  --query SecretString --output text
```

**FLUSHALL blocked:** If Redis is processing a large LUA script or a KEYS command, FLUSHALL may queue. Check with `INFO clients` and kill blocking clients if necessary.
