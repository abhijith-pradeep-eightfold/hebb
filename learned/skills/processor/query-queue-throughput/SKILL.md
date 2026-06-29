---
name: query-queue-throughput
model: sonnet
description: Time-bucketed throughput & drain diagnostics for one processor SQS queue from processor_event_log — inflow-vs-drain rate (the stock/flow fork), per-message latency (p50/p90 + total_proc_sec, by op or tenant), and the correct distinct-parent driver breakdown. Use when diagnosing a backed-up queue beyond "what's in it": "is this queue's depth an inflow surge or a drain dip", "plot dispatched vs processed per 15 min for <queue>", "which op/tenant's index latency spiked", "rank tenants by worker-seconds consumed", or "which op produced the messages that flooded <queue>" (distinct-parent attribution, not an event_type COUNT(*)). Pairs with inspect-cloudwatch-metric (overlay on the depth curve) and resolve-queue-worker-pool (test sibling inbound). For raw rows or simple COUNT(*) breakdowns use query-processor-event-log; for the root-op walk use trace-processor-op.
knowledge_required:
  - "[[../../../wiki/processor/processor-event-log|processor_event_log table]]"
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
---

# Query queue throughput (drain diagnostics)

Time-bucketed and breakdown aggregates of [[../../../wiki/processor/processor-event-log|processor_event_log]] for one queue — the **drain-side** workhorse for a [[../../../wiki/oncall/queue-backed-up|backed-up queue]] (queue depth is a stock = ∫(inflow − drain), so you must look at *both* sides, not just what is in the queue). The aggregate SQL lives in the shared util `hebb_utils.processor.event_log` (also used by `query-processor-event-log` and `trace-processor-op`); a **bundled, read-only runner** wraps it and is auto-allowed by the bash execution policy — no approval prompt.

## Steps

1. **Read the table page** (via `wiki-reader`): [[../../../wiki/processor/processor-event-log|processor_event_log]] — especially [[../../../wiki/processor/processor-event-log#latency_milliseconds|`latency_milliseconds`]] (op *processing* latency, not queue wait; use `percentile_approx`, not `MAX`, for its multi-million-ms tail; `total_proc_sec` = volume × latency).

2. **Run the bundled runner** in the mode that answers the question (PYTHONPATH must root at `www/`):
   - **`--mode rates`** — per-bucket inflow vs drain + net delta (the inflow-vs-drain fork). Overlay it on the CloudWatch depth curve (`inspect-cloudwatch-metric`); flat inflow with drain-driven net delta ⇒ drain branch.
     ```bash
     PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_queue_throughput.py" --queue index_requests --mode rates --since "2026-06-23 13:00:00" --until "2026-06-23 21:00:00" --bucket-minutes 15
     ```
   - **`--mode latency`** — p50/p90 latency + `total_proc_sec` per bucket and/or per `--by operation0,group_id` (optionally filtered to one `--operation`/`--group-id`). Worker-equivalents = `total_proc_sec / window_seconds`; rank tenants by `total_proc_sec` to find capacity hogs a raw count hides.
     ```bash
     PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_queue_throughput.py" --queue index_requests --mode latency --by operation0 --since "2026-06-23 15:00:00" --until "2026-06-23 18:30:00"
     ```
   - **`--mode parents`** — the **driver** breakdown: which parent ops produced the queue's messages, counted as `COUNT(DISTINCT processor_msg_id)` with **no event_type filter on the outer** (the correct metric — filtering on `message_dispatched` undercounts scheduled/retry parents whose dispatch row lands outside the window). Widen `--parent-since` earlier than `--since` to catch delayed parents.
     ```bash
     PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_queue_throughput.py" --queue index_requests --mode parents --since "2026-06-23 15:00:00" --until "2026-06-23 18:30:00" --parent-since "2026-06-23 13:00:00" --parent-until "2026-06-23 19:10:00"
     ```
   `--format json` emits machine-readable rows (the `rates`/`latency` timeseries feed the `plot-result-set` skill directly).

3. **Read the output.** `rates`: backlog-building buckets have `net_delta > 0`, draining buckets `< 0` — the sign flip locates onset/decay. `latency`: a drain trough coinciding with a p90 spike is the throttle; the top `total_proc_sec` op/tenant is the capacity hog. `parents`: the top `distinct_msgs` op is the true driver to trace (`trace-processor-op`) and route (`codeowners-owner`).

## Notes

- **Use `query-processor-event-log` instead** for raw filtered rows or a simple `COUNT(*)` breakdown (e.g. direct queue composition `--count-by operation0,group_id`, or drain-branch op-errors `--event-type message_processed --count-by operation0,status`). This skill is for the time-bucketed / percentile / distinct-parent aggregates that `--count-by` cannot express.
- **Use `trace-processor-op`** to walk a driver SMID to its root op after `--mode parents` names the driver.
- All reads go through `dwh.get_list`; every interpolated value (queue, op, group, timestamps, bucket size, limit) is charset/format-validated — read-only by construction.
