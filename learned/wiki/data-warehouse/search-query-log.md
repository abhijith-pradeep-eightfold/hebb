# log.search_query_log table

**Summary:** The per-query fact table for search. One logical table defined across all three analytics warehouses; in [[starrocks|StarRocks]] it lives in the `log` schema as an OLAP table partitioned by day on `t_create`. The key gotcha: `t_create` is the per-query event time; `analytics_loaded_at` is the ETL load time.

## StarRocks definition

DDL: `www/datawarehouse/starrocks/sql/fact_tables/search_query_log.sql` — `CREATE TABLE log.`search_query_log`` … `ENGINE=OLAP`.

### Columns

| Column | Type | Notes |
|---|---|---|
| `unique_id` | bigint AUTO_INCREMENT | |
| `t_create` | datetime | **per-query event timestamp — filter windows on this** |
| `sequence_message_id` | varchar(200) | |
| `api` | varchar(200) | |
| `search_host` | varchar(200) | |
| `core` | varchar(200) | |
| `shard_id` | int | |
| `is_instant` | boolean | |
| `callerid` | varchar(200) | |
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
| `env` | varchar(200) | |
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

Because each row carries `core`, `shard_id`, `search_host`, `api`, `latency_milliseconds`, and `rows_requested`/`rows_returned`, the table is the natural secondary source for testing whether **query load** drove a Solr host metric (e.g. a CPU alarm). Break the incident window down by `group_id` (which tenant), `shard_id`/`search_host` (which shard/replica host), `api` (`query` reads vs. `update/json/docs` indexing writes — see [[../solr/solr-collection-topology|Solr topology]]), and `rows_requested` (large-fanout reads). In the 2026-06-15 `profiles` shard-21 incident this breakdown showed query load did **not** correlate with the CPU spike — see [[../process/incident-metric-correlation|incident metric-correlation discipline]].

## Defined across three warehouses

The same logical table also has DDL for:

- **Redshift** — `www/datawarehouse/sql/redshift_log_tables/search_query_log.sql` (`public` schema).
- **Databricks** — `www/datawarehouse/databricks/analytics_project/src/sql/fact_tables/search_query_log.sql`.

Which one a query reads is decided by the [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]].

## Observed data point

On 2026-06-24 at 13:38 (us-west-2 StarRocks), `COUNT(*)` over `t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)` returned **977,347** rows; `min(t_create) ≈ 13:08`, `max(t_create) ≈ 13:36`.

## Related

- [[starrocks|StarRocks data warehouse]] — the warehouse hosting this table.
- [[querying-starrocks|Querying StarRocks]] — how to read it (`starrocks_utils.get_list`), with worked count-over-window and N-minute-bucketing techniques.
- [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — warehouse routing.

---
*Sources:* `www/datawarehouse/starrocks/sql/fact_tables/search_query_log.sql`, `www/datawarehouse/sql/redshift_log_tables/search_query_log.sql`, `www/datawarehouse/databricks/analytics_project/src/sql/fact_tables/search_query_log.sql`. Witnesses: `inputs/2026-06-24-starrocks-query-count.md`, `inputs/2026-06-24-solr-query-buckets.md`, `inputs/2026-06-26-queue-backed-up-batch-requests.md` (`t_create` confirmed **UTC**).
