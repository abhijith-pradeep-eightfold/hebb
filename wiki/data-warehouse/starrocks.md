# StarRocks data warehouse

**Summary:** StarRocks is one of the OLAP analytics data warehouses behind the `www` codebase (alongside Redshift and Databricks). It is region-gated, its credentials come from AWS Secrets Manager, and its host/port/dbname are resolved at runtime from cluster config — none of it lives in environment variables.

## What it is

`DBType.STARROCKS.value == 'starrocks'` (`www/db/db_type.py:21`). Tables use `ENGINE=OLAP`. The same logical fact tables exist in StarRocks, Redshift, and Databricks; which warehouse a query hits is chosen by the [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]]. To run queries against it, see [[querying-starrocks|Querying StarRocks]].

## Region gating

StarRocks is only available in these regions: `us-west-2`, `eu-central-1`, `ca-central-1`, `ap-southeast-2`, `westus2`.

- `DBType.is_db_type_supported_in_region('starrocks', region)` enforces this list — `www/db/db_type.py:110-111`.
- `DBType.all_starrocks_values()` returns `[DBType.STARROCKS.value]` when the region is supported, else `[]` — `www/db/db_type.py:147-148`.
- Because [[querying-starrocks|`starrocks_utils.get_list`]] asserts `db_type in DBType.all_starrocks_values()` (`www/datawarehouse/starrocks/starrocks_utils.py:34`), a query from an unsupported region **fails fast on that assert** — `all_starrocks_values()` is empty there.

`us-west-2` is a supported region; a query observed running there was not blocked by the gate.

## Credentials (Secrets Manager, not env)

There is no plaintext StarRocks host or credential in the environment. A scan of `env` found only Aurora MySQL (`VSDB_URI`-family) and Redshift URIs — no `star`/`rock`/`olap`/`9030`/`8030`/`dwh` variable. The code fetches credentials at runtime from AWS Secrets Manager:

- `STARROCKS-CLUSTER-RO` — read credentials.
- `STARROCKS-CLUSTER-RW` — write/admin credentials.

Selection is by operation type in `get_cluster_secret_key` (`www/db/db_connection.py:786-794`): `op_type == 'write'` ⇒ `admin_secret=True` ⇒ `STARROCKS-CLUSTER-RW`; otherwise `STARROCKS-CLUSTER-RO`.

## Connection (cluster config resolved at runtime)

Host/port/dbname are not hardcoded — they come from cluster config (`www/db/db_connection.py:270-277`):

```
cluster_config = db_shard_utils.get_cluster_config(db_type, cluster_id, region=region) or {}
cluster_uri = f'{host}:{port}/{db_name}'   # host/port/db_name pulled from cluster_config
```

At runtime the connection was observed to use the "hodor" client (`hodor_client.py`, log line "Using default cluster 0 for hodor starrocks"), region `usw2`, database `starrocks`.

## Related

- [[querying-starrocks|Querying StarRocks]] — the access path / how-to.
- [[search-query-log|log.search_query_log table]] — a StarRocks fact table.
- [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — warehouse selection.
- [[../vscode-repo/python-import-root|Python import root]] — scripts importing these packages need `PYTHONPATH=$CODE_BASE/www`.

---
*Sources:* `www/db/db_type.py` (:21, :110-111, :147-148), `www/db/db_connection.py` (:270-277, :786-794), `www/datawarehouse/starrocks/starrocks_utils.py:34`. Witness: `inputs/2026-06-24-starrocks-query-count.md`.
