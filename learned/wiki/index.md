# Hebb Wiki — Index

The compiled, interlinked knowledge base for the `EightfoldAI/vscode` (`www`) codebase. Start here and follow the wikilinks. Every page is reachable from this index.

## Oncall

- [[oncall/oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline for PagerDuty oncall tickets (read the alarm → characterize the metric → find the driver → trace & route to an owner), plus the catalog of ticket-type pages.
- [[oncall/queue-backed-up|Queue backed up]] — the SQS queue-depth ticket type: the metric-math CloudWatch alarm (`AWS/SQS ApproximateNumberOfMessagesVisible`, ≥50k), pulling the spike curve, then the **inflow-vs-drain fork** (depth is a stock = ∫(inflow−drain)) — the inflow branch (direct composition + correct distinct-parent attribution → trace root op → route owner) and the drain branch (op errors, processing latency, worker-pool contention, volume×latency).
- [[oncall/solr-cpu-high|Solr CPU too high]] — the Solr-replica host-CPU ticket type: the `CPUUtilization` 75%/5-of-6 alarm, characterizing the spike per-replica (`solr-shard-cpu`), then the **indexing-vs-query split** (CPU is a flow metric = indexing + query work; `callerid='index'` vs all other callerids) — the rate-metric analog of the queue-depth fork — then the `callerid × group_id × env` driver breakdown, and the `sequence_message_id` bridge from a query surge back to its root **processor** op and owner.
- [[oncall/alarm-provisioning-failures|Alarm Provisioning Failures]] — the daily-DAG alarm-provisioning ticket type: the `airflow-alarm_provisioning_failures.sum` `Sum >= 1` alarm where **N datapoints = N independent failing alarm keys**; enumerate the failing key via the **`[Action Needed] Alarm` email** (not CW Logs), read its traceback, confirm a missing-`alarm_config`-entry root cause with a plain `config.get`, and route to the owner.
- [[oncall/rds-cpu-high|RDS CPU too high]] — the RDS cluster-role CPU ticket type: the `AWS/RDS CPUUtilization` p75 ≥90% / 8-of-8 alarm on `DBClusterIdentifier`+`Role` (often in GovCloud); pull both WRITER and READER curves, then **RDS Performance Insights** to split the DB load (wait events + top SQL + by host) — the **commit / redo-log-flush write-storm** signature — spot-check the actual SQL (query tags name the op/tenant/caller), trace to the producing op/code path, and route.
- [[oncall/redis-errors-detected|Redis Error Detected]] — the per-namespace Redis-errors ticket type: a `<namespace>` / `prod-…-redis-errors.sum` `Sum > 100` / 2-of-2 counter alarm owned by Core Infra. The crux: the error **counter** (`counters.add` → CloudWatch *metrics*) and the runbook's `"Got error executing"` **log line** (`_log_error` → CloudWatch *Logs*) are **independent sinks** — so the prescribed Logs Insights query can return zero on a real spike. Characterize from the metric curve + alarm history; deeper RCA needs **ElastiCache-side signals** the alarm can't see.

## Data warehouse

- [[data-warehouse/starrocks|StarRocks data warehouse]] — the OLAP analytics warehouse: region gating, Secrets-Manager credentials, runtime cluster-config resolution.
- [[data-warehouse/querying-starrocks|Querying StarRocks]] — how to run a read-only query via `starrocks_utils.get_list` (cache TTL, the region assert, the call chain).
- [[data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — how the system picks StarRocks vs. Redshift vs. Databricks by region/config.
- [[data-warehouse/search-query-log|log.search_query_log table]] — the per-query fact table; `t_create` vs. `analytics_loaded_at`; **is** the Solr query log (`core`/`shard_id`/`search_host`/`is_instant`); defined across all three warehouses.

## Solr / search

- [[solr/solr-collection-topology|Solr collection topology]] — collection / shard / replica / host: how a "Solr CPU Util Too High" alarm names one host, which hosts a shard spans, and the `query` (read, load-balanced) vs. `update/json/docs` (write, fan-out) traffic semantics.
- [[solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]] — look up replica EC2 DNS hostnames for any collection + shard ID from `search_config` via `SEARCH_INDEX_SETTINGS_REGISTRY`; how `hosts_key` is derived per collection; profiles/positions special-cased, all others use `{tablename}_shard_hosts`; shard IDs are non-contiguous; includes DNS → InstanceId resolution for CloudWatch.

## Processor

- [[processor/processor-event-log|processor_event_log table]] — the per-message event log for SQS-driven processor ops: **SMID = `processor_msg_id`**, parent edge `processor_parent_msg_id`, op = `operation0` (`operations_list`); modelled by `ProcessorLogEvent` (logical db_type `REDSHIFT_LOG`, resolved per region by the adapter factory); the `get_processor_event_logs` helper's `group_id` requirement; column semantics — `latency_milliseconds` = **processing** latency (not queue wait; use `percentile_approx`/`total_proc_sec`), the `data_json` payload (`_traceback`, inner `event_type`, `update_spec[0].retry_count`), `msg_retry_count` `-1` sentinel.
- [[processor/tracing-processor-op-lineage|Tracing processor-op lineage]] — find a SMID's root processor op by walking `processor_parent_msg_id` to the parentless row; the dispatch mechanism (`_parent_msg_id`), and the `REROUTE_TO_HIGH_MEM` same-op two-hop reroute shape.
- [[processor/op-registry|op_registry]] — the central map from a processor operation name (the `operation0` value) to its `(module_path, ClassName)`, i.e. the source file that defines the op.
- [[processor/queue-worker-pool-segregation|Processor worker-pool / queue-group segregation]] — processor capacity is segregated into named **queue groups** (`processor_worker_<instance_type>_ecs_config` → `worker_config: queue_group → {queues, max_count, scale_out}`, via `ecs_scaling_utils`); how to resolve a queue's pools + sibling queues to test drain-side contention. Region-scoped runtime config.
- [[processor/trigger-event-fanout|trigger_event fan-out]] — the two mechanisms that re-broadcast entity changes as `trigger_event` messages: the interceptor `post_save` re-seed (dominant) and the `write_back_sor` retry self-loop (bounded at 6); why their `schedule_after_secs` delay forces distinct-parent attribution.

## ATS

- [[ats/ats-entity-cache|ats_entity_cache write path]] — the `AtsEntity` model on the **`log` DB** (`shared-log-cluster` family); `invalidate_ats_entity` writes one committed single-row upsert per call, driven per-deleted-position by the processor **`position_index`** op whose `pid` batches come from the bulk re-index CLI `re-index-db-positions.py`; the indexing gate is on/off (`search_group_mappings.do_not_index`), **not** a rate throttle. Owner: dp-integrations.

## Infra / telemetry

- [[infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — pull a CloudWatch alarm definition and the underlying EC2 `CPUUtilization` timeseries via read-only AWS CLI; alarm config (75% Average, 5-of-6 300s), `InstanceId` dimension, CloudWatch is UTC.
- [[infra/config-get|Reading a config value (`config.get`)]] — the minimal `from config import config; config.get('<name>', field_name='<field>')` read; config is **broadcast to all regions**, so read it plainly with the box's own creds — do NOT override `EF_DEFAULT_REGION` and do NOT add IAM/assume-role handling (both cause self-inflicted signing/access dead-ends).
- [[infra/govcloud-access|GovCloud (us-gov-west-1) access]] — GovCloud is a **separate AWS partition** (`aws-us-gov`); AWS calls need the `GOV_AWS_*` creds (the commercial key can't reach it). CloudWatch / RDS / Performance Insights / Logs-Insights answer from the agent box; the gov **warehouse** does not — read it in-region via `pssh shared-gov` using the model's region-agnostic `dwh` path (the StarRocks-only bundled tracer rejects gov).
- [[infra/rds-performance-insights|RDS Performance Insights]] — decompose a DB instance's load (`db.load.avg` = average active sessions) by wait event / SQL / user / host via `aws pi`; how to read AAS against the vCPU ceiling, the **commit / redo-log-flush write-storm** signature, and the **rate-vs-load two-axis** rule (why `ROLLBACK/sec ≈ COMMIT/sec` is the benign SQLAlchemy pool reset-on-return, not failing queries).

## vscode repo / environment

- [[vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — why scripts that import `www` packages need `PYTHONPATH=$CODE_BASE/www`, not `$CODE_BASE`; also notes libraries available in the venv (matplotlib 3.10.0).
- [[repo/codeowners-ownership|CODEOWNERS ownership resolution]] — resolve who owns a source file from `.github/CODEOWNERS` (last-matching-pattern-wins, no global default → unmatched files have no owner), the git-authorship fallback, and the org-team-read limitation on resolving `@org/team` handles to members.

## Process / agent discipline

- [[process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor on the real metric curve first, then correlate a candidate cause over the confirmed window plus a baseline (CloudWatch and warehouse `t_create` are both UTC — same clock, no shift); a non-correlation is a real finding.

## Skills

- [[skills/index|Skills catalog]] — every capability compiled into Hebb, named the way Claude Code loads it, each linked to the wiki pages it builds on. Generated from skill frontmatter; consult it and load the skill that fits before improvising.
