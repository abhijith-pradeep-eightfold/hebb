---
name: oncall-rds-cpu-high
model: sonnet
description: High-level oncall runbook for an "RDS CPU Utilization Too High" PagerDuty page — an `AWS/RDS CPUUtilization` p75 alarm on a cluster's WRITER or READER (often in GovCloud). Use when you pick up a "[<region>] P? RDS CPU Utilization Too High - for <cluster> - WRITER/READER - above N percent" alarm and want the end-to-end investigation, not just one step — confirm the WRITER and READER CPU curves, split the DB load in Performance Insights (wait events + top SQL + by host) to find the driver, spot-check the actual SQL (whose query tags name the op/tenant/caller), trace it to the producing op/code path, and route to the owner. Sequences external-context-puller -> inspect-cloudwatch-metric -> query-rds-performance-insights -> codeowners-owner -> oncall-post-report. Reach for this whenever an RDS / database CPU alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/rds-cpu-high|RDS CPU too high (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
  - "[[../../../wiki/infra/rds-performance-insights|RDS Performance Insights]]"
  - "[[../../../wiki/infra/govcloud-access|GovCloud access]]"
  - "[[../../../wiki/ats/ats-entity-cache|ats_entity_cache write path]]"
---

# Oncall runbook — RDS CPU too high

The high-level flow for an `[<region>] P? RDS CPU Utilization Too High - for <cluster> - WRITER/READER - above N percent` PagerDuty page. The domain facts — the `AWS/RDS CPUUtilization` p75 ≥90% / 8-of-8 / 60s alarm on `DBClusterIdentifier`+`Role`, the WRITER/READER comparison, the Performance Insights load-split and the **commit / redo-log-flush write-storm** signature, the query-tag attribution, and the report tables — live in [[../../../wiki/oncall/rds-cpu-high|RDS CPU too high]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which role rose, which wait-event/SQL dominates, whether to spot-check SQL vs run a warehouse breakdown, who to route to), so read each step's output before the next.

Many of these alarms are in **GovCloud** (`us-gov-west-1`) — a separate AWS partition needing the `GOV_AWS_*` creds. Set them before any AWS read for a gov alarm; see [[../../../wiki/infra/govcloud-access|GovCloud access]]:
```bash
export AWS_ACCESS_KEY_ID="$GOV_AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$GOV_AWS_SECRET_ACCESS_KEY"
```

## Execution flow

1. **Pull the page context.** Read the PagerDuty/Slack thread and the Confluence runbook for this alarm — **use the `external-context-puller` skill**. Note the **region** and the **cluster + role** from the alarm name. The alarm's own `AlarmDescription` link may be stale — the correct EP runbook is "RDS CPU Utilization Too High (AWS)".
2. **Confirm & characterize the spike — BOTH roles.** Pull the alarm definition and the `CPUUtilization` (p75 + Maximum) curve for **both `Role=WRITER` and `Role=READER`** over the incident window + a baseline, and the alarm state history (chronic vs rare) — **use the `inspect-cloudwatch-metric` skill** (RDS mode: `pull_rds_cpu.py --cluster <cluster> --region <region> --start <ISO8601Z> --end <ISO8601Z>`, which pulls both roles by default). **Both roles rising together** ⇒ cluster-wide load; **writer-only** ⇒ a write-path load. Establish the true breach window (CloudWatch is UTC) before correlating anything.
3. **Split the DB load — find the driver.** Resolve the role's instance `DbiResourceId` (`describe-db-instances`) and decompose `db.load.avg` by `db.wait_event` / `db.sql` / `db.user` / `db.host` over the spike window — **use the `query-rds-performance-insights` skill**. Read AAS against the instance vCPU count (a `db.r5.large` = 2 vCPU). `wait/io/redo_log_flush` + `COMMIT`-dominant + single-row `INSERT`s spread across the fleet ⇒ a **commit/write storm**; a heavy `SELECT` + CPU wait ⇒ an inefficient/high-volume query.
4. **Spot-check the actual SQL.** Fetch the full statement text for the top `db.sql` digests — **use the `query-rds-performance-insights` skill** (`--sql-id <db.sql.id>`). The literal SQL carries **query tags** (`env=`, `op=`, `group_id=`, `processor_msg_id=`, `db_exp=<email>`) that name the source — the op, the tenant, the caller — directly from the SQL, so for a uniform write storm no warehouse `group_id` breakdown is needed. (The EP runbook's Branch-B alternative attributes volume via the `db_query_log` warehouse table; prefer the SQL spot-check when the tags are uniform — and the user may explicitly decline the warehouse breakdown.)
5. **Trace & route.** Map the op/table named in the tags to its source (op → file via [[../../../wiki/processor/op-registry|op_registry]], table → model) and resolve **ownership** — **use the `codeowners-owner` skill**. (A GovCloud warehouse lineage trace of the `processor_msg_id`s is **corroboration only** and is gated by GovCloud reachability — the gov warehouse is not reachable from the agent box; see [[../../../wiki/infra/govcloud-access|GovCloud access]]. The root cause is already named by the query tags + code path, so do not block on the trace.)
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one. Do **not** post unless the user asks.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric (`AWS/RDS CPUUtilization`, p75), threshold/evaluation (90% / 8-of-8 / 60s), state, and **how chronic** (this trigger / prior trigger / gap).
2. **Spike characterization** — baseline → onset → peak (vs 90%) → decay, **for both WRITER and READER**, so cluster-wide vs writer-only is visible.
3. **Load breakdown** — PI by wait event / SQL / user / host with each share, against the vCPU ceiling.
4. **Driver SQL** — the spiked statement skeleton + its query tags (op / tenant / caller).
5. **Ownership / routing** — table → model → owning team, plus the immediate (scale up the writer) and durable (batch the per-row commits) fixes.
6. **Timeline** — the key timestamps in one place, all UTC.

## Constituent skills (each independently usable)

- `external-context-puller` — step 1, pull the PagerDuty/Slack thread + the Confluence runbook.
- `inspect-cloudwatch-metric` — step 2, the RDS alarm definition + WRITER/READER p75 curve + state history (RDS mode).
- `query-rds-performance-insights` — steps 3–4, the PI load split (wait event / SQL / host) + the full-SQL spot-check.
- `codeowners-owner` — step 5, resolve the producing op/table's owning team.
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
