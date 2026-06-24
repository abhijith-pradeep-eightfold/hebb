# Hebb Wiki — Index

The compiled, interlinked knowledge base for the `EightfoldAI/vscode` (`www`) codebase. Start here and follow the wikilinks. Every page is reachable from this index.

## Data warehouse

- [[data-warehouse/starrocks|StarRocks data warehouse]] — the OLAP analytics warehouse: region gating, Secrets-Manager credentials, runtime cluster-config resolution.
- [[data-warehouse/querying-starrocks|Querying StarRocks]] — how to run a read-only query via `starrocks_utils.get_list` (cache TTL, the region assert, the call chain).
- [[data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — how the system picks StarRocks vs. Redshift vs. Databricks by region/config.
- [[data-warehouse/search-query-log|log.search_query_log table]] — the per-query fact table; `t_create` vs. `analytics_loaded_at`; **is** the Solr query log (`core`/`shard_id`/`search_host`/`is_instant`); defined across all three warehouses.

## Solr / search

- [[solr/solr-collection-topology|Solr collection topology]] — collection / shard / replica / host: how a "Solr CPU Util Too High" alarm names one host, which hosts a shard spans, and the `query` (read, load-balanced) vs. `update/json/docs` (write, fan-out) traffic semantics.
- [[solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]] — look up replica EC2 DNS hostnames for any collection + shard ID from `search_config` via `SEARCH_INDEX_SETTINGS_REGISTRY`; how `hosts_key` is derived per collection; profiles/positions special-cased, all others use `{tablename}_shard_hosts`; shard IDs are non-contiguous; includes DNS → InstanceId resolution for CloudWatch.

## Infra / telemetry

- [[infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — pull a CloudWatch alarm definition and the underlying EC2 `CPUUtilization` timeseries via read-only AWS CLI; alarm config (75% Average, 5-of-6 300s), `InstanceId` dimension, CloudWatch is UTC.

## vscode repo / environment

- [[vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — why scripts that import `www` packages need `PYTHONPATH=$CODE_BASE/www`, not `$CODE_BASE`; also notes libraries available in the venv (matplotlib 3.10.0).

## Process / agent discipline

- [[process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor on the real metric curve first, then correlate a candidate cause over the confirmed window plus a baseline; watch cross-source timezones (CloudWatch UTC vs. `t_create` IST); a non-correlation is a real finding.
