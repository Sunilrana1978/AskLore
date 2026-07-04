# Incident Response Checklist

## Overview

A structured checklist for the Incident Commander (IC) to follow during active incidents. The IC coordinates — they do not debug. The IC's job is to keep the team focused and informed.

## First 5 Minutes

- [ ] Acknowledge the PagerDuty alert
- [ ] Join `#incidents` and announce you are the IC
- [ ] Assess severity (SEV-1 through SEV-4) using the On-Call Escalation runbook
- [ ] Open a Zoom bridge for SEV-1/SEV-2 (link in `#incidents` channel bookmarks)
- [ ] Post initial assessment message:

```
🔴 INCIDENT — SEV-{X}
What: <brief description>
Impact: <users / services affected>
IC: @your-name
Debug lead: @engineer-name
Bridge: <zoom link>
Status: Investigating
```

## First 15 Minutes

- [ ] Assign a debug lead (separate from the IC)
- [ ] Assign a scribe to take notes in the incident thread
- [ ] Pull in domain experts as needed (payments, data, security)
- [ ] For SEV-1: notify engineering lead immediately (see Escalation runbook)
- [ ] For SEV-1/SEV-2: notify Customer Success to prepare a status page update
- [ ] Establish a regular update cadence (every 10 minutes for SEV-1, 20 for SEV-2)

## During the Incident

### IC Update Template (post every N minutes)

```
⏱ UPDATE — T+{X} min
Status: Investigating / Mitigating / Monitoring
Findings: <what we know>
Actions taken: <what was done>
Next step: <what is being tried>
ETA: <estimate or "unknown">
```

### IC Decision Points

**Should we rollback?**
- If a deployment happened in the last 30 minutes and errors are correlated → rollback immediately, debug later.

**Should we scale out?**
- If traffic is the confirmed cause and services are healthy → scale ASG/ECS desired count.

**Should we fail over the database?**
- Only if the primary DB is confirmed degraded and the replica lag is < 100ms.

**Should we declare a customer communication?**
- SEV-1: always. SEV-2: if > 500 users affected or payment flows are impacted.

## Mitigation Confirmed

- [ ] Announce mitigation in incident channel
- [ ] Update status page: "We have identified and applied a fix. We are monitoring for full recovery."
- [ ] Continue monitoring for 15 minutes before declaring resolved
- [ ] Note exact time mitigation was applied (for the post-mortem timeline)

## Resolution

- [ ] Confirm error rates / latency back to baseline for 10+ minutes
- [ ] Update status page: "This incident has been resolved as of HH:MM UTC."
- [ ] Post resolved message in `#incidents`
- [ ] Close PagerDuty incident
- [ ] Ping Customer Success to send any required customer communications

```
✅ RESOLVED — T+{X} min
What fixed it: <summary>
Duration: <start time> to <end time>
Impact: <users affected, transactions lost/delayed>
Post-mortem due: <48 hours from now>
```

## Post-Incident (Within 48 Hours)

- [ ] Write post-mortem using the Notion template (Engineering → Post-Mortems)
- [ ] Schedule a post-mortem review meeting within 72 hours
- [ ] File action items as Jira tickets with owners and due dates
- [ ] Share the final post-mortem in `#eng-all`

## What NOT To Do

- Do not blame individuals in the incident channel or post-mortem
- Do not make production changes without announcing them in the bridge
- Do not close the incident without 10 minutes of stable monitoring
- Do not dismiss an escalation request — if someone says it's SEV-1, treat it as SEV-1 until proven otherwise
