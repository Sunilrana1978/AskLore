# S3 Bucket Access Issues

## Overview

This runbook covers diagnosing S3 access denied errors, bucket policy conflicts, and cross-account access issues.

## Step 1 — Identify the Error

S3 access errors come in a few forms:

- `AccessDenied` — the IAM principal lacks permission or a bucket policy explicitly denies
- `NoSuchBucket` — bucket name is wrong, or the bucket was deleted/renamed
- `NoSuchKey` — the object key doesn't exist (check for trailing slashes or case sensitivity)
- `403 Forbidden` on a presigned URL — URL has expired or the signing credentials were revoked

## Step 2 — Check IAM Permissions

Use the IAM policy simulator to test the principal's permissions:

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::074642417296:role/IngestionLambdaRole \
  --action-names s3:GetObject \
  --resource-arns "arn:aws:s3:::asklore-raw-074642417296-us-west-2/*" \
  --query "EvaluationResults[*].{Action:EvalActionName,Decision:EvalDecision}"
```

If the result is `explicitDeny` or `implicitDeny`, check:
1. The role's inline and managed policies
2. The S3 bucket policy for explicit denies
3. S3 Block Public Access settings (if accessing from outside AWS)

## Step 3 — Check the Bucket Policy

```bash
aws s3api get-bucket-policy \
  --bucket asklore-raw-074642417296-us-west-2 \
  --query Policy --output text | python3 -m json.tool
```

Look for:
- `Effect: Deny` statements that might be catching the principal
- Conditions on `aws:SourceVpc` or `aws:SourceIp` that exclude the caller's source
- Missing `Effect: Allow` for the required action

## Step 4 — Check S3 Block Public Access

If the bucket is intended for private access only (all our buckets are), confirm Block Public Access is enabled:

```bash
aws s3api get-public-access-block \
  --bucket asklore-raw-074642417296-us-west-2
```

All four settings should be `true`. Do not disable these for any production bucket.

## Step 5 — Server-Side Encryption (SSE) Access Issues

If the bucket uses a customer-managed KMS key, the IAM principal must also have permission to use that key:

```bash
aws kms describe-key --key-id <key-id> \
  --query "KeyMetadata.{Id:KeyId,Status:KeyState}"

aws kms get-key-policy --key-id <key-id> --policy-name default \
  --query Policy --output text | python3 -m json.tool
```

Add the required principal to the KMS key policy if missing:

```json
{
  "Effect": "Allow",
  "Principal": {"AWS": "arn:aws:iam::074642417296:role/IngestionLambdaRole"},
  "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
  "Resource": "*"
}
```

## Step 6 — Cross-Account Access

For cross-account S3 access, both the IAM policy in the source account AND the bucket policy in the destination account must allow the action:

```bash
# Bucket policy must include the cross-account principal
{
  "Effect": "Allow",
  "Principal": {"AWS": "arn:aws:iam::<source-account>:role/<role-name>"},
  "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::<bucket-name>/*"
}
```

## Step 7 — Enable S3 Access Logging for Audit

If you need to trace which principal made a request:

```bash
aws s3api put-bucket-logging \
  --bucket asklore-raw-074642417296-us-west-2 \
  --bucket-logging-status '{
    "LoggingEnabled": {
      "TargetBucket": "asklore-logs-074642417296-us-west-2",
      "TargetPrefix": "s3-access-logs/raw-bucket/"
    }
  }'
```

Then search logs with Athena for `403` responses to identify the denied principal.

## Troubleshooting

**Presigned URL expired:** Regenerate with a longer expiry (`--expires-in 3600` for 1 hour max for session-based credentials). Presigned URLs using role credentials expire when the role session expires (max 12 hours).

**Object exists but returns 403:** Check if the object has an ACL that restricts access. Objects in fully private buckets should use the bucket policy, not object ACLs — set `ObjectOwnership: BucketOwnerEnforced` to disable ACLs.
