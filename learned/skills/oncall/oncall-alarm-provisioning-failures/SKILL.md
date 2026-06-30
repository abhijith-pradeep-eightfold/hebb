---
name: oncall-alarm-provisioning-failures
model: sonnet
description: High-level oncall runbook for a "[<region>] [P2] Alarm Provisioning Failures" PagerDuty page — the daily alarm_manager_alerts DAG failing to provision one or more CloudWatch alarms. Use when you pick up an "Alarm Provisioning Failures" page and want the end-to-end investigation, not just one step — characterize the failing-key count (N datapoints = N independent failing alarm keys), enumerate the failing key via the "[Action Needed] Alarm" owner email (not CloudWatch Logs), read its traceback, confirm a missing-alarm_config-entry root cause with a plain config.get, and route to the owner. Sequences external-context-puller -> inspect-cloudwatch-metric -> config-get -> codeowners-owner -> oncall-post-report. Reach for this whenever an "Alarm Provisioning Failures" alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
  - "[[../../../wiki/infra/config-get|Reading a config value (config.get)]]"
---

# Oncall runbook — Alarm Provisioning Failures

The high-level flow for a `[<region>] [P2] Alarm Provisioning Failures` PagerDuty page. The domain facts — the `airflow-alarm_provisioning_failures.sum` `Sum >= 1` alarm, that the daily `alarm_manager_alerts` DAG bumps the counter **once per failing alarm key** (so **N datapoints = N independent failing keys = N independent config bugs**), the per-key `[Action Needed] Alarm` owner email, the witnessed missing-`alarm_config`-entry → unguarded-null-read root-cause shape, and the report tables — live in [[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (how many keys failed, which key, what its traceback means, whether the config entry is really missing, who to route to), so read each step's output before the next. Treat every failing key as its **own** bug — do not look for one shared cause behind a multi-datapoint firing.

## Execution flow

1. **Pull the page context.** Read the PagerDuty/Slack thread and the Confluence runbook for this alarm — **use the `external-context-puller` skill**. Note the **region** from the alarm name.
2. **Characterize the metric — how many keys failed?** Pull the alarm definition, the (sparse) metric series, and the alarm state history — **use the `inspect-cloudwatch-metric` skill**. The alarm is on a **custom airflow-namespace** metric (Namespace `airflow`, MetricName `airflow-alarm_provisioning_failures.sum`, Statistic `Sum`, `>= 1`, 3600s, 1/1 datapoint, `notBreaching`) with no EC2/SQS dimension — the skill's three entry commands (`describe-alarms`, `get-metric-statistics`, alarm-history) generalize directly with the namespace/metric swapped in. The datapoint value **is the count of failing keys**; the state history tells you chronic vs rare (this family is typically intermittent, firing in the daily-DAG window).
3. **Enumerate the failing key(s) — prefer the email path.** The runbook gives two paths; **start with the email**:
   - **(a) Gmail `[Action Needed] Alarm` notification** (preferred) — search for subject `[Action Needed] Alarm <key> provisioning is failing in region <region>` (the per-key email the DAG sends to the owner). It yields the **failing key AND the full traceback in one shot** — strictly more than the metric, which gives only the count. Pull it via the Gmail MCP.
   - **(b) DAG logs** (heavier fallback) — fetch the Airflow `alarm_manager_alerts` DAG logs and grep for `[Action Needed] Alarm`. This needs CloudWatch-Logs access + log-group enumeration and is only reachable in some environments; use it only if the email is unavailable.
4. **Diagnose each failing key from its traceback.** The email's traceback names the exact frames. Where it points at a **missing config entry** (the witnessed shape: `get_alarm_params_config` reads `config.get('alarm_config', field_name='<key>')` → `None` → unguarded `.get()` → `AttributeError: 'NoneType' object has no attribute 'get'`), **confirm the gap from the live source of truth** — **use the `config-get` skill** (`alarm_config --field-name <key>` → `None`, and/or `--has <key>` → key absent from the ~hundreds-of-keys dict). Config is **broadcast to all regions**, so read it plainly — no region override, no IAM handling.
5. **Route.** Each failing key routes to its `owner_emails`; when that defaulted to `core-infra@eightfold.ai` (no owner configured), resolve the alarm file's owner instead — **use the `codeowners-owner` skill** (file → CODEOWNERS owner, git-author fallback). The fix for a missing-config key is to add the `alarm_config.<key>` entry (one entry covers every region, since config is broadcast); the page follows the standing ack-and-wait pattern and auto-resolves after the next clean daily run.
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one. Do **not** post unless the user asks.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric (`airflow-alarm_provisioning_failures.sum`, Sum, `>= 1`, 3600s, 1/1), state, and **how chronic** (this trigger / prior trigger / gap).
2. **Failing-key list** — one row per failing key (= one datapoint): the key, its region, the `[Action Needed]` email recipient, and the exception/traceback head.
3. **Per-key root cause** — for each key, what the traceback shows; for a missing-config key, the live `config.get` confirmation (`None` / key absent).
4. **Ownership / routing** — key → `owner_emails`, or (when defaulted) the alarm-file CODEOWNERS owner / git author, plus the proposed fix (add the `alarm_config.<key>` entry).
5. **Timeline** — the key timestamps in one place, all UTC.

## Constituent skills (each independently usable)

- `external-context-puller` — step 1, pull the PagerDuty/Slack thread + the Confluence runbook.
- `inspect-cloudwatch-metric` — step 2, the alarm definition + (sparse) metric series + state history; generalizes to this custom airflow-namespace Sum metric.
- `config-get` — step 4, confirm the missing-`alarm_config`-entry root cause with a plain `config.get` (broadcast; no region override, no IAM handling).
- `codeowners-owner` — step 5, resolve the alarm file's owning team/author when routing falls to the default owner.
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
