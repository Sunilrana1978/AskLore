# Investigating High CPU on EC2

## Overview

This runbook covers diagnosing and resolving sustained high CPU utilization on EC2 instances. The CloudWatch alarm `EC2HighCPU` triggers when CPU > 85% for 10 consecutive minutes.

## Step 1 — Identify the Affected Instance

```bash
# List instances with high CPU from CloudWatch
aws cloudwatch list-metrics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --query "Metrics[*].Dimensions[?Name=='InstanceId'].Value[]"

# Get current CPU for a specific instance
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=<instance-id> \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average
```

## Step 2 — Connect and Identify the Process

```bash
# Connect via SSM Session Manager (no SSH key needed)
aws ssm start-session --target <instance-id>

# Inside the session:
top -b -n 1 | head -20          # snapshot of top CPU consumers
ps aux --sort=-%cpu | head -15  # sorted by CPU usage
```

Common culprits:
- **Application process** (node, java, python): likely an infinite loop or runaway request
- **dd, gzip, tar**: data migration or backup job consuming CPU
- **kswapd, kworker**: kernel processes — may indicate memory pressure forcing swapping
- **Unknown process**: investigate with `ls -l /proc/<pid>/exe` and `cat /proc/<pid>/cmdline`

## Step 3 — Application Process Running High

If the runaway process is the application:

```bash
# Get thread-level breakdown (Java example)
jstack <pid> | grep -A 5 "runnable"

# For Python: send SIGTERM to get a traceback (if configured)
kill -SIGTERM <pid>

# Check application logs for the time CPU spiked
journalctl -u <service-name> --since "10 minutes ago" | grep -i "error\|exception\|loop"
```

If the process is unresponsive and must be killed:

```bash
kill -9 <pid>
# The process supervisor (systemd/ECS) will restart it
```

## Step 4 — Check for Memory Pressure

If `kswapd` is the culprit, the instance is swapping due to memory exhaustion:

```bash
free -h       # check swap usage
vmstat 1 5    # watch si/so (swap in/out) columns
```

If swap is heavy, the instance needs more memory — scale up or restart the service to free memory.

## Step 5 — Remediation Options

| Root Cause | Remediation |
|---|---|
| Runaway application thread | Restart the service; fix the underlying code |
| Memory pressure / swapping | Scale instance type up; restart service to free heap |
| Legitimate batch job | Move to off-peak hours or dedicated instance |
| Crypto miner / malware | Isolate instance immediately (see Security Incident runbook) |
| High legitimate traffic | Scale out via Auto Scaling Group |

## Step 6 — Auto Scaling (If Applicable)

If the instance is in an ASG and the load is legitimate:

```bash
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name <asg-name> \
  --desired-capacity <current+2>
```

## Escalation

If CPU remains high after restarting the process, or you suspect a security issue (unknown processes, outbound network connections), page the on-call security engineer immediately.
