---
name: query-starrocks
description: Run a read-only query against the StarRocks data warehouse (e.g. log.search_query_log) from the vscode repo. Use when a task asks to count, aggregate, or read rows from StarRocks / the data warehouse — it points you at the StarRocks access pattern and the correct import root so you don't re-derive them.
---

# Query StarRocks

A thin composition skill: it tells you *which* compiled knowledge and *which* execution skill to use to run a read-only query against the [[../../../wiki/data-warehouse/starrocks|StarRocks data warehouse]]. It does not re-implement query execution — `task-executer` already owns running scripts against `$CODE_BASE`, and the access facts live in the wiki.

## Steps

1. **Get the access pattern from the wiki** (via `wiki-reader`). Read, under `wiki/data-warehouse/`:
   - [[../../../wiki/data-warehouse/querying-starrocks|Querying StarRocks]] — the `starrocks_utils.get_list(query, db_type=DBType.STARROCKS.value, cache_ttl_secs=...)` entry point, the call chain, and other entry points (`get_max_value`, `get_customer_data`, `execute_query`).
   - [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]] — columns and the `t_create` (per-query event time) vs. `analytics_loaded_at` (ETL load time) gotcha, if the target is that table.
   - [[../../../wiki/data-warehouse/starrocks|StarRocks data warehouse]] — region gating and that credentials come from Secrets Manager (you don't supply them).

2. **Compose the SQL** for the request (this is the runtime judgment this skill carries):
   - Filter time windows on the **event-time** column (e.g. `t_create`), never on `analytics_loaded_at`.
   - MySQL-style datetime functions work: `WHERE t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)`.
   - For a count/aggregate over a window, add a **sanity row** first — warehouse `NOW()`, `MIN/MAX` of the event-time column, and `COUNT(*)` over the window — to confirm the window resolved against live data and to reveal ingest lag.
   - To bucket counts into fixed N-minute windows, use StarRocks `time_slice(<event_time>, INTERVAL N MINUTE) AS bucket_start` and group/order by the bucket — see the "counts in N-minute buckets" worked technique in [[../../../wiki/data-warehouse/querying-starrocks|Querying StarRocks]]. Scope with plain `WHERE` columns (e.g. `group_id`, `core` on `log.search_query_log`).
   - Use `cache_ttl_secs=None` in `get_list` for a fresh/live read (the default is 600s).

3. **Run it via `task-executer`.** Write the read-only script that calls `starrocks_utils.get_list`, get explicit user approval (task-executer's hard rule), and run it in the vscode venv. **Critical:** these packages are `www`-rooted, so use `PYTHONPATH="$CODE_BASE/www"` — see [[../../../wiki/vscode-repo/python-import-root|Python import root]]. (`PYTHONPATH="$CODE_BASE"` fails with `ModuleNotFoundError: No module named 'datawarehouse'`.)
   ```python
   from datawarehouse.starrocks import starrocks_utils
   from db.db_type import DBType
   rows = starrocks_utils.get_list(query, db_type=DBType.STARROCKS.value, cache_ttl_secs=None)
   ```
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" your_script.py
   ```

4. **Interpret and report.** Confirm the window against the sanity row (expect `max(event_time)` to trail `NOW()` slightly due to ingest lag). If the region gate blocks (the `db_type in DBType.all_starrocks_values()` assert fails), StarRocks isn't supported in the resolved region — report that exactly rather than guessing.

## Notes

- **Plotting the result.** If the task wants a chart (e.g. bucketed counts over time), `matplotlib` 3.10.0 is in `$VSCODE_PYTHON`; use the Agg backend to render a PNG headlessly in the same read-only script. The recipe is in [[../../../wiki/data-warehouse/querying-starrocks|Querying StarRocks → plotting]] — no separate skill needed (the plotting is generic matplotlib, not a StarRocks-specific capability).
- **Read-only by default.** `get_list` uses `op_type='read'` (`STARROCKS-CLUSTER-RO`). Writes go through `execute_query` (`op_type='write'`, `STARROCKS-CLUSTER-RW`) — only when the task explicitly calls for it.
- Which warehouse a query hits (StarRocks vs. Redshift vs. Databricks) is normally chosen by [[../../../wiki/data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]]; this skill uses the direct `starrocks_utils` path to target StarRocks specifically.
