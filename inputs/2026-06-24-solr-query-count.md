# Count Solr queries in the last 30 minutes

**Task:** Determine how many Solr queries happened in the last 30 minutes by counting rows in the StarRocks `log.search_query_log` table over a 30-minute window.

## Log

### [14:49] wiki-reader
- **observed:** Consulted the Hebb wiki for the Solr-query-count task. Read `wiki/index.md`, then followed wikilinks to `wiki/data-warehouse/search-query-log.md`, `wiki/data-warehouse/querying-starrocks.md`, and `wiki/vscode-repo/python-import-root.md`. Key facts found: `log.search_query_log` is the per-query fact table for search (columns include `core`, `shard_id`, `search_host`, `is_instant`); filter time windows on `t_create` (per-query event time), NOT `analytics_loaded_at` (ETL load time). Read path is `starrocks_utils.get_list(query, cache_ttl_secs=600, ...)`; pass `cache_ttl_secs=None` for a live read. Imports `from datawarehouse.starrocks import starrocks_utils` and `from db.db_type import DBType` need `PYTHONPATH=$CODE_BASE/www` (not `$CODE_BASE`). A prior witness on 2026-06-24 ~13:38 recorded `COUNT(*)` over the same 30-min window returning 977,347 with `max(t_create)` ~2 min behind warehouse `NOW()`.

### [14:50] query-starrocks + task-executer
- **observed:** Confirmed env vars: `CODE_BASE=/home/ec2-user/vscode`, `VSCODE_PYTHON=/home/ec2-user/py3.13-virt/bin/python`; `$CODE_BASE/www/datawarehouse/starrocks` exists. Composed a single read-only COUNT query with a sanity row (`NOW()`, `MIN(t_create)`, `MAX(t_create)`, `COUNT(*)`) over `log.search_query_log` filtered on `t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)`. Interpreted "Solr queries in last 30 minutes" as the count of `log.search_query_log` rows in that window (Solr-specific columns present), not a live Solr admin/metrics endpoint. Wrote the scratch script and presented it to the user for approval. Not run yet — awaiting explicit user go-ahead.
- **script:** `scratch` — `/tmp/claude-1001/-home-ec2-user-hebb/2702ded9-ff69-48e3-82c7-b9d86c0742fc/scratchpad/count_solr_queries_30m.py` (calls `starrocks_utils.get_list(QUERY, db_type=DBType.STARROCKS.value, cache_ttl_secs=None)`; run with `PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" <script>`).
