# On-Call Escalation Steps

## Overview

This runbook defines the escalation chain and decision criteria for on-call engineers. Follow this when an incident cannot be resolved within the first response SLA or when severity warrants immediate leadership involvement.

## Severity Definitions

| Severity | Criteria | Initial Response SLA | Escalation Trigger |
|---|---|---|---|
| SEV-1 | Complete outage, revenue impact, data loss | 5 minutes | No resolution in 15 min |
| SEV-2 | Partial outage or major feature broken | 15 minutes | No resolution in 30 min |
| SEV-3 | Degraded performance, workaround available | 30 minutes | No resolution in 2 hours |
| SEV-4 | Minor issue, cosmetic, no user impact | Best effort | N/A |

## Step 1 — Acknowledge and Assess

1. Acknowledge the PagerDuty alert within the SLA window.
2. Open the incident Slack channel: `#incidents` → "Create Incident".
3. Assess severity using the table above.
4. Post initial assessment: what is broken, scope of impact, what you know so far.

## Step 2 — Declare the Incident

For SEV-1 and SEV-2, declare formally in Slack:

```
🔴 INCIDENT DECLARED — SEV-{1|2}
Summary: <one sentence>
Impact: <number of users / services affected>
IC: @your-name
Bridge: <Zoom link>
```

The Incident Commander (IC) coordinates — they do not debug. Assign a separate engineer to debug.

## Step 3 — Escalation Chain

### SEV-1 Escalation

| Time Since Declare | Action |
|---|---|
| T+0 | Page on-call engineering lead via PagerDuty (policy: `eng-lead-oncall`) |
| T+15 min | Page VP Engineering via PagerDuty (policy: `vp-eng-oncall`) |
| T+30 min | CEO and CTO notified by VP Eng — do not page directly |

### SEV-2 Escalation

| Time Since Declare | Action |
|---|---|
| T+0 | Notify engineering lead via Slack DM |
| T+30 min | Page engineering lead via PagerDuty if not resolved |
| T+60 min | Page VP Engineering |

### Escalating a Specific Domain

For issues in specific domains, page the relevant team lead directly:

- **Payments:** `payments-oncall` PagerDuty policy
- **Data / DB:** `data-oncall` PagerDuty policy
- **Security / suspected breach:** Page CISO immediately, do not investigate alone

## Step 4 — Customer Communication

For SEV-1 and SEV-2, the Customer Success lead must post a status page update within 15 minutes of declaration.

- Status page: status.example.com (managed by CS team)
- Template: "We are aware of an issue affecting [feature]. Our team is actively investigating. We will provide an update in 30 minutes."

Do not provide technical root-cause details on the public status page.

## Step 5 — Resolution and Post-Incident

1. Confirm the fix is stable for 10 minutes before declaring resolved.
2. Post all-clear in `#incidents` and close the PagerDuty incident.
3. Update the status page to "Resolved."
4. File a post-mortem within 48 hours (template: Notion → Engineering → Post-Mortems).

## Contacts

| Role | PagerDuty Policy | Slack |
|---|---|---|
| On-call engineer | `primary-oncall` | `#oncall` |
| Engineering lead | `eng-lead-oncall` | `#eng-leads` |
| VP Engineering | `vp-eng-oncall` | DM only |
| Payments team | `payments-oncall` | `#team-payments` |
| Data team | `data-oncall` | `#team-data` |
