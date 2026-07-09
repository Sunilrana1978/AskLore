# Postmortem: Cache Stampede After Redis Flush

**Date:** 2026-03-20
**Severity:** SEV-2
**Duration:** 11 minutes (16:40–16:51 UTC)
**Author:** Platform team
**Status:** Final

## Summary

A manual Redis cache flush, performed to clear stale product-catalog entries after a bad deploy, was executed during business hours instead of the recommended low-traffic window. The resulting cache stampede overwhelmed `prod-db-cluster` with simultaneous cache-miss queries from every concurrent request, dropping the primary DB's available connections and causing a brief, broad slowdown across checkout, catalog, and account services.

## Impact

- Elevated latency (p99 2.1s, normally 150ms) across `svc-checkout`, `svc-catalog`, `svc-account` for 11 minutes
- No hard errors — degraded but available throughout
- 90 support tickets referencing "site is slow"

## Timeline (UTC)

- **16:38** — Engineer runs `redis-cli FLUSHDB` against `prod-cache` to clear a stale catalog entry, per [Redis Cache Flush Procedure](../infra-runbooks/redis-cache-flush.md) — but during peak traffic, not the recommended window
- **16:40** — Latency alarms fire across three services simultaneously
- **16:42** — On-call notices the timing correlation with the flush in `#platform-eng`
- **16:44** — `prod-db-cluster` connection count and CPU both spike as every request now misses cache and queries the DB directly
- **16:46** — Cache begins repopulating as requests complete and write their results back
- **16:51** — Latency returns to baseline as cache hit rate recovers above 90%

## Root Cause

`FLUSHDB` clears the entire cache, not just the stale key. The runbook's guidance to flush only during low-traffic windows exists precisely because a full flush during peak load causes every subsequent request to miss cache at once — a classic stampede. The engineer was aware of the stale-entry problem but reached for the broadest tool (full flush) rather than targeted key deletion, and did not check the current traffic window first.

## Detection

Latency alarms across three unrelated-looking services fired within 2 minutes, which is what led the on-call engineer to correlate them with the recent manual action rather than suspect three independent failures.

## Resolution

No intervention was needed beyond monitoring — the cache repopulated naturally as traffic continued, and the DB connection pool was never fully exhausted (unlike the [2026-02-14 payment outage](2026-02-14-payment-service-outage.md)), so the incident self-resolved once the initial wave of cache misses cleared.

## Action Items

- [x] Update the Redis Cache Flush runbook to require explicit confirmation of current traffic level before `FLUSHDB`, not just a suggested time window
- [x] Add `DEL` / pattern-based key deletion examples to the runbook as the preferred alternative to full flush for single-key staleness issues
- [ ] Evaluate request coalescing (single in-flight DB query per cache-miss key, shared across concurrent requests) to blunt future stampedes regardless of cause
- [ ] Add a Redis cache hit-rate dashboard panel visible on the main on-call dashboard

## Lessons Learned

**What went well:** Fast correlation between the manual action and the alarm pattern avoided a longer, more confused investigation.

**What went poorly:** The existing runbook's "prefer low-traffic windows" guidance was advisory rather than enforced, and there was no lower-blast-radius alternative documented for the common case of a single stale key.
