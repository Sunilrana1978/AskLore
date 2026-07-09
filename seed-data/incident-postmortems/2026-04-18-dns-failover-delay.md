# Postmortem: Delayed Recovery During DNS Failover

**Date:** 2026-04-18
**Severity:** SEV-2
**Duration:** 26 minutes (extended recovery beyond the expected ~2 minutes)
**Author:** Platform team
**Status:** Final

## Summary

During a planned AZ failover drill, the DNS record update used to redirect traffic away from the affected AZ took far longer to propagate than expected because the record's TTL had drifted to 3,600 seconds instead of the intended 60 seconds. Clients that had cached the old resolution continued routing to the degraded AZ for up to an hour, well past the drill's planned recovery window.

## Impact

- This was a planned drill, not a customer-facing incident, but it revealed a real gap: an unplanned AZ failure would have had the same extended recovery time
- No customer impact during the drill itself (traffic was shifted gradually and monitored)
- Follow-up audit found 3 other production DNS records with similarly drifted TTLs

## Timeline (UTC)

- **T+0** — Drill begins: `prod-cluster`'s primary AZ marked degraded, failover DNS update pushed
- **T+2 min** — Expected recovery time based on documented 60s TTL
- **T+5 min** — Drill lead notices a meaningful fraction of synthetic traffic still resolving to the old AZ
- **T+8 min** — `dig api.example.com` from multiple external vantage points shows inconsistent TTLs, some reporting ~3,500s remaining
- **T+12 min** — Root cause found: the record's TTL was changed to 3600 during an unrelated Route 53 hygiene pass 4 months earlier, with no process to catch TTL drift on failover-critical records
- **T+26 min** — Last observed stale resolution clears as caches with the longest remaining TTL finally expire

## Root Cause

DNS TTL is a contract with every resolver and client cache between the record owner and the eventual consumer — once a client caches a 3,600-second TTL, no amount of urgency after the fact will make it re-resolve sooner. The record was originally configured with a 60-second TTL specifically to support fast failover, but a later, unrelated change reset it to the Route 53 default without anyone recognizing the record's special requirement. See [DNS Propagation Issues](../infra-runbooks/dns-propagation.md) for general propagation troubleshooting — this incident is the specific case where propagation delay was self-inflicted via TTL misconfiguration rather than external resolver caching.

## Detection

Detected during a planned drill specifically because the team was watching synthetic traffic closely; an equivalent real failure might have taken longer to attribute to DNS caching versus assuming the failover itself hadn't worked.

## Resolution

No fix was needed mid-drill — the situation self-resolved once caches naturally expired. The real fix was restoring the correct TTL and auditing other records afterward.

## Action Items

- [x] Restore 60s TTL on `api.example.com` and the 3 other affected failover-critical records
- [x] Document all failover-critical DNS records and their required TTLs in the DNS Propagation runbook
- [ ] Add a scheduled check (weekly) that alerts if any tagged failover-critical record's TTL drifts from its required value
- [ ] Include a TTL verification step in the pre-drill checklist, not just post-drill audit

## Lessons Learned

**What went well:** Running this as a planned drill meant the gap was found with zero customer impact, which is the entire point of drilling failover procedures regularly.

**What went poorly:** A configuration property (TTL) that is critical for one specific record's function looked identical to every other record's TTL in the Route 53 console — nothing marked it as special, so a routine hygiene change silently broke it.
