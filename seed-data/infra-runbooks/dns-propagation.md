# DNS Propagation Issues

## Overview

DNS changes (new records, TTL reductions, record updates) can take time to propagate globally due to caching at resolvers. This runbook covers verifying DNS changes and troubleshooting slow propagation.

## Step 1 — Verify the Change at the Authoritative Nameserver

Always verify the record exists at the authoritative nameserver before blaming propagation:

```bash
# Find the authoritative nameservers for the domain
dig NS example.com +short

# Query the authoritative nameserver directly (bypasses resolver cache)
dig @ns-123.awsdns-45.com api.example.com A +short
```

If the record is wrong at the authoritative server, the issue is with the DNS change itself — fix it in Route 53 first.

## Step 2 — Check Route 53 Record

```bash
# List records in the hosted zone
aws route53 list-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --query "ResourceRecordSets[?Name=='api.example.com.']"
```

Verify:
- Record type is correct (A, CNAME, ALIAS)
- Value points to the correct target
- TTL is set appropriately (300s for most records; 60s for records that change frequently)

## Step 3 — Check Propagation from Multiple Locations

Use `dig` with public resolvers to check propagation:

```bash
# Google Public DNS
dig @8.8.8.8 api.example.com A +short

# Cloudflare
dig @1.1.1.1 api.example.com A +short

# OpenDNS
dig @208.67.222.222 api.example.com A +short
```

If resolvers return different values, propagation is still in progress. Typical propagation times:

| TTL of Old Record | Expected Propagation |
|---|---|
| 300 seconds (5 min) | 5–10 minutes |
| 3600 seconds (1 hour) | 1–2 hours |
| 86400 seconds (1 day) | Up to 48 hours |

## Step 4 — Force Cache Flush (Client-Side)

If a specific machine is caching the old record:

```bash
# macOS
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder

# Linux (systemd-resolved)
sudo systemd-resolve --flush-caches

# Linux (nscd)
sudo nscd -i hosts
```

## Step 5 — Reduce TTL Before Future Changes

Best practice: reduce TTL to 60 seconds at least 1 TTL period before making a DNS change, then restore it after propagation confirms success.

```bash
# Step 1: reduce TTL (do this well in advance)
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "api.example.com.",
        "Type": "A",
        "TTL": 60,
        "ResourceRecords": [{"Value": "<current-ip>"}]
      }
    }]
  }'
```

Wait 1 hour (the old TTL) before making the actual record change.

## Step 6 — CNAME vs ALIAS for AWS Resources

For AWS resources (ALB, CloudFront, API Gateway), always use Route 53 ALIAS records instead of CNAME:

- ALIAS records resolve at Route 53 level (no external DNS hop)
- ALIAS records work at the zone apex (e.g., `example.com` itself, not just `api.example.com`)
- ALIAS records are free (no per-query charge)

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "api.example.com.",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "<alb-hosted-zone-id>",
          "DNSName": "<alb-dns-name>",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

## Troubleshooting

**Record propagated globally but HTTPS still fails:** The SSL certificate may not cover the new domain. Check ACM (see SSL Cert Rotation runbook).

**Propagation taking > 48 hours:** Contact AWS Support if the record appears correct in Route 53 but is not propagating. This is rare but can occur with DNSSEC validation issues.
