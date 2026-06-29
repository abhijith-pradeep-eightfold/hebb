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
| `operation0` | The op of the row — first op in the list; set as `operations[0] if operations else None` (`:186`). Resolve the op name to its source file via [[op-registry]]. |
| `operations_list` | Comma-joined full op list (`:187`). |
| `event_type` | Lifecycle event. Model documents `message_dispatched` / `message_received` / `message_processed` (`:218`); live data also shows **`message_fetched`**. |
| `status` | Per-op outcome, populated on the `message_processed` row. Model documents `PASS` / `FAIL` (`:223`); live data also shows reroute markers such as **`REROUTE_TO_HIGH_MEM`** (see [[tracing-processor-op-lineage#the-high-mem-reroute|the high-mem reroute]]). |
| `group_id` | Customer/tenant id (e.g. `dcsg.com`). |
| `queue_name` | SQS queue the message ran on (e.g. `data_audit_requests`, `high_mem_no_retry_queue`). |
| `data_json` | Event payload; populated on `message_dispatched` / `message_received` (`:221`). Carries the business event + dispatch provenance — see [[#the-data_json-payload|The data_json payload]] below. |
| `entity_id` | The entity (e.g. profile id) the op acted on; usable as a `get_processor_event_logs` filter and a per-entity `GROUP BY` key. |
| `request_trace_id` | *"A unique id used to track request across system"* (`:229`). |
| `latency_milliseconds` | **Op *processing* latency** (dequeue→done), `message_processed` only (`:232`) — *not* queue wait. See [[#latency_milliseconds|latency_milliseconds]] below. |
| `memory_usage_bytes` | RSS at processing; `message_processed` only (`:225`). |
| `msg_retry_count` | SQS `ApproximateReceiveCount` (`:224`, set at `:191`). Live data shows **`-1` as a framework sentinel** (not a real redelivery), so filter it out when counting genuine retries. |
| `t_create` | Row event timestamp, stored in **UTC** (like `search_query_log.t_create` and the other `log.*` tables). |
| `system_id`, `cluster_type`, `git_revision`, `hostname` | system id; cluster (`spot`/`on_demand`/`canary`/dev) `:234`; git revision `:233`; machine IP `:228`. |

### latency_milliseconds

`latency_milliseconds` is the **op's processing time — dequeue→done — not the time the message waited in the queue.** This matters when diagnosing a backed-up queue: a rising `latency_milliseconds` is a genuine *cause* of reduced drain throughput, not the backlog re-expressed as wait time (which would be a circular conclusion).

- `process_message(...)` records `msg_start_time = time.time()` *after* the message is received/dequeued (`www/processor/worker.py:662-664`).
- The `message_processed` row is logged with that start (`www/processor/worker.py:944` → `worker_utils.log_message_processed(..., msg_start_time)` → `www/processor/worker_utils.py:242` `latency_seconds = time.time() - op_start_time` → `www/processor/queue_utils.py:293` `latency_milliseconds = int(latency_seconds * 1000)`).
- **Queue wait is a separate field:** `lag_seconds = int(time.time() - _message_dispatched_ts)` (dispatch→now) at `www/processor/queue_utils.py:300`, and `time_from_dispatched_to_received` at `www/processor/worker.py:669`.
- **Aggregation gotcha:** `latency_milliseconds` (and its `MAX`) has a pathological **multi-million-ms tail**, so a raw `AVG`/`MAX` is misleading. Use **`percentile_approx(latency_milliseconds, 0.5)` / `0.9`** as the signal. To rank what *consumed worker capacity*, use **`total_proc_sec = SUM(latency_milliseconds)/1000`** (volume × per-message latency) and **worker-equivalents = total_proc_sec / window_seconds** — a small-count, very-slow tenant can dominate a pool that a raw count hides.

### The data_json payload

`data_json` (populated on `message_dispatched`/`message_received`) is the full message payload. Reading one real payload beats reasoning about the schema — it carries both the business event and the dispatch provenance. Extract fields in StarRocks with `get_json_string(data_json, '$.path')` / `get_json_int(...)`:

| JSON path | Meaning |
|---|---|
| `$.event_type` | The **business** event (`profile_data_changed`, `application_update`, `candidate_profile_updated`, …) — **distinct from the row-level `event_type` column** (which is the lifecycle event `message_dispatched`/`message_processed`/…). Break a storm down by this to see *what kind* of event flooded a queue. |
| `$._parent_op` | The parent op name embedded in the payload (a payload-level complement to the `processor_parent_msg_id` edge). |
| `$._traceback` | The publish call stack — names the exact code path that enqueued the message (e.g. an interceptor `post_save`). Use it to find a seeding mechanism that a source grep misses; see [[trigger-event-fanout|trigger_event fan-out]]. |
| `$._interceptor_stack` | The interceptor context (e.g. `["prod:profile_data:None"]`). |
| `$.event_context...` | Op-specific context, e.g. `update_spec[0].retry_count` (the per-chain retry counter for the [[trigger-event-fanout|write_back retry loop]]). |

## Built-in accessor (and its limitation)

`ProcessorLogEvent.get_processor_event_logs(processor_msg_id=, processor_parent_msg_id=, entity_id=, t_create=, operation0=, limit=10)` (`:255-289`) builds and runs a query for you, but it **hard-filters on `group_id`** (`WHERE group_id = '{group_id}'`, `:267`) taken from the instance — and it time-boxes to ±1 day when given a `t_create`. So it is unusable from a **bare SMID** (no group_id, no time known). To trace an op from just a SMID, issue a direct `dwh.get_list` query filtered on `processor_msg_id` instead — see [[tracing-processor-op-lineage|tracing processor-op lineage]] and the `trace-processor-op` skill.

## Writer path

Worker code builds and flushes these rows: `www/processor/worker_utils.py:162-244` (`log_message_*` → `create_and_flush_processor_log_event`). The per-row object (including `processor_parent_msg_id`) is assembled in `www/processor/queue_utils.py:277-303` (parent set at `:295`). The in-memory op tracker holds the SMID as `OpInfo.smid` (`www/processor/op_monitor.py:30`, `:53` `smid=log_collector.get_sequence_message_id()`) — that is the same value persisted as `processor_msg_id`.

## Running scripts that read this table

Importing `db.base_log_event` and `cloud_interfaces.datawarehouse` requires `PYTHONPATH=$CODE_BASE/www` (these are `www`-rooted packages) — see [[../vscode-repo/python-import-root|Python import root]]. Running with `$CODE_BASE` alone fails with `ModuleNotFoundError: No module named 'db'`.

## Related skills

- `trace-processor-op` — use it to find the root processor op of a SMID and print the `processor_parent_msg_id` chain from target up to root.
- `query-processor-event-log` — use it for single filtered reads of this table (by `processor_msg_id`, `processor_parent_msg_id`, `group_id`, `operation0`, or a recent time window) and `COUNT(*)` breakdowns.
- `query-queue-throughput` — use it for time-bucketed aggregates of this table for a queue: inflow-vs-drain rate, `percentile_approx`/`total_proc_sec` latency (by op/group), and the distinct-parent driver attribution.

## Related

- [[tracing-processor-op-lineage|Tracing processor-op lineage]] — how to walk `processor_parent_msg_id` to the root op.
- [[op-registry|op_registry]] — map an `operation0` value to the source file that defines the op.
- [[trigger-event-fanout|trigger_event fan-out]] — what the `data_json._traceback` / `event_context.update_spec[0].retry_count` fields encode for the interceptor + write_back retry storm.
- [[queue-worker-pool-segregation|Processor worker-pool / queue-group segregation]] — which worker pool drains a given `queue_name` (capacity behind the drain rate).
- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — uses `message_dispatched` (direct + parent attribution), `message_processed` status/latency, to diagnose a backed-up queue's inflow vs drain.
- [[../data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — resolves `REDSHIFT_LOG` to the region's physical warehouse.
- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — the `starrocks_utils` read path (this table uses the adapter-factory `dwh.get_list` path instead).
- [[../vscode-repo/python-import-root|Python import root]] — `PYTHONPATH=$CODE_BASE/www` for these `www`-rooted imports.

---
*Sources:* `www/db/base_log_event.py` (:181, :186-187, :199-213, :215-235, :255-289), `www/processor/worker_utils.py:162-244`, `www/processor/queue_utils.py:277-303`, `www/processor/op_monitor.py:30,:53`. Witnesses: `inputs/2026-06-26-smid-processor-trace.md`, `inputs/2026-06-26-queue-backed-up-batch-requests.md` (`t_create` confirmed **UTC**).
