---
name: oncall-queue-backed-up
description: High-level oncall runbook for a "Queue backed up" (SQS queue-depth) PagerDuty page. Use when you pick up a "[<region>] Queue backed up-<queue>" alarm and want the end-to-end investigation, not just one step — confirm and characterize the queue-depth spike, find which operation0/group flooded the queue, trace it to its root processor op, and route to the owning team. Sequences inspect-cloudwatch-metric → query-processor-event-log → trace-processor-op → codeowners-owner. Reach for this whenever an SQS queue-backed-up / queue-depth alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
---

# Oncall runbook — Queue backed up (SQS)

The high-level flow for a `[<region>] Queue backed up-<queue>` PagerDuty page. The domain facts — the metric-math alarm, the backing metric, the trailing-space `queue_name` gotcha, the table shapes to report — live in [[../../../wiki/oncall/queue-backed-up|Queue backed up]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which window to pull, which op to trace, who to route to), so read each step's output before the next.

## Execution flow

1. **Confirm & characterize the spike.** Pull the queue-depth alarm + metric and read the curve (sudden vs gradual, peak vs threshold) — **use the `inspect-cloudwatch-metric` skill** (`pull_queue_depth.py --queue <queue> --region <region> --start <ISO8601Z> --end <ISO8601Z>`). CloudWatch is UTC; establish the true spike window before correlating anything.
2. **Find what flooded the queue.** Over that window, break `message_dispatched` down by `operation0 × group_id` to find the outlier op/tenant — **use the `query-processor-event-log` skill** (`--queue <queue> --event-type message_dispatched --since <start> --until <end> --count-by operation0,group_id`). `queue_name` is matched trimmed (trailing-space gotcha).
3. **Trace to the root op.** Take a representative culprit SMID and walk `processor_parent_msg_id` to the parentless root — **use the `trace-processor-op` skill**. This is the root operation that fanned out the messages.
4. **Route to the owner.** Map the root (and culprit) `operation0` to its source file and resolve the owning team/author — **use the `codeowners-owner` skill** (op→file via [[../../../wiki/processor/op-registry|op_registry]], file→owner via [[../../../wiki/repo/codeowners-ownership|CODEOWNERS]], git-author fallback).
5. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric, threshold / evaluation (datapoints, period), state.
2. **Spike characterization** — baseline → onset → peak (vs the 50k threshold) → decay, and the shape (sudden ramp + drain vs gradual creep; sustained vs blip).
3. **Driver breakdown** — `operation0 × group_id` over the breach window, with each driver's share (narrow to the breach window so a burst separates from heavy baseline traffic for the same op).
4. **Op-lineage** — root → target chain, one row per hop: the op, its queue, time, and status.
5. **Ownership / routing** — op → source file → owning team/author, for the root and culprit ops.
6. **Timeline** — the key timestamps in one place, all on one clock (UTC for CloudWatch + warehouse `t_create`).

## Constituent skills (each independently usable)

- `inspect-cloudwatch-metric` — step 1, the queue-depth alarm + metric pull.
- `query-processor-event-log` — step 2, the op×group `message_dispatched` breakdown.
- `trace-processor-op` — step 3, the root-op walk.
- `codeowners-owner` — step 4, op→file→owner routing.
- `oncall-post-report` — step 5 (optional), post the finished report back to the PagerDuty Slack thread.
