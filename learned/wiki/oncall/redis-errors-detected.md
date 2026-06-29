# Redis Error Detected

**Summary:** The oncall ticket type for a `{priority}: Redis Errors Detected - {namespace} ({region})` PagerDuty page — a CloudWatch alarm on a per-namespace `redis-errors` **counter metric**, owned by **Core Infra**. The crux of this ticket type: the error **counter** (a CloudWatch *metric*) and the runbook's error **log line** (a CloudWatch *Logs* entry) are **independent sinks**, so the prescribed Logs Insights query can return **zero records** during a real metric spike. Characterize the incident from the **metric curve + alarm history**, not the log query; deeper root-cause needs **ElastiCache-side signals** the alarm cannot see.

## The alarm

| Field | Value |
|---|---|
| Namespace | the **namespace itself** (e.g. `ranking_service`) — *not* an `AWS/*` namespace |
| MetricName | `PROD_REDIS_ERROR_COUNTER_MAP[namespace]` — e.g. `ranking_service` → `prod-ranking-service-redis-errors.sum` |
| Statistic | **`Sum`** |
| Dimensions | **none** |
| Threshold | `> 100`, `GreaterThanThreshold` |
| Period | `300` s |
| EvaluationPeriods / DatapointsToAlarm | `2` / `2` (2-of-2) |
| TreatMissingData | `notBreaching` (errors are near-zero at baseline → no datapoints at all) |
| AlarmActions | SNS PagerDuty + `errors_volkscience_com` |
| AlarmDescription | links the generic **Core Infra Playbook - PagerDuty**, *not* the per-namespace runbook |

The alarm name is built by `RedisErrorsDetectedAlarms` (`alarm_prefix='Redis Errors Detected'`, `alarm_registry_key='redis_errors_detected'`, alarm Id `"{namespace}_redis_errors_detected"`) as `'{priority}: {prefix} - {namespace} ({region})'`. The **same alarm class fires for every namespace** in `PROD_REDIS_ERROR_COUNTER_MAP` (airflow, apiserver, parser, processor, www, ranking_service, …) — one entry per namespace, each its own metric and its own alarm. *Source:* `www/monitoring/alarms/redis_alarms.py:50-51` (the namespace→metric map), `:65-88` (`RedisErrorsDetectedAlarms`: prefix/registry-key, name format, `Stat="Sum"`, the metric pulled from the map).

So the page fires when the **sum of `redis-errors`** for the namespace exceeds 100 across **two** consecutive 5-min datapoints. Because `notBreaching` is set and the metric is near-zero at baseline, the metric has **no datapoints at all** outside an error burst.

## The crux: the counter and the log line are independent sinks

This is the structural finding that makes the prescribed runbook insufficient — read it before running the runbook's query.

- **The metric (counter).** The `redis-errors` counter is incremented by `counters.add('redis-errors', 1, counter_breakdowns=[<exc type>, cluster])` inside `_handle_error(connection, cluster)`. `counters.add` publishes to **CloudWatch *metrics*** (the `counters` module header: *"Module to collect counters and publish to cloudwatch"*). *Source:* `www/utils/redis_utils.py:81`; `www/utils/counters.py:1,76`.
- **The log line (what the runbook filters on).** The string `"Got error executing"` is emitted by a **separate** function, `_log_error` → `log.error('Got error executing {redis_cmd} key in redis cluster ({cluster}) …')` → **CloudWatch *Logs***. `redis_cmd` is upper-cased, so rendered lines read e.g. `Got error executing GET key in redis cluster (prod)`. *Source:* `www/utils/redis_utils.py:69-72`.
- **They are two pipelines.** The metric counter (botox → CloudWatch metrics) and the `log.error` line (→ CloudWatch Logs) are **decoupled**. Most op error paths call **both** `_log_error` and `_handle_error`, so normally the counter increment and the log line co-occur. But the two do **not** always travel together:
  - **`WritePipeline.__enter__` init failure** bumps the **counter without** the runbook's log line: it logs a *different* string, `log.exception("Got exception trying to init write pipeline")`, then calls `_handle_error` (which increments the counter). *Source:* `www/utils/redis_utils.py:320-329`.
  - **`WRONGTYPE`** errors are early-returned by `_handle_error` **before** the `counters.add` line, so a WRONGTYPE inflates **neither** sink (it is treated as a client error, not a server error). *Source:* `www/utils/redis_utils.py:78-79` (the early `return`) preceding the counter at `:81`.

**Consequence — the runbook's log query can legitimately return zero during a real spike.** The Feb-2024 Confluence runbook ("Redis Error Detected - {namespace}") prescribes a single CloudWatch **Logs Insights** query on the namespace's log group (mapping `ranking_service` → log group `RankingService`):

```
fields @timestamp, @message, @logStream, @log
| filter @message like "Got error executing" and @message like "redis"
| sort @timestamp desc
| limit 1000
```

Because the metric and the log line are independent sinks, this query can match **0 records** while the metric shows a genuine spike — confirmed in the worked example below (metric 360 at the breach bucket, runbook query = 0 records over the spike window). **Do not read "0 records" as "no incident."** The metric curve and the alarm history are what characterize this ticket type; the log query is a best-effort detail, not the evidence.

## The investigation flow

1. **Pull the PD thread + the runbook.** Read the PagerDuty Slack alert thread and the per-namespace Confluence runbook (use the `external-context-puller` skill). Note the alarm name carries the **region** and the **namespace** in parentheses. Treat the alarm's `AlarmDescription` link as the *generic* Core Infra playbook — the per-namespace runbook (with the Logs Insights query) is a different page.
2. **Read the alarm + characterize the metric.** Pull the alarm definition and the `redis-errors` (`Sum`, 300 s) timeseries over the incident window plus a baseline (use the `inspect-cloudwatch-metric` skill). Establish the spike shape — a Redis-errors blip is typically a **sharp single-bucket burst decaying to a 1–3/5min trickle within ~20 min, self-resolving in ~60 s**. CloudWatch is UTC. The metric is the primary evidence (see [[../process/incident-metric-correlation|incident metric-correlation discipline]]).
3. **Pull the alarm state-transition history → chronic vs rare.** Use `inspect-cloudwatch-metric`'s state-history mode. A chronic transient-blip class shows many `OK→ALARM→OK` episodes each lasting ~60–120 s (ack→auto-resolve), with no multi-minute sustained ALARM. That pattern → ack-and-close.
4. **(Optional) run the runbook's Logs Insights query — expecting it may be empty.** Map the namespace to its log group (`ranking_service` → `RankingService`) and run the verbatim query. **If it returns 0 records, that is consistent with the crux above, not a failure** — the WritePipeline-init path bumps the counter without the `"Got error executing"` line, and the metric counter is a different sink from the logs. Don't burn effort broadening the query to "find the missing lines"; the lines may genuinely not exist for this spike.
5. **Route, and bound the RCA.** Owner is **Core Infra**. A **deeper** root cause (was it a node failover, memory pressure, a cluster-down event?) needs **ElastiCache-side signals** — `FreeableMemory`, `EngineCPUUtilization`, node/replication events — a dimension **neither the log group nor the `redis-errors` counter captures**. For a recurring, self-resolving blip this deep-dive is usually **not warranted**; note the ElastiCache follow-up as the next step only if the class changes (sustained ALARM, rising frequency).
6. **Report, and post if asked.** Assemble the table-structured report ([[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]); when asked to post, confirm the surface and use plain-text references. For a small RCA like a transient blip, a **reply-only** post (no Canvas) is usually right — the `oncall-post-report` skill drafts both and asks.

## A note on identity / AWS access

A peer auto-triage bot may report its CloudWatch / Logs / ElastiCache reads as **AccessDenied** — that is its IAM user's permissions, not a fact about the incident. Confirm the agent box's own identity (`aws sts get-caller-identity`) before trusting "couldn't read the metric": the box may have full CloudWatch/Logs read in the region even when a sibling automation user does not. A direct metric/history read from the box that *can* read is authoritative over a peer's no-read RCA.

## What to report

Follow the shared [[oncall-investigation#reporting-an-oncall-ticket|table-structured report]] — but for this type the **metric + history** carry the finding, and the log breakdown is often empty:

1. **Alarm** — name, region, namespace, backing metric (`<namespace>` / `prod-…-redis-errors.sum`, `Sum`), threshold/evaluation (`> 100` / 2-of-2 / 300 s), state, and chronic-vs-rare from the history.
2. **Spike characterization** — baseline (near-zero) → onset → peak (vs 100) → decay; the shape (single-bucket burst vs sustained).
3. **Log evidence** — the runbook Logs Insights result, *including a 0-record result stated as expected* (with the counter↔log-line decoupling as the reason).
4. **Recurrence** — count of self-resolving ALARM episodes over the recent window from the alarm history.
5. **Ownership / routing** — Core Infra; the ElastiCache-signal follow-up named as the deeper-RCA path (only if warranted).
6. **Timeline** — key timestamps on the UTC clock.

## Worked example (this incident's shape)

A `P2: Redis Errors Detected - ranking_service (eu-central-1)` page (Core Infra) was a **recurring transient Redis-side blip, already self-resolved — ack and close**. The metric burst to ~360 errors in the 20:05 UTC 5-min bucket, decaying to 1–3/5min within ~20 min; the alarm went `ALARM→OK` in ~60 s. The alarm history showed **7 self-resolving episodes in ~26 days**, each ~60–120 s. The runbook's verbatim Logs Insights query matched **0 records** in the spike window (1.3M records scanned); the day's real `"Got error executing GET/MULTI_GET … in redis cluster"` lines (`redis_utils.py:72`) sat at 05:41–08:02 UTC, **unrelated** to the 20:05 spike — exactly the counter↔log-line decoupling above. No commits on the redis path in 14 days. Deeper RCA (ElastiCache signals) was declined as not warranted for this auto-resolving class.

## Related skills

- `oncall-redis-errors-detected` — the high-level runbook skill for this ticket type; sequences the PD-thread / Confluence-runbook pull → metric + history characterization → the (expect-may-be-empty) Logs Insights query → Core Infra routing → report.
- `inspect-cloudwatch-metric` — use it to pull the alarm definition, the `redis-errors` (`Sum`) metric curve, and the **alarm state-transition history** (chronic-vs-rare).
- `oncall-post-report` — use it to post the finished report back to the PagerDuty Slack thread; it drafts both a concise reply and a full report and asks which to post (lean reply-only for a small RCA), with the confirm-before-post + plain-text-references safety rules.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline + ticket-type catalog.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — the metric-first method; the 0-record log query is a textbook *non-correlation is itself a finding*.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — a different alarm family (host CPU); the shared CloudWatch-is-UTC and alarm-state-history mechanics.

---
*Sources:* witness `inputs/2026-06-29-redis-errors-ranking-service.md` (`[21:55]` the wiki gap — no Redis ticket-type page/skill; `[21:57]` the PD thread + Confluence runbook + the verbatim Logs Insights query; `[22:00]` the alarm definition + identity check; `[22:01]` the metric spike shape; `[22:02]` the runbook query returning 0 records; `[22:08]` the counter-vs-log-line code reading; `[22:10]` the 24h-vs-spike correlation + no-recent-commit; `[22:07]` the 7-episode alarm history). Code confirmed against live `www/utils/redis_utils.py:69-72,78-81,320-329`, `www/utils/counters.py:1,76`, `www/monitoring/alarms/redis_alarms.py:50-51,65-88`.
