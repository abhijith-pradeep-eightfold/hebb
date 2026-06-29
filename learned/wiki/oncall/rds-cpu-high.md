# RDS CPU too high

**Summary:** The oncall ticket type for an `[<region>] P? RDS CPU Utilization Too High - for <cluster> - WRITER/READER - above N percent` PagerDuty page — an `AWS/RDS CPUUtilization` alarm on a cluster role's CPU, distinct from the EC2-host [[solr-cpu-high|Solr CPU]] and the [[queue-backed-up|queue-depth]] runbooks. The investigation: pull the alarm + the WRITER **and** READER CPU curves, then **RDS Performance Insights** to split the DB load (wait events + top SQL + by user/host) and find the driver, spot-check the actual SQL (whose query tags name the op/tenant/caller), trace it to the producing op/code path, and route to the owner.

## The alarm

| Field | Value |
|---|---|
| Namespace | `AWS/RDS` |
| MetricName | `CPUUtilization` |
| Statistic | **`ExtendedStatistic p75`** (not `Average`) |
| Dimensions | **`DBClusterIdentifier` + `Role`** (`WRITER` / `READER`) |
| Threshold | `90.0`, `GreaterThanOrEqualToThreshold` |
| Period | `60` s |
| EvaluationPeriods / DatapointsToAlarm | `8` / `8` (8 consecutive 1-min datapoints) |
| TreatMissingData | `notBreaching` |
| AlarmActions | SNS PagerDuty + `errors_volkscience_com` |

So the page fires when the **p75-across-the-minute CPU of the named role** is ≥ 90% for 8 consecutive minutes — onset + 8 min + ~1 min ≈ when it pages. The alarm dimension is the **cluster + role**, so a `WRITER` alarm tracks the writer instance's CPU.

Note this differs from the EC2 [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm]] in every parameter: namespace (`AWS/RDS` vs `AWS/EC2`), dimension (`DBClusterIdentifier`+`Role` vs `InstanceId`), statistic (`p75` vs `Average`), and threshold/evaluation (90% / 8-of-8 / 60 s vs 75% / 5-of-6 / 300 s). Many of these alarms are also in **GovCloud** (`us-gov-west-1`), a separate partition — see [[../infra/govcloud-access|GovCloud access]] for the credentials.

> **The alarm's own `AlarmDescription` may point at the wrong runbook.** In this incident the `AlarmDescription` linked a stale Confluence page; the correct EP runbook is **"RDS CPU Utilization Too High (AWS)"** (Confluence space EP, page `2656829683`). Do not trust the description's link blindly.

## The investigation flow

1. **Read the alarm + CPU curve for BOTH roles.** Pull the alarm definition and the `CPUUtilization` (p75 + Maximum) timeseries for **both `Role=WRITER` and `Role=READER`** over the incident window + a baseline. The role comparison is diagnostic:
   - **Both rise together** ⇒ cluster-wide load (affects readers and the writer); the writer runs hotter because it also serves the write/log-insert path.
   - **Writer-only rise** ⇒ a write-path-specific load.
   Establish the spike shape (sustained ramp vs one-minute blip) and pull the alarm **state history** to judge chronic vs rare. CloudWatch is UTC.
2. **Split the DB load in Performance Insights.** Resolve the role's instance `DbiResourceId` (`describe-db-instances`) and pull `db.load.avg` grouped by `db.wait_event`, `db.sql`, `db.user`, `db.host` over the spike window + baseline. Read **AAS against the instance vCPU count** (a `db.r5.large` = 2 vCPU, so AAS ≫ 2 is saturation). The dimension that dominates names the load type — see [[../infra/rds-performance-insights|RDS Performance Insights]]:
   - `wait/io/redo_log_flush` + `COMMIT`-dominated + single-row `INSERT`s, spread across the app fleet under the read-write user ⇒ a **commit/write storm** (the per-row commit rate is the driver, not query complexity).
   - CPU-bound execution + a heavy `SELECT` ⇒ the EP runbook's inefficient-query branch (Branch A) or volume branch (Branch B).
3. **Spot-check the actual SQL.** Fetch the full statement text for the top `db.sql` digests (`get-dimension-key-details`, `db.sql` group on aurora-mysql). The literal queries carry **query tags** in a SQL comment — `env=`, `op=`, `group_id=`, `processor_msg_id=`, `request_trace_id=`, `db_exp=<email>` — which name the source (the op, the tenant, the caller) **directly from the SQL**, so for a uniform write storm no warehouse `group_id` breakdown is needed. (The EP runbook's Branch-B alternative is to attribute volume via the `db_query_log` warehouse table + tags; prefer spot-checking the real SQL when the tags are uniform.)
4. **Trace to the producing code path + route.** Map the op/table named in the tags to its source (op → file via [[../processor/op-registry|op_registry]], table → model). Resolve **ownership** of that code via [[../repo/codeowners-ownership|CODEOWNERS]]. (A GovCloud warehouse lineage trace of the `processor_msg_id`s is **corroboration only** — and is gated by GovCloud reachability, see [[../infra/govcloud-access|GovCloud access]]; the root cause is already named by the query tags + the code path.)
5. **Report, and post if asked.** Assemble the table-structured report (the shared format on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]); when asked to post, confirm the surface and use plain-text references.

## Remediation

- **Immediate:** if CPU is stuck > 95% for > 15 min (or connection timeouts appear), **scale up the cluster** — add/upsize the writer (small classes like `db.r5.large` saturate fast on a write storm).
- **Durable (for a write storm):** **batch the per-row writes / reduce per-row commits** in the producing path — the COMMIT-per-row is the saturating cost (see [[../infra/rds-performance-insights|RDS Performance Insights]]). A benign per-call `ROLLBACK` rides alongside each commit (the SQLAlchemy pool reset-on-return) and is removed by the same batching, but it was never the cost.

## Worked example (this incident's shape)

A `WRITER` page on the `log` cluster turned out to be a **mass position cache-invalidation**: the processor `position_index` op invalidating deleted positions for one tenant, one committed single-row `INSERT INTO ats_entity_cache` per position (`expiry_reason=ef_entity_deleted`, `caller_id=invalidate_ats_entity`), dispatched by the bulk re-index CLI `re-index-db-positions.py`. PI showed 87% `wait/io/redo_log_flush`, ~89% `COMMIT`, fleet-wide. Owner: `dp-integrations`. See [[../ats/ats-entity-cache|ats_entity_cache write path]] for the full producer→SQL chain.

## What to report

Follow the shared [[oncall-investigation#reporting-an-oncall-ticket|table-structured report]]:

1. **Alarm** — name, region, backing metric (`AWS/RDS CPUUtilization`, p75), threshold/evaluation (90% / 8-of-8 / 60 s), state, and chronic-vs-rare from the history.
2. **Spike characterization** — baseline → onset → peak (vs 90%) → decay, **for both WRITER and READER** (so cluster-wide vs writer-only is visible).
3. **Load breakdown** — PI by wait event / SQL / user / host with each share, against the vCPU ceiling.
4. **Driver SQL** — the spiked statement skeleton + its query tags (op / tenant / caller).
5. **Ownership / routing** — table → model → owning team.
6. **Timeline** — key timestamps on the UTC clock.

## Related skills

- `oncall-rds-cpu-high` — the high-level runbook skill for this ticket type; sequences the metric pull → PI load-split → SQL spot-check → owner resolution → report.
- `inspect-cloudwatch-metric` — use it to pull the `AWS/RDS CPUUtilization` alarm + WRITER/READER p75 curve and tabulate breach buckets (GovCloud creds + gov region for a gov alarm).
- `query-rds-performance-insights` — use it to decompose `db.load.avg` by wait event / SQL / user / host and rank by AAS (finds the write-storm driver).
- `codeowners-owner` — use it to route the producing op/table to its owning team.
- `oncall-post-report` — use it to post the finished report back to the PagerDuty Slack thread (confirm-before-post, plain-text references).

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline + ticket-type catalog.
- [[../infra/rds-performance-insights|RDS Performance Insights]] — the load-split that is this ticket's analytical core.
- [[../infra/govcloud-access|GovCloud access]] — many RDS alarms are in `us-gov-west-1`; the GOV creds + the warehouse-trace hand-off.
- [[../ats/ats-entity-cache|ats_entity_cache write path]] — the table/op behind this incident's write storm.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the EC2-host CPU alarm family (different namespace/dimension/statistic).

---
*Sources:* witness `inputs/2026-06-29-rds-cpu-alarm-triage.md` (`[19:52]` the wiki gap; `[19:58]` the alarm definition + state history + the stale `AlarmDescription` link; `[20:02]` WRITER/READER curve characterization; `[20:08]` the EP runbook branches; `[20:14]`/`[20:32]` the PI load-split + SQL spot-check; `[20:18]`/`[20:59]`/`[21:52]` the owner + producer→SQL chain; the held-then-posted RCA at `[21:02]`–`[21:06]`).
