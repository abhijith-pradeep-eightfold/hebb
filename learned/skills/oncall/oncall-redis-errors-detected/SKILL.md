---
name: oncall-redis-errors-detected
model: sonnet
description: High-level oncall runbook for a "Redis Errors Detected - <namespace>" PagerDuty page — a per-namespace `redis-errors` `Sum > 100` / 2-of-2 / 300s CloudWatch counter alarm, owned by Core Infra. Use when you pick up a "[<region>] [P?] Redis Errors Detected - <namespace> (<region>)" alarm and want the end-to-end investigation, not just one step — pull the PD thread + the per-namespace Confluence runbook, characterize the `redis-errors` metric burst + the alarm state history (chronic vs rare), run the runbook's Logs Insights query *expecting it may return zero* (the counter and the error log line are independent sinks), and route to Core Infra. Sequences external-context-puller -> inspect-cloudwatch-metric -> (optional Logs Insights) -> oncall-post-report. Reach for this whenever a Redis-errors / ElastiCache-errors counter alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/redis-errors-detected|Redis Error Detected (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
---

# Oncall runbook — Redis Error Detected

The high-level flow for a `{priority}: Redis Errors Detected - {namespace} ({region})` PagerDuty page. The domain facts — the per-namespace `redis-errors` `Sum > 100` / 2-of-2 / 300s counter alarm (`RedisErrorsDetectedAlarms`, `PROD_REDIS_ERROR_COUNTER_MAP`), the **counter↔log-line decoupling** that lets the runbook's Logs Insights query return zero on a real spike, the transient-blip shape, and the ElastiCache-signal RCA ceiling — live in [[../../../wiki/oncall/redis-errors-detected|Redis Error Detected]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (read the metric burst shape, decide whether the chronic history means ack-and-close, interpret a 0-record log query, decide whether an ElastiCache deep-dive is warranted), so read each step's output before the next.

> **The crux, before you run the runbook's query:** the `redis-errors` **counter** (`counters.add` → CloudWatch *metrics*) and the runbook's `"Got error executing"` **log line** (`_log_error` → CloudWatch *Logs*) are **independent sinks**. The Confluence runbook's verbatim Logs Insights query can match **0 records** during a genuine metric spike (the WritePipeline-init error path bumps the counter with a *different* log line; WRONGTYPE bumps neither). **Do not read "0 records" as "no incident," and do not burn effort broadening the query to find the missing lines** — the metric curve + the alarm history are the evidence. See [[../../../wiki/oncall/redis-errors-detected|Redis Error Detected]].

## Execution flow

1. **Pull the page context.** Read the PagerDuty/Slack alert thread and the **per-namespace** Confluence runbook ("Redis Error Detected - {namespace}") — **use the `external-context-puller` skill**. Note the **region** and the **namespace** from the alarm name (`… - <namespace> (<region>)`). The alarm's own `AlarmDescription` links the *generic* Core Infra playbook, not this per-namespace runbook — find the runbook by name. A peer auto-triage bot's RCA may be based on **AccessDenied** reads (its IAM user's permissions, not the incident); confirm your own box can read CloudWatch with `aws sts get-caller-identity` and trust a real read over the bot's no-read RCA.
2. **Confirm & characterize the spike.** Pull the alarm definition and the `redis-errors` counter curve (`Sum`, 300s) over the incident window + a baseline — **use the `inspect-cloudwatch-metric` skill** (counter mode: `pull_metric_sum.py --namespace <namespace> --metric-name prod-<svc>-redis-errors.sum --region <region> --start <ISO8601Z> --end <ISO8601Z>`, dimensionless, `Sum`, threshold 100). A Redis-errors blip is typically a **sharp single-bucket burst decaying to a 1–3/5min trickle within ~20 min**, self-resolving in ~60s. The metric has no datapoints at the near-zero baseline (`notBreaching`). CloudWatch is UTC.
3. **Chronic vs rare — alarm state history.** Pull the alarm's state-transition history — **use the `inspect-cloudwatch-metric` skill** (`pull_alarm_history.py --alarm-name "<exact alarm name>" --region <region>`). A chronic transient-blip class shows many `OK→ALARM→OK` episodes each ~60–120s. That pattern + a self-resolved current state ⇒ **ack-and-close**. (CloudWatch retains ~14 days of history; cross-check PagerDuty for older precedent.)
4. **(Optional) run the runbook's Logs Insights query — expecting it may be empty.** Map the namespace to its log group (`ranking_service` → `RankingService`) and run the runbook's verbatim query (`filter @message like "Got error executing" and @message like "redis"`) over the spike window. **A 0-record result is consistent with the counter↔log-line decoupling above, not a failure.** Don't broaden the query chasing the missing lines.
5. **Route, and bound the RCA.** Owner is **Core Infra**. A deeper root cause (node failover, memory pressure, cluster-down) needs **ElastiCache-side signals** — `FreeableMemory`, `EngineCPUUtilization`, node/replication events — a dimension neither the log group nor the counter captures. For a recurring self-resolving blip this deep-dive is usually **not warranted**; name it as the next step only if the class changes (sustained ALARM, rising frequency). Corroborate "no code regression" with `git -C "$CODE_BASE" log --since=<14d ago> -- www/utils/redis_utils.py www/monitoring/alarms/redis_alarms.py`.
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it drafts **both** a concise reply and the full report and asks which to post (lean **reply-only** for a small RCA like a transient blip), confirms the destination, and renders owner/customer names as plain text so the post pages no one. Do **not** post unless the user asks, and obtain the user's own approval of the wording before posting.

## What to report

Deliver a **detailed, table-structured report** following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For this type the **metric + history** carry the finding and the log breakdown is often empty:

1. **Alarm** — name, region, namespace, backing metric (`<namespace>` / `prod-…-redis-errors.sum`, `Sum`), threshold/evaluation (`> 100` / 2-of-2 / 300s), state, and chronic-vs-rare from the history.
2. **Spike characterization** — baseline (near-zero) → onset → peak (vs 100) → decay; the shape (single-bucket burst vs sustained).
3. **Log evidence** — the runbook Logs Insights result, *including a 0-record result stated as expected* with the counter↔log-line decoupling as the reason.
4. **Recurrence** — count of self-resolving ALARM episodes over the recent window.
5. **Ownership / routing** — Core Infra; the ElastiCache-signal follow-up named as the deeper-RCA path (only if warranted).
6. **Timeline** — key timestamps on the UTC clock.

## Constituent skills (each independently usable)

- `external-context-puller` — step 1, pull the PagerDuty/Slack thread + the per-namespace Confluence runbook.
- `inspect-cloudwatch-metric` — steps 2–3, the alarm definition + `redis-errors` counter curve (`pull_metric_sum.py`) + alarm state history (`pull_alarm_history.py`).
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
