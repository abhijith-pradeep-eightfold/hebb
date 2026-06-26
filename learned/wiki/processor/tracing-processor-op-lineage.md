# Tracing processor-op lineage

**Summary:** How to find the **root processor op** behind a SMID and reconstruct the op chain that led to it. Processor messages form a parent→child tree: each child row in [[processor-event-log|processor_event_log]] records the message that dispatched it in `processor_parent_msg_id`. Starting from a target `processor_msg_id`, follow that edge upward until a row has no parent — that terminal row is the root, and its `operation0` is the root op.

## The dispatch mechanism (why the edge exists)

When a processor op dispatches a child message, the child's payload carries `_parent_msg_id` set to the **current** message id (`www/processor/queue_utils.py` ~`:650`). When that child later logs to the warehouse, the value is persisted as the row's `processor_parent_msg_id` (assembled in `queue_utils.py:277-303`, parent set at `:295`). So the table encodes the full dispatch tree. The code names this traversal explicitly — `queue_utils.py:691` refers to *"traverse the parent message id chain."*

## The walk

Given a target SMID:

1. Query [[processor-event-log|processor_event_log]] for all rows with `processor_msg_id = <smid>` (one message emits several rows — one per `event_type`). Use the **adapter-factory read path**: resolve the table and db_type from the model, then `dwh.get_list(query, db_type=...)` (see [[processor-event-log|the table page]] for why this path, not `starrocks_utils`).
2. Read `processor_parent_msg_id` from any of those rows (it is the same across a message's rows).
3. If it is empty/null → **this is the root**; its `operation0` is the root op. Stop.
4. Otherwise set the SMID to the parent and repeat from step 1.

The op chain is the sequence of `operation0` values from root down to the target. A bare SMID is enough — `processor_msg_id` is a selective UUID, so no `group_id` or time-box is needed (which is exactly why the model's own `get_processor_event_logs` helper does **not** fit here: it hard-filters on `group_id` — see [[processor-event-log#built-in-accessor-and-its-limitation|the accessor limitation]]).

Guard the walk against cycles (track visited SMIDs) and cap depth; in practice chains are shallow.

## The high-mem reroute

A common chain shape is a **same-op two-hop chain** produced by a memory-breach reroute, not by a genuine parent/child op handoff. When an op's RSS breaches the limit, the processor marks the message in Redis to be re-dispatched onto `high_mem_no_retry_queue` (`www/processor/op_monitor.py:128-131`). The original row is then `message_processed` with `status = REROUTE_TO_HIGH_MEM`, and the rerouted message runs the **same** `operation0` again on `high_mem_no_retry_queue` as a child. So seeing `op[parent] → op[child]` with identical ops, the parent on a normal queue with `status=REROUTE_TO_HIGH_MEM` and the child on `high_mem_no_retry_queue`, means "this op was retried with more memory," not "op A called op B."

*Worked example (witness `inputs/2026-06-26-smid-processor-trace.md`):* target `data_audit` on `high_mem_no_retry_queue` → parent `data_audit` on `data_audit_requests` with `status=REROUTE_TO_HIGH_MEM`, parent null. Root op = `data_audit`; the chain is a single high-mem reroute.

## Related skills

- `trace-processor-op` — use it to do this end-to-end from a SMID: it resolves the table/db_type from the model, walks the `processor_parent_msg_id` chain to the root, and prints each hop plus the root op and the root→target op trace.

## Related

- [[processor-event-log|processor_event_log table]] — the columns this walk reads (`processor_msg_id`, `processor_parent_msg_id`, `operation0`, `status`, `queue_name`).
- [[../data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — why the read goes through the model's resolved db_type.
- [[../vscode-repo/python-import-root|Python import root]] — `PYTHONPATH=$CODE_BASE/www` to import the model.

---
*Sources:* `www/processor/queue_utils.py` (~:650, :277-303, :295, :691), `www/db/base_log_event.py:231` (the `processor_parent_msg_id` edge), `www/processor/op_monitor.py:128-131` (high-mem reroute). Witness: `inputs/2026-06-26-smid-processor-trace.md`.
