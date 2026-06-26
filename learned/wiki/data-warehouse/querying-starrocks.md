# Querying StarRocks

**Summary:** How to run a read-only query against the [[starrocks|StarRocks data warehouse]] from the `www` codebase — the `starrocks_utils` entry points, the cache-TTL and region-gate behavior, and the call chain down to the DB client.

## Entry module

`www/datawarehouse/starrocks/starrocks_utils.py`. Import as `from datawarehouse.starrocks import starrocks_utils` and `from db.db_type import DBType` — note these are `www`-rooted packages, so a script must run with `PYTHONPATH=$CODE_BASE/www` (see [[../vscode-repo/python-import-root|Python import root]]).

## `get_list` — the read path

```
get_list(query, vals=(), db_type=DBType.STARROCKS.value, return_dict=True,
         json_cols=None, cache_ttl_secs=600, cache_version=None,
         cache_no_result=True, proxy_type=None, ...)
```
(`starrocks_utils.py:31-37`)

- **`cache_ttl_secs` defaults to `600`** (10 min). For a fresh/live read, pass `cache_ttl_secs=None` to bypass the cache.
- It **asserts** `db_type in DBType.all_starrocks_values()` (`:34`). In a region where StarRocks isn't supported, `all_starrocks_values()` is `[]` and this assert fails — see [[starrocks#region-gating|region gating]].
- Call chain: `starrocks_utils.get_list` → `db.db_utils.get_list` → `db.db_client.get_db_client(db_type='starrocks', op_type='read')`. The adapter wrapper is `www/cloud_interfaces/adapters/datawarehouse/starrocks_adapter.py`.

## Other entry points (same module)

- `get_max_value(table, field, ...)` — `starrocks_utils.py:39`.
- `get_customer_data(group_id, query, ...)` — per-customer reads; fetches per-`group_id` credentials via `get_customer_data_credentials` (`:45-51`).
- `get_starrocks_customer_views(schema_name='customer_views', ...)` — lists views (`:76`).
- `execute_query(query, ...)` — the **write** path; gets a client with `op_type='write'`, which uses the `STARROCKS-CLUSTER-RW` secret (`:79-81`, see [[starrocks#credentials-secrets-manager-not-env|credentials]]).

## SQL dialect note

MySQL-style datetime functions work against StarRocks. A windowed count over the last 30 minutes used:

```sql
SELECT COUNT(*) FROM log.search_query_log
WHERE t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)
```

Filter on `t_create` (the per-query event time), **not** `analytics_loaded_at` — see [[search-query-log|the table page]] for why.

## Worked technique: sanity row + windowed count

When counting rows over a time window, run a sanity row first — warehouse `NOW()`, `MIN/MAX` of the event-time column, and `COUNT(*)` over the window — to confirm the window resolved against live data and to surface ingest lag. (Observed: `max(t_create)` trailed `NOW()` by ~2 minutes, reflecting load latency into [[search-query-log|log.search_query_log]].)

## Worked technique: counts in N-minute buckets

To bucket event-time counts into fixed N-minute windows, use the StarRocks **`time_slice`** function on the event-time column, then group/order by the bucket:

```sql
SELECT time_slice(t_create, INTERVAL 5 MINUTE) AS bucket_start, COUNT(*) AS n
FROM log.search_query_log
WHERE t_create >= DATE_SUB(NOW(), INTERVAL 6 HOUR)
  AND group_id = 'volkscience.com' AND core = 'profiles'
GROUP BY bucket_start
ORDER BY bucket_start
```

`time_slice(<datetime>, INTERVAL N <unit>)` floors each timestamp to the start of its N-unit bucket (here a 6-hour window yields 72 five-minute buckets). Pair it with the sanity row above and the same `t_create` window + scoping filters (`group_id`, `core`) described on [[search-query-log|the table page]].

### Plotting the result to a PNG

`matplotlib` **3.10.0** is available in the vscode venv (`$VSCODE_PYTHON` — see [[../vscode-repo/python-import-root|Python import root]]). For headless plotting (no display), select the **Agg** backend before importing pyplot and `savefig` to a PNG — e.g. bucket start on the x-axis, count on the y-axis:

```python
import matplotlib
matplotlib.use("Agg")          # headless: render to file, no display
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
# ... build x = [r["bucket_start"] for r in rows], y = [r["n"] for r in rows] ...
plt.bar(x, y); plt.savefig("/path/to/out.png")
```

The query half still needs `PYTHONPATH="$CODE_BASE/www"`; matplotlib itself has no `www` import dependency.

## Related

- [[starrocks|StarRocks data warehouse]] — region gating, credentials, connection.
- [[search-query-log|log.search_query_log table]] — schema and column semantics.
- [[datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] — how a query gets routed to StarRocks vs. another warehouse.
- [[../vscode-repo/python-import-root|Python import root]] — running scripts that import `www` packages.

---
*Sources:* `www/datawarehouse/starrocks/starrocks_utils.py` (:31-37, :34, :39, :45-51, :76, :79-81), `www/cloud_interfaces/adapters/datawarehouse/starrocks_adapter.py`. Witnesses: `inputs/2026-06-24-starrocks-query-count.md`, `inputs/2026-06-24-solr-query-buckets.md` (`time_slice` bucketing, matplotlib→PNG).
