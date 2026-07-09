# Postmortem: Checkout Latency Spike — ALB Health Check Flapping

**Date:** 2026-03-02
**Severity:** SEV-2
**Duration:** 22 minutes (09:14–09:36 UTC)
**Author:** Platform team
**Status:** Final

## Summary

`svc-checkout`'s ALB target group began flapping targets healthy/unhealthy every 30–60 seconds, causing p99 checkout latency to climb from 180ms to 4.2s. Root cause was a health check endpoint that queried the database directly, so any DB latency blip was misreported as service unhealthiness — compounding the very problem it was trying to detect.

## Impact

- p99 checkout latency exceeded 4s for 22 minutes
- No full outage — checkout succeeded but slowly, and some client-side timeouts occurred
- ~600 checkout abandonments attributed to the incident (based on funnel drop-off comparison to baseline)

## Timeline (UTC)

- **09:14** — `ALB Health Check Failures` alarm fires for `svc-checkout` target group (see [ALB Health Check Failures](../infra-runbooks/load-balancer-health-checks.md))
- **09:16** — On-call confirms targets cycling in/out of the target group every 30–60s
- **09:20** — `prod-db-cluster` shows elevated but not critical query latency (p99 380ms, normally 40ms) — a batch analytics job started at 09:12 was consuming shared IOPS
- **09:24** — Root cause identified: `/health` handler runs `SELECT 1` against the primary DB with a 200ms timeout; under the batch job's IOPS pressure, queries occasionally exceeded 200ms, failing health checks
- **09:28** — Batch job paused manually to relieve DB pressure
- **09:33** — Target group stabilizes, latency recovers
- **09:36** — Incident resolved

## Root Cause

The `/health` endpoint was designed to catch "app is up but DB is unreachable" failures, which is reasonable in principle. In practice it made the health check's availability a function of DB query latency under shared load, rather than a true liveness signal — a slow dependency became indistinguishable from a dead service.

## Detection

CloudWatch health check alarm fired promptly. Diagnosis took ~10 minutes because the DB latency increase was moderate, not severe, and didn't itself trigger an RDS alarm — it only became visible in the context of the health check timeout.

## Resolution

Paused the conflicting batch job. No code change was deployed during the incident; the health check logic itself was flagged for a follow-up fix.

## Action Items

- [x] Change `/health` to check DB connectivity via a cached "last successful query" timestamp instead of a live query
- [x] Move ad-hoc batch/analytics jobs off `prod-db-cluster` onto the read replica
- [ ] Add a scheduling guard that prevents batch jobs from running during known peak checkout windows (11:00–13:00, 18:00–20:00 UTC)
- [ ] Document the shared-IOPS risk in the RDS Connection Troubleshooting runbook

## Lessons Learned

**What went well:** The ALB health check alarm caught the degradation quickly, well before it became a full outage.

**What went poorly:** A liveness check that depends on a shared, contended resource can cause exactly the flapping behavior it's meant to prevent. Health checks should test the narrowest possible thing: "is this process alive and able to serve," not "is the entire dependency chain fast right now."
