---
name: resolve-queue-worker-pool
model: sonnet
description: Resolve which processor worker-pool (queue-group) drains a given SQS queue, and the sibling queues that share those pools, for a region. Use when a task needs to know a processor queue's drain capacity or whether it contends with neighbours — "which worker pool drains index_requests", "what queues share a pool with <queue>", "is <queue> on a dedicated pool", "find the queue-group siblings of <queue>", or the drain-side noisy-neighbour check of a "Queue backed up" oncall (does a pool-sibling's inbound spike explain the drain dip). Reads the live region-scoped processor_worker_<instance_type>_ecs_config via ecs_scaling_utils. Pair the sibling list with query-queue-throughput to test each sibling's inbound.
knowledge_required:
  - "[[../../../wiki/processor/queue-worker-pool-segregation|Processor worker-pool / queue-group segregation]]"
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
---

# Resolve a queue's worker pool + siblings

The `www` processor segregates drain capacity into named **queue groups** (worker pools), each draining a fixed set of queues with its own `max_count` / `scale_out_pending_messages_per_worker`. A queue's drain rate is bounded by the pools it belongs to and contended by its **sibling queues**. This skill resolves both from the live, region-scoped runtime config — see [[../../../wiki/processor/queue-worker-pool-segregation|Processor worker-pool / queue-group segregation]]. The lookup logic lives in the shared util `hebb_utils.processor.worker_pools`; a **bundled, read-only runner** wraps it and is auto-allowed by the bash execution policy (`core/tools/bash_exec_policy.py`) — no approval prompt.

## Steps

1. **Read the segregation page** (via `wiki-reader`): [[../../../wiki/processor/queue-worker-pool-segregation|queue-worker-pool segregation]] — the config is `processor_worker_<instance_type>_ecs_config` → `worker_config: {queue_group: {queues, max_count, scale_out_pending_messages_per_worker}}`, read per region via `ecs_scaling_utils`; a queue usually sits in several groups (a dedicated pool + `everything_else` + `unallocated`).

2. **Run the bundled resolver** for the queue + region:
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/resolve_queue_worker_pool.py" --queue index_requests --region us-west-2
   ```
   - **`PYTHONPATH="$CODE_BASE/www"`**, not `$CODE_BASE`: the util imports `processor.ecs_scaling_utils` (www-rooted) — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
   - `--queue` (required) is the SQS `queue_name`. `--region` defaults to `EF_DEFAULT_REGION`; **pass the incident's region** — the mapping is region-scoped. `--format json` emits machine-readable pools + siblings.

3. **Read the output.** Each `=== [<instance_type>] <queue_group> (max_count=…, scale_out=…) ===` block is one pool the queue drains on, with its sibling queues; the final line is the union of all siblings. A queue alone in a high-`max_count` pool is **not** automatically uncontended — check that pool's other siblings and the `everything_else` pool it also rides.

## Notes

- The config returned is the **current** value; queue-group layout changes over time, so it is not guaranteed identical to a past incident's layout (caveat it when reasoning historically).
- For Azure regions the config partition can differ (an `-aws` suffix for AWS-SQS workers); this resolver targets the region you pass directly (validated for AWS regions like us-west-2).
- **Noisy-neighbour check:** feed the sibling list to the `query-queue-throughput` skill — compare each sibling's inbound (`message_dispatched`) rate baseline-vs-breach; a sibling whose inbound spiked in-window is a contention suspect, none spiking rules shared-pool contention out.
