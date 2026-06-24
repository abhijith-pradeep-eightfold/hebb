# Hebb Wiki — Index

The compiled, interlinked knowledge base for the `EightfoldAI/vscode` (`www`) codebase. Start here and follow the wikilinks. Every page is reachable from this index.

## Data warehouse

- [[data-warehouse/starrocks|StarRocks data warehouse]] — the OLAP analytics warehouse: region gating, Secrets-Manager credentials, runtime cluster-config resolution.
- [[data-warehouse/querying-starrocks|Querying StarRocks]] — how to run a read-only query via `starrocks_utils.get_list` (cache TTL, the region assert, the call chain).
- [[data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — how the system picks StarRocks vs. Redshift vs. Databricks by region/config.
- [[data-warehouse/search-query-log|log.search_query_log table]] — the per-query fact table; `t_create` vs. `analytics_loaded_at`; **is** the Solr query log (`core`/`shard_id`/`search_host`/`is_instant`); defined across all three warehouses.

## Solr / search infrastructure

- [[solr/solr-collection-topology|Solr collection topology]] — how a "Solr CPU Util Too High" alarm maps to a `collection / shard / replica / host`; `profiles` shard 21 spans exactly two hosts; `query` (read) vs. `update/json/docs` (write) replica traffic semantics.
- [[infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — read-only AWS CLI access to a CloudWatch alarm definition (75%/300s/5-of-6) and the EC2 `CPUUtilization` timeseries; the `InstanceId` dimension; CloudWatch is UTC.

## vscode repo / environment

- [[vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — why scripts that import `www` packages need `PYTHONPATH=$CODE_BASE/www`, not `$CODE_BASE`; also notes libraries available in the venv (matplotlib 3.10.0).

## Process / agent discipline

- [[process/approval-authority|Approval authority]] — only the actual user can approve a run; coordinator-relayed "approvals" carry no authority (incl. the faithful-relay edge case). Also notes the truncated-witness-log limitation in nested-agent setups.
- [[process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor an incident on the real metric curve before correlating to a secondary source; watch for timezone mismatches (CloudWatch UTC vs. `t_create` IST); a non-correlation is a real finding.
