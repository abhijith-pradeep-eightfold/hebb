# processor_event_log table

**Summary:** The per-message event log for the `www` processor (SQS-driven background ops). Each processor message emits rows here keyed by its **SMID** (`processor_msg_id`); rows carry the op(s) run, the parent message that dispatched it (`processor_parent_msg_id`), the event lifecycle, and the outcome. It is a data-warehouse table modelled by `ProcessorLogEvent`; its logical db_type is `REDSHIFT_LOG`, resolved per region to an actual warehouse (StarRocks/Redshift/Databricks) by the [[../data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]].

## The model

`class ProcessorLogEvent(BaseLogEvent)` — `www/db/base_log_event.py:181`. It is both the write-side event object (constructed and flushed by the processor workers) and the read-side schema/accessor for the warehouse table.

- **Stream / table name:** `get_streamname()` returns `'processor_event_log'` (`:205-206`).
- **Full (schema-qualified) table name:** `ProcessorLogEvent.get_full_table_name(db_type=None)` (`:208-213`) → `dwh.get_db_tablename_with_schema_prefix('processor_event_log', db_type=db_type)`. In a StarRocks region this resolves to `log.processor_event_log`.
- **Logical db_type → physical warehouse:** the `db_type` property (`:199-203`) is `dwh.get_db_type_override(DBType.REDSHIFT_LOG.value)`. `REDSHIFT_LOG` is a *logical* type; `get_db_type_override` maps it to whatever warehouse serves this region (observed: it resolved to **starrocks**, queried via the "Hodor" StarRocks client). This is the same "one logical table, many physical warehouses" routing the [[../data-warehouse/datawarehouse-adapter-factory|adapter factory]] performs — see also [[../data-warehouse/search-query-log|log.search_query_log]], another such table.
- Read it with `dwh.get_list(query, db_type=<resolved db_type>)` (`from cloud_interfaces import datawarehouse as dwh`). This is the **adapter-factory read path**, distinct from the `starrocks_utils.get_list` path documented in [[../data-warehouse/querying-starrocks|Querying StarRocks]] — use the model's own `db_type`/`get_full_table_name` so the query follows the region's warehouse routing rather than hardcoding StarRocks.

## Key columns

The authoritative column descriptions are the model's `get_column_description_for_capability_query_builder` dict (`www/db/base_log_event.py:215-235`).

| Column | Meaning |
|---|---|
| `processor_msg_id` | **The SMID** — *"A unique id for processor message from SQS"* (`:230`). A UUID; selective enough to query on by itself. |
| `processor_parent_msg_id` | *"processor_msg_id of the parent of the current message"* (`:231`) — the **edge to the parent op**; see [[tracing-processor-op-lineage|tracing processor-op lineage]]. |
| `operation0` | The op of the row — first op in the list; set as `operations[0] if operations else None` (`:186`). |
| `operations_list` | Comma-joined full op list (`:187`). |
| `event_type` | Lifecycle event. Model documents `message_dispatched` / `message_received` / `message_processed` (`:218`); live data also shows **`message_fetched`**. |
| `status` | Per-op outcome, populated on the `message_processed` row. Model documents `PASS` / `FAIL` (`:223`); live data also shows reroute markers such as **`REROUTE_TO_HIGH_MEM`** (see [[tracing-processor-op-lineage#the-high-mem-reroute|the high-mem reroute]]). |
| `group_id` | Customer/tenant id (e.g. `dcsg.com`). |
| `queue_name` | SQS queue the message ran on (e.g. `data_audit_requests`, `high_mem_no_retry_queue`). |
| `data_json` | Event payload; populated on `message_dispatched` / `message_received` (`:221`). |
| `request_trace_id` | *"A unique id used to track request across system"* (`:229`). |
| `latency_milliseconds` | Op latency; `message_processed` only (`:232`). |
| `memory_usage_bytes` | RSS at processing; `message_processed` only (`:225`). |
| `msg_retry_count` | SQS `ApproximateReceiveCount` (`:224`, set at `:191`). |
| `t_create` | Row event timestamp. |
| `system_id`, `cluster_type`, `git_revision`, `hostname` | system id; cluster (`spot`/`on_demand`/`canary`/dev) `:234`; git revision `:233`; machine IP `:228`. |

## Built-in accessor (and its limitation)

`ProcessorLogEvent.get_processor_event_logs(processor_msg_id=, processor_parent_msg_id=, entity_id=, t_create=, operation0=, limit=10)` (`:255-289`) builds and runs a query for you, but it **hard-filters on `group_id`** (`WHERE group_id = '{group_id}'`, `:267`) taken from the instance — and it time-boxes to ±1 day when given a `t_create`. So it is unusable from a **bare SMID** (no group_id, no time known). To trace an op from just a SMID, issue a direct `dwh.get_list` query filtered on `processor_msg_id` instead — see [[tracing-processor-op-lineage|tracing processor-op lineage]] and the `trace-processor-op` skill.

## Writer path

Worker code builds and flushes these rows: `www/processor/worker_utils.py:162-244` (`log_message_*` → `create_and_flush_processor_log_event`). The per-row object (including `processor_parent_msg_id`) is assembled in `www/processor/queue_utils.py:277-303` (parent set at `:295`). The in-memory op tracker holds the SMID as `OpInfo.smid` (`www/processor/op_monitor.py:30`, `:53` `smid=log_collector.get_sequence_message_id()`) — that is the same value persisted as `processor_msg_id`.

## Running scripts that read this table

Importing `db.base_log_event` and `cloud_interfaces.datawarehouse` requires `PYTHONPATH=$CODE_BASE/www` (these are `www`-rooted packages) — see [[../vscode-repo/python-import-root|Python import root]]. Running with `$CODE_BASE` alone fails with `ModuleNotFoundError: No module named 'db'`.

## Related skills

- `trace-processor-op` — use it to find the root processor op of a SMID and print the `processor_parent_msg_id` chain from target up to root.
- `query-processor-event-log` — use it for single filtered reads of this table (by `processor_msg_id`, `processor_parent_msg_id`, `group_id`, `operation0`, or a recent time window).

## Related

- [[tracing-processor-op-lineage|Tracing processor-op lineage]] — how to walk `processor_parent_msg_id` to the root op.
- [[../data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — resolves `REDSHIFT_LOG` to the region's physical warehouse.
- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — the `starrocks_utils` read path (this table uses the adapter-factory `dwh.get_list` path instead).
- [[../vscode-repo/python-import-root|Python import root]] — `PYTHONPATH=$CODE_BASE/www` for these `www`-rooted imports.

---
*Sources:* `www/db/base_log_event.py` (:181, :186-187, :199-213, :215-235, :255-289), `www/processor/worker_utils.py:162-244`, `www/processor/queue_utils.py:277-303`, `www/processor/op_monitor.py:30,:53`. Witness: `inputs/2026-06-26-smid-processor-trace.md`.
