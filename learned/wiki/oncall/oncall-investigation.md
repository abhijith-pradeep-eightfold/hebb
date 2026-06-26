# Oncall investigation — ticket types

**Summary:** The umbrella page for handling PagerDuty oncall tickets against the `EightfoldAI/vscode` stack. Each **ticket type** (alarm family) gets its own page documenting: the alarm + backing metric, the step-by-step investigation flow, and the skills/scripts that automate it. Start an oncall from the matching ticket-type page; this page holds the shared discipline and the catalog.

## Shared discipline

Anchor on the real metric first, then correlate — the general method is [[../process/incident-metric-correlation|incident metric-correlation discipline]] (confirm the metric curve and its true window before attributing a cause; watch cross-source timezones). The standard oncall arc:

1. **Read the alarm.** Note the **region** and the **resource type** (queue, host, collection…). The alarm is usually a CloudWatch alarm; some non-AWS cloud types page from Azure/OCI equivalents instead. Pull the alarm definition to learn the backing metric and threshold before looking at anything else.
2. **Characterize the metric.** Pull the underlying timeseries over the incident window plus a baseline; establish the spike shape (sudden vs gradual, peak vs threshold, sustained vs blip) — a non-correlation is itself a finding.
3. **Find the driver.** Break the load down by the dimension that identifies the cause (per-op / per-tenant / per-host) over the confirmed window.
4. **Trace and route.** Trace the driver to its root, then resolve **ownership** of the responsible code so the incident reaches the right team.

## Ticket types

- **Queue backed up** (SQS queue-depth alarm) → [[queue-backed-up|Queue backed up]].
- **Solr CPU too high** (EC2 host-CPU alarm) → covered by [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm]] + the [[../process/incident-metric-correlation|metric-correlation discipline]] (and the `solr-shard-cpu` skill).

New ticket types are added here as their incidents are compiled — each as its own page with the alarm, the flow, and the automating skills/scripts.

## Related skills

- `oncall-queue-backed-up` — the high-level runbook skill for the *Queue backed up* ticket type (each ticket type has one such per-type runbook skill).

## Related

- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — the metric-first correlation method underpinning every oncall.
- [[queue-backed-up|Queue backed up]] — the SQS queue-depth ticket type.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the host-CPU alarm family.
