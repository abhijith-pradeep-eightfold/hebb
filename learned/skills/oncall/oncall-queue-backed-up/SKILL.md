---
name: oncall-queue-backed-up
model: sonnet
description: High-level oncall runbook for a "Queue backed up" (SQS queue-depth) PagerDuty page. Use when you pick up a "[<region>] Queue backed up-<queue>" alarm and want the end-to-end investigation, not just one step — confirm and characterize the queue-depth spike, find which operation0/group flooded the queue, trace it to its root processor op, and route to the owning team. Sequences inspect-cloudwatch-metric → query-processor-event-log → trace-processor-op → codeowners-owner. Reach for this whenever an SQS queue-backed-up / queue-depth alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
---

# Oncall runbook — Queue backed up (SQS)

The high-level flow for a `[<region>] Queue backed up-<queue>` PagerDuty page. The domain facts — the metric-math alarm, the backing metric, the trailing-space `queue_name` gotcha, the **inflow-vs-drain fork** (depth is a stock = ∫(inflow − drain)), and the table shapes to report — live in [[../../../wiki/oncall/queue-backed-up|Queue backed up]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which window to pull, which side moved, which op to trace, who to route to), so read each step's output before the next. Critically, **do not jump straight to "what op flooded it"** — a depth spike can be a drain dip with flat inflow, so fork first.

## Execution flow

1. **Confirm & characterize the spike.** Pull the queue-depth alarm + metric and read the curve (sudden vs gradual, peak vs threshold) — **use the `inspect-cloudwatch-metric` skill** (`pull_queue_depth.py --queue <queue> --region <region> --start <ISO8601Z> --end <ISO8601Z>`). CloudWatch is UTC; establish the true spike window before correlating anything.
2. **Fork — inflow surge or drain dip?** Depth is a backlog (∫(inflow − drain)), so first decide *which side moved*. Pull the queue's per-bucket inflow (`message_dispatched`) vs drain (`message_processed`) + net delta and overlay it on the depth curve from step 1 — **use the `query-queue-throughput` skill** (`--queue <queue> --mode rates --since <start> --until <end>`). Inflow rising with depth ⇒ **inflow branch** (step 3); inflow flat/falling while depth climbs ⇒ **drain branch** (step 4). (A storm's dispatch spike can land on the *upstream* queue, leaving this queue's inflow flat — that's still the drain branch here.)
3. **Inflow branch — what flooded the queue, then trace & route.** (a) Direct composition: break `message_dispatched` on the queue down by `operation0 × group_id` for the outlier op/tenant — **use the `query-processor-event-log` skill** (`--queue <queue> --event-type message_dispatched --since <start> --until <end> --count-by operation0,group_id`; `queue_name` matched trimmed). **But absolute count over a single window surfaces the highest-*baseline* tenant, not the one that spiked** — so (b) **separate spike from baseline**: run the comparative-window driver-lift (pre/spike/post, normalized per-hour, ranked by lift) — **use the `query-queue-throughput` skill** (`--mode drivers-lift --since <spike-start> --until <spike-end> --pre-since <baseline-start> [--post-until <post-end>]`); a spike-specific driver shows high `lift` with ~0 traffic before/after, while a flat `lift ≈ 1` is baseline noise even if its absolute volume tops the list. (c) Driver/parent attribution — rank the parent ops that *produced* the flood with the correct distinct-msg metric — **use the `query-queue-throughput` skill** (`--mode parents`, widen `--parent-since` earlier to catch scheduled/retry parents; a plain `message_dispatched` COUNT undercounts them). (d) Trace a representative **high-lift** driver SMID to its parentless root — **use the `trace-processor-op` skill** — then (e) route: map the root/culprit `operation0` to its file and owner — **use the `codeowners-owner` skill** (op→file via [[../../../wiki/processor/op-registry|op_registry]], file→owner via [[../../../wiki/repo/codeowners-ownership|CODEOWNERS]], git-author fallback).
4. **Drain branch — why consumers fell behind (inflow flat).** Work the drain-side causes: (a) **op errors/reroutes** — break `message_processed` by `operation0 × status` (**`query-processor-event-log` skill**, `--event-type message_processed --count-by operation0,status`); near-100% PASS rules errors out. (b) **per-message latency** — p50/p90 + `total_proc_sec`, by op and by tenant (**`query-queue-throughput` skill**, `--mode latency --by operation0` / `--by group_id`); a drain trough coinciding with a p90 spike is the throttle (`latency_milliseconds` is *processing* latency, not queue wait, so it's a real cause). (c) **worker-pool contention** — resolve the queue's pools + sibling queues (**`resolve-queue-worker-pool` skill**), then test each sibling's inbound (`query-queue-throughput --mode rates`); none spiking rules contention out. (d) **volume × latency** — rank tenants by `total_proc_sec` (worker-equiv = `total_proc_sec / window_seconds`); a small-count, very-slow tenant can saturate the pool. If latency points at indexing, confirm the Solr backend is hot with the `solr-shard-cpu` skill. Route to the owner of the slow op / backend (op→file via [[../../../wiki/processor/op-registry|op_registry]], file→owner via [[../../../wiki/repo/codeowners-ownership|CODEOWNERS]] — **use the `codeowners-owner` skill**).
5. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric, threshold / evaluation (datapoints, period), state.
2. **Spike characterization** — baseline → onset → peak (vs the 50k threshold) → decay, and the shape (sudden ramp + drain vs gradual creep; sustained vs blip).
3. **Inflow-vs-drain rate** — per-bucket `dispatched_in` vs `processed_out` (+ net delta) over the window ±2h: the fork evidence showing whether depth was inflow- or drain-driven.
4. **Driver breakdown** — `operation0 × group_id` over the breach window with each driver's share, **plus the comparative-window lift** (pre/spike/post normalized per-hour rates + `lift`) that distinguishes the spike-specific driver from high-baseline noise, **and** the distinct-parent attribution (which op produced the flood).
5. **Drain-branch tables** (when drain-side) — op errors (`operation0 × status`), latency by op/tenant (p50/p90 + `total_proc_sec`), worker-pool siblings + their inbound, and per-tenant volume × latency (worker-equivalents).
6. **Op-lineage** — root → target chain, one row per hop: the op, its queue, time, and status.
7. **Ownership / routing** — op → source file → owning team/author, for the root and culprit ops.
8. **Timeline** — the key timestamps in one place, all on one clock (UTC for CloudWatch + warehouse `t_create`).

## Constituent skills (each independently usable)

- `inspect-cloudwatch-metric` — step 1, the queue-depth alarm + metric pull.
- `query-queue-throughput` — step 2 (inflow-vs-drain fork), step 3 (parent attribution), step 4 (latency p50/p90 + `total_proc_sec`).
- `query-processor-event-log` — step 3 (direct `operation0 × group_id` composition) and step 4 (op-errors `operation0 × status`).
- `resolve-queue-worker-pool` — step 4, the queue's worker-pool groups + sibling queues (contention check).
- `trace-processor-op` — step 3, the root-op walk.
- `codeowners-owner` — steps 3/4, op→file→owner routing.
- `solr-shard-cpu` — step 4 (optional), confirm the indexing backend is hot when latency points at Solr.
- `oncall-post-report` — step 5 (optional), post the finished report back to the PagerDuty Slack thread.
