# Modifying Security Groups Safely

## Overview

Security group changes in production are high-risk — an incorrect rule can expose services to the internet or cut off legitimate traffic. All production security group changes require a peer review before applying.

## Rules for Production Security Group Changes

1. Never add `0.0.0.0/0` as a source to any inbound rule on a non-public-facing security group.
2. Never open a management port (22/SSH, 3389/RDP) from `0.0.0.0/0` — use SSM Session Manager instead.
3. All changes must be peer-reviewed in the relevant Jira ticket before applying.
4. Test the change in staging first.
5. After applying, verify the intended traffic works AND that unintended access is blocked.

## Step 1 — Identify the Security Group

```bash
# Find a security group by name
aws ec2 describe-security-groups \
  --filters Name=group-name,Values=prod-svc-api-sg \
  --query "SecurityGroups[0].{Id:GroupId,Name:GroupName,VPC:VpcId}"

# List all rules on a security group
aws ec2 describe-security-groups --group-ids <sg-id> \
  --query "SecurityGroups[0].{Inbound:IpPermissions,Outbound:IpPermissionsEgress}"
```

## Step 2 — Adding an Inbound Rule

Always use security group references instead of IP ranges when the source is another AWS service:

```bash
# Allow traffic from the ALB security group on port 8080
aws ec2 authorize-security-group-ingress \
  --group-id <target-sg-id> \
  --protocol tcp \
  --port 8080 \
  --source-group <alb-sg-id>
```

If an IP range is required (e.g., office VPN CIDR):

```bash
aws ec2 authorize-security-group-ingress \
  --group-id <target-sg-id> \
  --protocol tcp \
  --port 443 \
  --cidr 203.0.113.0/24   # office VPN range
```

## Step 3 — Removing an Inbound Rule

```bash
aws ec2 revoke-security-group-ingress \
  --group-id <sg-id> \
  --protocol tcp \
  --port 22 \
  --cidr 0.0.0.0/0   # removing an overly permissive SSH rule
```

**Verify before revoking:** Confirm no legitimate traffic depends on the rule by checking VPC Flow Logs first.

## Step 4 — Verify with VPC Flow Logs

Before removing a rule, check if it is currently being used:

```bash
# Query flow logs in CloudWatch Logs Insights
# Log group: /aws/vpc/flowlogs
fields srcAddr, dstAddr, dstPort, action
| filter dstPort = 22 and action = "ACCEPT"
| stats count() by srcAddr
| sort count() desc
| limit 20
```

If there is recent ACCEPT traffic on the port you are about to close, investigate before revoking.

## Step 5 — Verify After Change

```bash
# Confirm the rule was added/removed
aws ec2 describe-security-groups --group-ids <sg-id> \
  --query "SecurityGroups[0].IpPermissions"

# Test connectivity from the intended source (if possible)
nc -zv <target-private-ip> <port>

# Verify VPC Flow Logs show ACCEPT for the new rule (wait 1–2 minutes)
```

## Step 6 — Emergency Lockdown

If a security group is inadvertently opened to the internet:

```bash
# Remove the overly permissive rule immediately
aws ec2 revoke-security-group-ingress \
  --group-id <sg-id> \
  --protocol tcp \
  --port <port> \
  --cidr 0.0.0.0/0

# Post in #incidents and notify the security team
```

Then review CloudTrail to determine how the rule was added and whether any unauthorized access occurred.

## Troubleshooting

**Change applied but traffic still blocked:** Security groups are stateful but check NACLs (Network ACLs) — they are stateless and apply at the subnet level. A NACL deny overrides a security group allow.

**Cannot revoke a rule — rule not found:** The rule may be specified with a different protocol format. List all rules first and use the exact parameters from the describe output.
