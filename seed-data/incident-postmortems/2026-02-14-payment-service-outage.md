# Postmortem: Payment Service Outage — DB Connection Pool Exhaustion

**Date:** 2026-02-14
**Severity:** SEV-1
**Duration:** 47 minutes (14:02–14:49 UTC)
**Author:** Payments team
**Status:** Final

## Summary

`svc-payment` returned 503s for all checkout requests for 47 minutes after a deploy introduced a connection leak. Every request that failed to acquire a DB connection from the pool held it open instead of releasing it on error, exhausting `prod-db-cluster`'s available connections within minutes of increased traffic.

## Impact

- ~14,000 checkout attempts failed
- Estimated $210K in delayed/lost transactions (recovered: ~$180K on retry after resolution)
- Customer Success fielded 340+ support tickets

## Timeline (UTC)

- **13:55** — `svc-payment` v2.14.0 deployed (adds retry logic around the Stripe charge call)
- **14:02** — `PaymentSvcHighMemory` and `PaymentSvcErrorRate` alarms fire
- **14:04** — On-call engineer acknowledges, declares SEV-1, opens bridge
- **14:09** — `prod-db-cluster` connection count confirmed at max (100/100) via RDS Performance Insights
- **14:15** — Root cause suspected: new retry path in v2.14.0 doesn't close the DB connection in its `except` branch
- **14:22** — Decision: rollback per the Deployment Rollback runbook rather than hotfix under pressure
- **14:31** — Rollback to v2.13.2 completed via `aws ecs update-service --task-definition svc-payment:previous-revision`
- **14:36** — Connection count begins dropping as leaked connections time out
- **14:49** — Error rate back to baseline; incident declared resolved

## Root Cause

The retry wrapper added in v2.14.0 caught `StripeConnectionError` to retry the charge, but the `except` block didn't release the DB connection acquired earlier in the request (used to write the pending transaction row). Under normal Stripe latency this leaked slowly; a brief Stripe latency blip that morning triggered enough retries to exhaust the pool in under 10 minutes.

## Detection

CloudWatch alarms fired within 2 minutes of the leak becoming acute. Detection was fast; root-causing took longer because the connection leak wasn't the first hypothesis — initial suspicion was on Stripe's side given the retry logic's proximity to the charge call.

## Resolution

Rolled back to the previous task definition revision (see [Deployment Rollback](../infra-runbooks/deployment-rollback.md) and [Restarting the Payment Service](../infra-runbooks/payment-service-restart.md)). A rolling restart alone would not have fixed this — the bad code path was still present in v2.14.0.

## Action Items

- [x] Add a `finally` block to guarantee connection release regardless of exception path (shipped in v2.14.1)
- [x] Add a CloudWatch alarm on `prod-db-cluster` connection count at 80% of max, not just at exhaustion
- [ ] Require a load test against a connection-pool-exhaustion scenario before any PR touching DB session handling merges
- [ ] Add automatic circuit-breaker on `svc-payment` that sheds load when connection pool utilization exceeds 90%

## Lessons Learned

**What went well:** The on-call engineer correctly chose rollback over an in-place hotfix under SEV-1 pressure, which resolved the incident faster than debugging the leak live would have.

**What went poorly:** Code review did not catch the missing connection release because the diff was reviewed in isolation from the resource-acquisition code a few lines above, in a different function.
