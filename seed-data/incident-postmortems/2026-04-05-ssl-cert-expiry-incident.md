# Postmortem: API Outage — Expired SSL Certificate

**Date:** 2026-04-05
**Severity:** SEV-1
**Duration:** 34 minutes (03:11–03:45 UTC)
**Author:** Platform team
**Status:** Final

## Summary

The TLS certificate for `api.example.com` expired at 03:11 UTC, causing every client (browser and server-to-server) performing certificate validation to reject connections outright. The certificate's renewal had been automated for over a year, but a change to the ACM validation DNS records six weeks earlier silently broke the auto-renewal without any alert firing.

## Impact

- Full API outage — 100% of requests to `api.example.com` failed at the TLS handshake
- All checkout, catalog, and account traffic affected equally (nothing reached the application layer)
- Occurred at 03:11 UTC (low-traffic window), which limited the blast radius to ~4,000 failed requests rather than a peak-hour equivalent of 10x that

## Timeline (UTC)

- **~2 months prior** — DNS records for ACM certificate validation were modified during an unrelated Route 53 cleanup, unintentionally removing the CNAME record ACM uses to auto-renew
- **03:11** — Certificate expires; `CertExpiryWarning` alarm (30-day warning) had fired weeks earlier but was acknowledged and not acted on, since the team believed renewal was automatic
- **03:11** — `ApiAvailability` synthetic canary begins failing
- **03:13** — Page fires, on-call begins investigating, initially suspects a deploy or infra issue since TLS handshake failures don't surface application logs
- **03:22** — `openssl s_client -connect api.example.com:443` confirms certificate `NotAfter` date is in the past
- **03:25** — Manual certificate renewal initiated per [SSL/TLS Certificate Rotation](../infra-runbooks/ssl-cert-rotation.md)
- **03:25** — ACM renewal request fails silently again — the missing CNAME record is discovered as the actual root cause
- **03:38** — CNAME record restored; ACM issues the new certificate
- **03:42** — New certificate deployed to the ALB listener
- **03:45** — Canary recovers, incident resolved

## Root Cause

ACM's DNS-validated auto-renewal depends on a specific CNAME record persisting in Route 53 indefinitely. An unrelated DNS cleanup six weeks prior removed it without anyone realizing the record was load-bearing for certificate renewal, since it isn't obviously connected to anything at a glance. The 30-day expiry warning alarm fired as designed, but was dismissed on the (incorrect) assumption that renewal was fully automatic and required no human action.

## Detection

Detection was immediate via the availability canary, but diagnosis was slower than typical because a TLS handshake failure produces no application-level error to search logs for — the investigation had to start from `openssl s_client` output rather than CloudWatch Logs.

## Resolution

Restored the missing ACM validation CNAME record, which allowed ACM to reissue the certificate, followed by manual deployment of the new certificate to the ALB per the standard rotation runbook.

## Action Items

- [x] Treat `CertExpiryWarning` alarms as requiring explicit verification of successful renewal, not just acknowledgment
- [x] Add a weekly synthetic check that specifically validates `NotAfter` on all production certificates, independent of the 30-day ACM warning
- [ ] Tag all DNS records required for certificate auto-renewal with a `DO-NOT-DELETE: cert-validation` comment/tag in Route 53
- [ ] Add a Route 53 change-review step that cross-checks any deleted CNAME against the ACM certificate validation record list before applying

## Lessons Learned

**What went well:** The availability canary caught the outage within seconds of expiry, and the low-traffic timing limited real-world impact.

**What went poorly:** A silent, months-earlier change (the DNS cleanup) had no immediate consequence, which is exactly what made it dangerous — the causal link to this incident was six weeks removed from the triggering action. Automated renewal that fails silently is worse than no automation, because it erodes the operational muscle memory of manually verifying certificates.
