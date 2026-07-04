# Auto Scaling Group Troubleshooting

## Overview

This runbook covers diagnosing Auto Scaling Group (ASG) failures: instances not launching, scale-out stuck, or scale-in terminating the wrong instances.

## Step 1 — Check ASG Activity History

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name <asg-name> \
  --max-records 20 \
  --query "Activities[*].{Time:StartTime,Status:StatusCode,Cause:Cause,Description:Description}" \
  --output table
```

Activity statuses:
- `Successful` — instance launched or terminated as expected
- `Failed` — launch failed; check the `Description` field for the error
- `Cancelled` — scaling cancelled (often by a conflicting scale action)

## Step 2 — Instances Not Launching

### Check Launch Template

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names <asg-name> \
  --query "AutoScalingGroups[0].{LaunchTemplate:LaunchTemplate,Min:MinSize,Max:MaxSize,Desired:DesiredCapacity}"

aws ec2 describe-launch-template-versions \
  --launch-template-id <lt-id> \
  --versions '$Latest' \
  --query "LaunchTemplateVersions[0].LaunchTemplateData"
```

Common launch template issues:
- AMI ID no longer exists (deleted or wrong region)
- Instance type not available in the configured AZs
- IAM instance profile doesn't exist

### Check Service Limits

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-1216C47A  # Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances
```

If the vCPU limit is hit, request a quota increase via the Service Quotas console.

### Check Spot Availability (If Using Spot)

If the ASG uses Spot instances, the configured instance type may be unavailable:

```bash
aws ec2 describe-spot-price-history \
  --instance-types m5.xlarge \
  --product-descriptions "Linux/UNIX" \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --query "SpotPriceHistory[*].{AZ:AvailabilityZone,Price:SpotPrice}"
```

If no prices are returned, the instance type is unavailable. Add alternative instance types to the ASG's mixed instances policy.

## Step 3 — Scale-Out Not Triggering

### Check Scaling Policies

```bash
aws autoscaling describe-policies \
  --auto-scaling-group-name <asg-name> \
  --query "ScalingPolicies[*].{Name:PolicyName,Type:PolicyType,Metric:TargetTrackingConfiguration.CustomizedMetricSpecification}"
```

### Check CloudWatch Alarm State

```bash
aws cloudwatch describe-alarms \
  --alarm-names <alarm-name> \
  --query "MetricAlarms[0].{State:StateValue,Reason:StateReason,Metric:MetricName}"
```

If the alarm is in `INSUFFICIENT_DATA`, the metric has stopped reporting. Check if the CloudWatch agent on instances is running.

### Check Cooldown Period

After a scale-out event, the ASG enters a cooldown period (default: 300 seconds) during which additional scale-out actions are suppressed. Check when the last scaling activity was:

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name <asg-name> --max-records 1 \
  --query "Activities[0].StartTime"
```

## Step 4 — Scale-In Terminating the Wrong Instance

ASG uses a termination policy to choose which instance to terminate first. Default policy: `Default` (oldest launch config, then closest to billing hour).

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names <asg-name> \
  --query "AutoScalingGroups[0].TerminationPolicies"
```

To protect a specific instance from termination:

```bash
aws autoscaling set-instance-protection \
  --instance-ids <instance-id> \
  --auto-scaling-group-name <asg-name> \
  --protected-from-scale-in
```

## Step 5 — Manual Scaling Override

To temporarily set a fixed instance count (bypassing scaling policies):

```bash
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name <asg-name> \
  --min-size 3 \
  --desired-capacity 3
```

Remember to restore the original values and re-enable scaling policies after the incident.
