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

## What to report

A root-cause summary carrying the four tables from [[../../../wiki/oncall/queue-backed-up|the wiki page]]: the spike shape vs threshold, the `operation0 × group_id` breakdown (with the outlier's share of the window), the root-op lineage, and the owning team/person to route to.

## Constituent skills (each independently usable)

- `inspect-cloudwatch-metric` — step 1, the queue-depth alarm + metric pull.
- `query-processor-event-log` — step 2, the op×group `message_dispatched` breakdown.
- `trace-processor-op` — step 3, the root-op walk.
- `codeowners-owner` — step 4, op→file→owner routing.
