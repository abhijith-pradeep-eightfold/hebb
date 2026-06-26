---
name: trace-processor-op
description: Trace a processor SMID to its root op and print the op chain that led to it. Use when a task gives you a SMID (processor_msg_id) and asks for its root processor op, its parent/lineage, or the chain of ops via processor_event_log — e.g. "what's the root op of SMID <uuid>", "trace this processor message to its origin", "which op dispatched this message". Walks processor_parent_msg_id up to the parentless root via the data warehouse.
knowledge_required:
  - "[[../../../wiki/processor/processor-event-log|processor_event_log table]]"
  - "[[../../../wiki/processor/tracing-processor-op-lineage|Tracing processor-op lineage]]"
---

# Trace a processor op to its root

Given a **SMID** (`processor_msg_id`), find the **root processor op** and the op chain to reach it by walking the `processor_parent_msg_id` edge up the dispatch tree in [[../../../wiki/processor/processor-event-log|processor_event_log]] until a row has no parent. The domain facts (columns, the parent edge, the high-mem reroute shape, why the model's own `get_processor_event_logs` helper doesn't fit a bare SMID) live in the wiki — see [[../../../wiki/processor/tracing-processor-op-lineage|tracing processor-op lineage]]. The walk itself is deterministic, so it is a **bundled, read-only script**; because it is anchored under the skill dir, the bash execution policy (`core/tools/bash_exec_policy.py`) auto-allows it — it runs **without an approval prompt** every time (this is exactly the scratch-script approval that a bundled skill removes).

The reusable "read processor_event_log" logic (warehouse resolution, row fetch, the parent-chain walk) lives in the shared util `hebb_utils.processor.event_log`, so the sibling `query-processor-event-log` skill shares the same implementation; this script is a thin CLI over `walk_parent_chain`.

## Steps

1. **Read the grounding wiki pages** (via `wiki-reader`): [[../../../wiki/processor/processor-event-log|processor_event_log]] (what the columns mean, the `REDSHIFT_LOG`→region-warehouse routing) and [[../../../wiki/processor/tracing-processor-op-lineage|tracing processor-op lineage]] (the walk, the reroute shape). You usually don't need anything else — the script handles table/warehouse resolution.

2. **Run the bundled tracer** with the target SMID:
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/trace_processor_op.py" <smid>
   ```
   - **`PYTHONPATH="$CODE_BASE/www"`**, not `$CODE_BASE`: the script imports `db.base_log_event` and `cloud_interfaces.datawarehouse`, which are `www`-rooted — see [[../../../wiki/vscode-repo/python-import-root|Python import root]]. (`$CODE_BASE` alone fails with `ModuleNotFoundError: No module named 'db'`.)
   - The script resolves the table and physical warehouse from the model itself (`ProcessorLogEvent` → `DBType.REDSHIFT_LOG` → `dwh.get_db_type_override` → e.g. StarRocks `log.processor_event_log`) and reads via `dwh.get_list` — the [[../../../wiki/data-warehouse/datawarehouse-adapter-factory|adapter-factory]] read path, not `starrocks_utils`. Nothing is hardcoded per region.
   - It only issues `SELECT`s it builds itself and validates the SMID is UUID-charset before interpolating. Flags: `--max-depth N` (cycle/length cap, default 50), `--format json` (machine-readable hops).

3. **Read the output.** It prints each hop (target → root), then the **root op** (`operation0` of the parentless row) and the **root→target op trace**. A `NO ROW FOUND` note means the chain reached a SMID with no row in the table (e.g. aged out of the retained partitions).

4. **Interpret reroute shapes.** A same-op two-hop chain where the parent is on a normal queue with `status=REROUTE_TO_HIGH_MEM` and the child is on `high_mem_no_retry_queue` is a memory-breach **retry of the same op**, not op A calling op B — see [[../../../wiki/processor/tracing-processor-op-lineage#the-high-mem-reroute|the high-mem reroute]].

## Notes

- **Bare SMID only is enough** — `processor_msg_id` is a selective UUID, so no `group_id` or time window is needed. This is why the model's built-in `ProcessorLogEvent.get_processor_event_logs` (which hard-filters on `group_id`) is not used here; the script queries `processor_msg_id` directly.
- The query is slow against the warehouse (tens of seconds per hop via the StarRocks "Hodor" client) but chains are typically shallow.
- Read-only by construction: the script never executes arbitrary SQL and `dwh.get_list` is a read path. For **single filtered reads** of `processor_event_log` (by SMID, parent, group, op, or time window) rather than a full walk-to-root, use the `query-processor-event-log` skill; for general read-only warehouse SQL, use the `query-starrocks` skill.
