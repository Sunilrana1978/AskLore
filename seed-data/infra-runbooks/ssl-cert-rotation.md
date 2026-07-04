# SSL/TLS Certificate Rotation

## Overview

This runbook covers rotating SSL/TLS certificates for all public-facing services. Certificates must be rotated before expiry (typically 30 days before) or immediately after a suspected private key compromise.

## Prerequisites

- Access to AWS Certificate Manager (ACM) or the target certificate store
- IAM permissions: `acm:RequestCertificate`, `acm:DeleteCertificate`, `elasticloadbalancing:ModifyListener`
- DNS admin access (Route 53 or external DNS) for domain validation
- Notify the on-call team before rotating certs on production load balancers

## Checking Certificate Expiry

```bash
# List all ACM certificates and their expiry dates
aws acm list-certificates --query "CertificateSummaryList[*].[DomainName,CertificateArn]" --output table

# Check a specific certificate
aws acm describe-certificate --certificate-arn <arn> \
  --query "Certificate.{Domain:DomainName,Expiry:NotAfter,Status:Status}"
```

## Requesting a New Certificate

### ACM-Managed (Recommended)

```bash
aws acm request-certificate \
  --domain-name api.example.com \
  --validation-method DNS \
  --subject-alternative-names "*.api.example.com"
```

After requesting, retrieve the DNS validation CNAME:

```bash
aws acm describe-certificate --certificate-arn <new-arn> \
  --query "Certificate.DomainValidationOptions"
```

Add the CNAME record to Route 53 and wait for status to become `ISSUED` (usually 5–30 minutes).

### Verifying Validation

```bash
aws acm wait certificate-validated --certificate-arn <new-arn>
echo "Certificate validated and ready"
```

## Attaching the New Certificate to the Load Balancer

```bash
# Find the HTTPS listener ARN
aws elbv2 describe-listeners --load-balancer-arn <alb-arn> \
  --query "Listeners[?Protocol=='HTTPS'].ListenerArn" --output text

# Update the listener with the new cert
aws elbv2 modify-listener \
  --listener-arn <listener-arn> \
  --certificates CertificateArn=<new-cert-arn>
```

## Verification

```bash
# Verify the cert served by the ALB
echo | openssl s_client -connect api.example.com:443 -servername api.example.com 2>/dev/null \
  | openssl x509 -noout -dates

# Confirm no browser SSL warnings by visiting https://api.example.com in a browser
```

## Deleting the Old Certificate

Wait at least 24 hours after the new cert is confirmed working before deleting the old one.

```bash
aws acm delete-certificate --certificate-arn <old-cert-arn>
```

## Troubleshooting

**Validation stuck in PENDING_VALIDATION:** Confirm the CNAME record is present in DNS — use `dig CNAME _<token>.api.example.com` to verify propagation.

**ALB still serving old cert:** Check if the old cert is set as the default; additional certs attached to the listener are not default unless explicitly set.

**Certificate in use error on delete:** Remove the cert from all listeners before deleting.
