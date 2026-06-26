---
name: query-processor-event-log
description: Read rows from the processor_event_log warehouse table by filter — processor_msg_id (SMID), processor_parent_msg_id, group_id, operation0, queue_name, event_type, and/or a time window (relative hours or absolute since/until) — or get a COUNT(*) breakdown grouped by columns (count-by) instead of raw rows. Use when a task asks to look at processor op events / SQS processor messages: "show the events for SMID <uuid>", "what ops ran for group X in the last 6h", "find children of this message", "did operation Y pass or fail", "list message_processed rows for tenant Z", or "which operation0/group flooded queue Z over this window" (count-by breakdown). For walking a SMID all the way to its ROOT op use trace-processor-op instead; for arbitrary StarRocks SQL use query-starrocks.
knowledge_required:
  - "[[../../../wiki/processor/processor-event-log|processor_event_log table]]"
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
---

# Query processor_event_log

Read rows from [[../../../wiki/processor/processor-event-log|processor_event_log]] by filter. This is the small, reusable "read processor op events" building block; the warehouse-routing and read logic live in the shared util `hebb_utils.processor.event_log` (also used by `trace-processor-op`), and a **bundled, read-only runner** wraps it. Because the runner is anchored under the skill dir, the bash execution policy (`core/tools/bash_exec_policy.py`) auto-allows it — it runs **without an approval prompt**.

## Steps

1. **Read the table page** (via `wiki-reader`): [[../../../wiki/processor/processor-event-log|processor_event_log]] — what the columns mean (`processor_msg_id` = SMID, `processor_parent_msg_id`, `operation0`/`operations_list`, `event_type`, `status`, `group_id`, `queue_name`), and that the table is reached via the model's `REDSHIFT_LOG`→region-warehouse routing (the script handles this).

2. **Run the bundled reader** with one or more filters:
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_processor_event_log.py" --group-id dcsg.com --operation data_audit --since-hours 6
   ```
   - **`PYTHONPATH="$CODE_BASE/www"`**, not `$CODE_BASE`: the util imports `db.base_log_event` and `cloud_interfaces.datawarehouse`, which are `www`-rooted — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
   - Filters (all optional, AND-combined; **at least one is required** so the scan stays bounded): `--msg-id`, `--parent-msg-id`, `--group-id`, `--operation`, `--queue` (matched on the **trimmed** `queue_name` — the stored value may carry a trailing space), `--event-type` (e.g. `message_dispatched`), `--since` / `--until` (absolute `t_create` bounds, `'YYYY-MM-DD[ HH:MM[:SS]]'`), `--since-hours N`, `--limit N` (default 200). `--format json` emits machine-readable rows.
   - **Aggregate (count) mode** — pass `--count-by <cols>` (comma-separated, from `operation0,group_id,queue_name,event_type,status,system_id`) to get a `COUNT(*)` breakdown grouped by those columns, ordered by count descending, instead of raw rows. This is the "what flooded a queue" breakdown — e.g. find the outlier op/tenant on a backed-up queue ([[../../../wiki/oncall/queue-backed-up|Queue backed up]]):
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "/home/ec2-user/hebb/.claude/skills/query-processor-event-log/scripts/query_processor_event_log.py" --queue ai_interview_op_queue --event-type message_dispatched --since "2026-06-25 12:00:00" --until "2026-06-26 04:00:00" --count-by operation0,group_id
   ```
   - Every interpolated value is charset-validated (UUID charset for ids; identifier charset for `group_id`/`operation0`; `--since-hours`/`--limit` are ints), and reads go through `dwh.get_list` — read-only by construction.

3. **Read the output.** Rows are newest-first. One message yields several rows (one per `event_type`: `message_dispatched`/`message_received`/`message_fetched`/`message_processed`); `status` (and `latency_milliseconds`, `memory_usage_bytes`) populate on `message_processed`.

## Notes

- **Use `trace-processor-op` instead** when you need to follow `processor_parent_msg_id` all the way to the **root** op of a SMID — that skill walks the chain; this one does single filtered reads.
- **Use `query-starrocks` instead** for arbitrary read-only SQL against StarRocks; this skill is specific to `processor_event_log` and its model-routed warehouse path.
