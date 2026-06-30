# Oncall investigation — ticket types

**Summary:** The umbrella page for handling PagerDuty oncall tickets against the `EightfoldAI/vscode` stack. Each **ticket type** (alarm family) gets its own page documenting: the alarm + backing metric, the step-by-step investigation flow, and the skills/scripts that automate it. Start an oncall from the matching ticket-type page; this page holds the shared discipline and the catalog.

## Shared discipline

Anchor on the real metric first, then correlate — the general method is [[../process/incident-metric-correlation|incident metric-correlation discipline]] (confirm the metric curve and its true window before attributing a cause; pin each source's timezone before overlaying). The standard oncall arc:

1. **Read the alarm.** Note the **region** and the **resource type** (queue, host, collection…). Take the region from the alarm name / console link. The alarm is usually a CloudWatch alarm; some non-AWS cloud types page from Azure/OCI equivalents instead. Pull the alarm definition to learn the backing metric and threshold before looking at anything else.
2. **Characterize the metric.** Pull the underlying timeseries over the incident window plus a baseline; establish the spike shape (sudden vs gradual, peak vs threshold, sustained vs blip) — a non-correlation is itself a finding.
3. **Find the driver.** Break the load down by the dimension that identifies the cause (per-op / per-tenant / per-host) over the confirmed window. Narrow to the **breach window** so a burst separates from heavy baseline traffic for the same dimension.
4. **Trace and route.** Trace the driver to its root, then resolve **ownership** of the responsible code so the incident reaches the right team.
5. **Report.** Deliver the finding as a **table-structured report** (below), and post it back to the page's Slack thread if asked.

## Ticket types

- **Queue backed up** (SQS queue-depth alarm) → [[queue-backed-up|Queue backed up]].
- **Solr CPU too high** (EC2 host-CPU alarm on a Solr replica) → [[solr-cpu-high|Solr CPU too high]].
- **Alarm Provisioning Failures** (daily-DAG alarm-provisioning failure on the custom `airflow` metric; N datapoints = N independent failing alarm keys) → [[alarm-provisioning-failures|Alarm Provisioning Failures]].
- **RDS CPU too high** (`AWS/RDS CPUUtilization` p75 alarm on a cluster `DBClusterIdentifier`+`Role`, often in GovCloud) → [[rds-cpu-high|RDS CPU too high]].
- **Redis Error Detected** (a per-namespace `redis-errors` `Sum > 100` counter alarm; the error **counter** and the runbook's **log line** are independent sinks, so the prescribed Logs Insights query can return zero on a real spike) → [[redis-errors-detected|Redis Error Detected]].
- **Airflow DAG Failure** (a per-DAG `airflow-airflow.<dag>.failed.sum` `Sum >= 1` / 900s / 1-of-1 counter alarm; the counter is keyed on the wrapped script's **process exit code** so many exit-0 aborts never page, and on-demand DAGs make it intermittent — distinct from *Alarm Provisioning Failures*) → [[airflow-dag-failure|Airflow DAG Failure]].

New ticket types are added here as their incidents are compiled — each as its own page with the alarm, the flow, and the automating skills/scripts.

## Reporting an oncall ticket

Deliver an oncall finding as a **detailed, table-structured report** — not a prose summary. This format is shared across every ticket type; each per-type page lists which tables apply. Use a table wherever the content is tabular:

1. **Alarm** — name, region, backing metric, threshold / evaluation (datapoints, period), state.
2. **Spike characterization** — baseline → onset → peak (vs threshold) → decay; the shape (sudden vs gradual, sustained vs blip).
3. **Driver breakdown** — the load broken down by the causal dimension (per-op / per-tenant / per-host) over the confirmed window, with each driver's share.
4. **Lineage** — root → target chain (per hop: the op, its queue/host, time, status), if the ticket traces to a root cause.
5. **Ownership / routing** — the responsible code (op → file → owner team/author) so the incident reaches the right team.
6. **Timeline** — the key timestamps in one place (all on one clock — UTC for CloudWatch/warehouse sources).

### Posting the report to Slack

When asked to post the report to Slack, treat it as an **outward-facing** action and follow three rules:

- **Draft both forms, then ask which to post.** Always prepare **both** a concise threaded reply **and** the full table-structured report (a Canvas), and ask the user which to post — **both** (Canvas + linking reply) or **reply-only**. **Lean reply-only for a small RCA** (a few-line transient blip): a full Canvas there is noise. Don't default to a Canvas.
- **Confirm the destination/surface before posting.** The post names people and customers; the channel/surface is usually unspecified — confirm it (together with the both-vs-reply-only choice) before guessing.
- **Render people/teams/customers as plain text, never @-mentions** — so the post does not page anyone.

The `oncall-post-report` skill encodes all three rules; **use it** to draft the reply + report, ask which to post, and post into the PagerDuty alert thread.

## Related skills

- `oncall-queue-backed-up` — the high-level runbook skill for the *Queue backed up* ticket type (each ticket type has one such per-type runbook skill).
- `oncall-solr-cpu-high` — the high-level runbook skill for the *Solr CPU too high* ticket type (characterize the CPU spike → split indexing vs query → break down the drivers → trace any processor source → route).
- `oncall-alarm-provisioning-failures` — the high-level runbook skill for the *Alarm Provisioning Failures* ticket type (characterize the failing-key count → enumerate the key via the `[Action Needed] Alarm` email → confirm the missing-config root cause with a plain config read → route to the owner).
- `oncall-rds-cpu-high` — the high-level runbook skill for the *RDS CPU too high* ticket type (pull the WRITER/READER CPU curves → split the DB load in Performance Insights → spot-check the actual SQL/query tags → trace to the producing op/code path → route).
- `oncall-redis-errors-detected` — the high-level runbook skill for the *Redis Error Detected* ticket type (pull the PD thread + per-namespace runbook → characterize the `redis-errors` metric + alarm history → run the runbook's Logs Insights query *expecting it may be empty* → route to Core Infra).
- `oncall-airflow-dag-failure` — the high-level runbook skill for the *Airflow DAG Failure* ticket type (pull the PD thread + Confluence runbook → characterize the `airflow-airflow.<dag>.failed.sum` metric + state history → trace the failure→exit-code→counter path → confirm the deploy via `build_log` → route via CODEOWNERS).
- `oncall-post-report` — use it to post the finished report back to the PagerDuty Slack thread; it drafts **both** a concise reply and the full report and asks which to post (lean reply-only for a small RCA), with a confirm-before-post gate and plain-text (non-paging) references. Applies to every ticket type.

## Related

- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — the metric-first correlation method underpinning every oncall.
- [[queue-backed-up|Queue backed up]] — the SQS queue-depth ticket type.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the host-CPU alarm family.
