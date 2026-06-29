# log.search_query_log table

**Summary:** The per-query fact table for search. One logical table defined across all three analytics warehouses; in [[starrocks|StarRocks]] it lives in the `log` schema as an OLAP table partitioned by day on `t_create`. The key gotcha: `t_create` is the per-query event time; `analytics_loaded_at` is the ETL load time.

## StarRocks definition

DDL: `www/datawarehouse/starrocks/sql/fact_tables/search_query_log.sql` — `CREATE TABLE log.`search_query_log`` … `ENGINE=OLAP`.

### Columns

| Column | Type | Notes |
|---|---|---|
| `unique_id` | bigint AUTO_INCREMENT | |
| `t_create` | datetime | **per-query event timestamp — filter windows on this** |
| `sequence_message_id` | varchar(200) | the **processor SMID** that issued the query — see [[#sequence_message_id|below]] |
| `api` | varchar(200) | |
| `search_host` | varchar(200) | |
| `core` | varchar(200) | |
| `shard_id` | int | |
| `is_instant` | boolean | |
| `callerid` | varchar(200) | the calling **feature / code path**; `callerid='index'` = indexing — see [[#callerid|below]] |
| `rows_requested` | int | |
| `rows_returned` | int | |
| `hostname` | varchar(200) | |
| `cloudwatch_log_stream` | varchar(200) | |
| `data_json` | json | |
| `query_attempt` | int | |
| `timeout_seconds` | int | |
| `latency_milliseconds` | int | |
| `status_code` | int | |
| `group_id` | varchar(100) | distribution key |
| `env` | varchar(200) | the **originating service** of the query (e.g. `github-ci`, `processor`) — see [[#env|below]] |
| `analytics_loaded_at` | datetime DEFAULT CURRENT_TIMESTAMP | **ETL load time — NOT query time** |

### This *is* the Solr query log (no separate table)

There is **no separate "Solr query-log" table** — this one carries the Solr-specific columns directly: `core` (the Solr core), `shard_id`, `search_host`, and `is_instant`. So counting "Solr queries" is a filtered read of `log.search_query_log`, not a hit against a Solr admin/metrics endpoint.

### Scoping filters

To scope a count to a specific customer and core, filter — alongside the `t_create` window — on:

- **`group_id`** — the customer/tenant id (also the distribution HASH key), e.g. `group_id = 'volkscience.com'`.
- **`core`** — the Solr core, e.g. `core = 'profiles'`.

Both are plain `WHERE` columns. Example windowed + scoped predicate:

```sql
WHERE t_create >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
  AND group_id = 'volkscience.com'
  AND core = 'profiles'
```

### Timestamp semantics (gotcha)

`t_create` is stored in **UTC** — like the other `log.*` tables (e.g. [[../processor/processor-event-log|processor_event_log]]). So when correlating against a UTC source such as an [[../infra/cloudwatch-cpu-alarm|EC2 CPUUtilization]] curve, it is already on the **same clock — no shift needed** (a CPU spike at 08:20–08:35 UTC is matched against `t_create` literals `08:20–08:35` directly).

### Physical layout

- `PARTITION BY date_trunc('day', t_create)`
- `DISTRIBUTED BY HASH(group_id) BUCKETS 7`
- `ORDER BY (group_id, api, env)`
- `partition_live_number = 31` — roughly 31 days of partitions retained.
- `compression = LZ4`, `datacache.enable = true`, `replication_num = 1`.

### Used as an incident-correlation source

Because each row carries `core`, `shard_id`, `search_host`, `api`, `callerid`, `env`, `latency_milliseconds`, and `rows_requested`/`rows_returned`, the table is the natural secondary source for testing whether **query load** (vs. indexing) drove a Solr host metric (e.g. a CPU alarm) — the backing data for the [[../oncall/solr-cpu-high|Solr CPU too high]] oncall ticket type. Break the incident window down by `group_id` (which tenant), `shard_id`/`search_host` (which shard/replica host), and `rows_requested` (large-fanout reads).

**Two equivalent keys split indexing from query** — use either:
- **`api`** — `query` reads vs. `update/json/docs` indexing writes (the topology-grounded split — see [[../solr/solr-collection-topology|Solr topology]]); writes fan out to all replicas, reads are load-balanced.
- **`callerid`** — `callerid='index'` is the indexing stream; every other `callerid` is a query read (the feature-grounded split — see [[#callerid|callerid]] below).

Once the indexing-vs-query split names the stream that rose, break that stream down by its **source-identifying columns** — `callerid` (which feature), `group_id` (which tenant), `env` (which originating service) — to find the driver. To pull the per-bucket split and the `callerid × group_id × env` driver breakdown in one step, **use the `query-solr-load` skill**.

The correlation can go either way and **a non-correlation is a real finding**: in the 2026-06-15 `profiles` shard-21 incident query load did **not** correlate with the CPU spike, while in the 2026-06-29 `profiles` shard-21 incident query throughput roughly **doubled** and drove it (indexing flat) — see [[../process/incident-metric-correlation|incident metric-correlation discipline]] and [[../oncall/solr-cpu-high|Solr CPU too high]].

### Source-identifying columns

These three columns identify *where a query came from* — together they answer "which feature, for which tenant, from which service" and (for processor-issued queries) link back to the processor op that issued it.

#### `callerid`

The **calling feature / code path** that issued the query — e.g. `pipeline_v2_leads:recommended`, `get_implicit_employee_counts_of_roles`, `check_management_permission`, `ideal-candidate-by-pos`. The reserved value **`callerid='index'` marks the indexing (write) stream**; all other values are query reads — so `callerid` is one of the [[#used-as-an-incident-correlation-source|two indexing-vs-query split keys]] and the primary feature dimension for a driver breakdown.

#### `env`

The **originating service / environment** of the query — the single most useful discriminator for *why* a load appeared. Observed values include **`github-ci`** (queries from CI test suites) and **`processor`** (queries issued by the `www` processor). Read this column **directly**; do not try to derive the originating environment from other identifiers (e.g. a `system_id` probe). `env` is also part of the table's `ORDER BY (group_id, api, env)`.

#### `sequence_message_id`

For a query issued by the processor (`env='processor'`), this carries the **processor SMID (`processor_msg_id`)** of the message that issued it. It is the **join key** from this table to [[../processor/processor-event-log|processor_event_log]]: feed a culprit `sequence_message_id` into the `trace-processor-op` skill to walk `processor_parent_msg_id` to the root processor op behind a query surge (and from there to its owner). This is how a Solr query surge is routed back to a processor batch job — see [[../oncall/solr-cpu-high|Solr CPU too high]].

## Defined across three warehouses

The same logical table also has DDL for:

- **Redshift** — `www/datawarehouse/sql/redshift_log_tables/search_query_log.sql` (`public` schema).
- **Databricks** — `www/datawarehouse/databricks/analytics_project/src/sql/fact_tables/search_query_log.sql`.

Which one a query reads is decided by the [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]].

## Observed data point

On 2026-06-24 at 13:38 (us-west-2 StarRocks), `COUNT(*)` over `t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)` returned **977,347** rows; `min(t_create) ≈ 13:08`, `max(t_create) ≈ 13:36`.

## Related skills

- `query-solr-load` — use it to pull the per-bucket indexing-vs-query split (`callerid='index'` vs all other callerids, `--mode split`) and the per-source `callerid × group_id × env` driver breakdown (`--mode drivers`) for a `core`+`shard_id`, the analytical core of a Solr-CPU investigation.

## Related

- [[starrocks|StarRocks data warehouse]] — the warehouse hosting this table.
- [[querying-starrocks|Querying StarRocks]] — how to read it (`starrocks_utils.get_list`), with worked count-over-window and N-minute-bucketing techniques.
- [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — warehouse routing.
- [[../oncall/solr-cpu-high|Solr CPU too high]] — the oncall ticket type that uses this table for the indexing-vs-query split and the driver breakdown.
- [[../processor/processor-event-log|processor_event_log]] — joined via `sequence_message_id` to trace a processor-issued query surge to its root op.

---
*Sources:* `www/datawarehouse/starrocks/sql/fact_tables/search_query_log.sql`, `www/datawarehouse/sql/redshift_log_tables/search_query_log.sql`, `www/datawarehouse/databricks/analytics_project/src/sql/fact_tables/search_query_log.sql`. Witnesses: `inputs/2026-06-24-starrocks-query-count.md`, `inputs/2026-06-24-solr-query-buckets.md`, `inputs/2026-06-26-queue-backed-up-batch-requests.md` (`t_create` confirmed **UTC**), `inputs/2026-06-29-profiles-shard21-r1-cpu.md` (`env`, `callerid='index'`, `sequence_message_id`→processor SMID).
