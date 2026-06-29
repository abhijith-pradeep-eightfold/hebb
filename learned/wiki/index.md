# Hebb Wiki — Index

The compiled, interlinked knowledge base for the `EightfoldAI/vscode` (`www`) codebase. Start here and follow the wikilinks. Every page is reachable from this index.

## Oncall

- [[oncall/oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline for PagerDuty oncall tickets (read the alarm → characterize the metric → find the driver → trace & route to an owner), plus the catalog of ticket-type pages.
- [[oncall/queue-backed-up|Queue backed up]] — the SQS queue-depth ticket type: the metric-math CloudWatch alarm (`AWS/SQS ApproximateNumberOfMessagesVisible`, ≥50k), pulling the spike curve, then the **inflow-vs-drain fork** (depth is a stock = ∫(inflow−drain)) — the inflow branch (direct composition + correct distinct-parent attribution → **comparative-window lift** to separate the spike-specific driver from high-baseline noise → trace root op → route owner) and the drain branch (op errors, processing latency, worker-pool contention, volume×latency).
- [[oncall/solr-cpu-high|Solr CPU too high]] — the Solr-replica host-CPU ticket type: the `CPUUtilization` 75%/5-of-6 alarm, characterizing the spike per-replica (`solr-shard-cpu`), then the **indexing-vs-query split** (CPU is a flow metric = indexing + query work; `callerid='index'` vs all other callerids) — the rate-metric analog of the queue-depth fork — then the `callerid × group_id × env` driver breakdown, and the `sequence_message_id` bridge from a query surge back to its root **processor** op and owner.
- [[oncall/host-unhealthy|Host unhealthy]] — the Elastic Beanstalk ELB health-check ticket type: a metric-math CloudWatch alarm whose breach signal is the **difference** `UnHealthyHostCount − HealthyHostCount ≥ 0` (a third alarm shape — merge two `AWS/ApplicationELB` series, not a single-metric threshold), then the **churn-vs-fault fork** (transient deploy/instance-replacement churn that self-resolves vs. a sustained fault), settled by the two EB evidence sources — the hosts behind the target group (instance type, launch time, env) and the EB environment event stream — with no processor lineage (route to the EB environment owner / Core Infra).

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

## Infra / telemetry

- [[infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — pull a CloudWatch alarm definition and the underlying EC2 `CPUUtilization` timeseries via read-only AWS CLI; alarm config (75% Average, 5-of-6 300s), `InstanceId` dimension, CloudWatch is UTC.

## vscode repo / environment

- [[vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — why scripts that import `www` packages need `PYTHONPATH=$CODE_BASE/www`, not `$CODE_BASE`; also notes libraries available in the venv (matplotlib 3.10.0).
- [[repo/codeowners-ownership|CODEOWNERS ownership resolution]] — resolve who owns a source file from `.github/CODEOWNERS` (last-matching-pattern-wins, no global default → unmatched files have no owner), the git-authorship fallback, and the org-team-read limitation on resolving `@org/team` handles to members.

## Process / agent discipline

- [[process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor on the real metric curve first, then correlate a candidate cause over the confirmed window plus a baseline (CloudWatch and warehouse `t_create` are both UTC — same clock, no shift); a non-correlation is a real finding.

## Skills

- [[skills/index|Skills catalog]] — every capability compiled into Hebb, named the way Claude Code loads it, each linked to the wiki pages it builds on. Generated from skill frontmatter; consult it and load the skill that fits before improvising.
