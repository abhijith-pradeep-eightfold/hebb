---
name: query-starrocks
model: sonnet
description: Run a read-only query against the StarRocks data warehouse (e.g. log.search_query_log) from the vscode repo. Use when a task asks to count, aggregate, or read rows from StarRocks / the data warehouse — it points you at the StarRocks access pattern and the correct import root so you don't re-derive them.
---

# Query StarRocks

Run a read-only query against the [[../../../wiki/data-warehouse/starrocks|StarRocks data warehouse]]. The access facts live in the wiki; the runtime judgment this skill carries is **composing the right SQL**; execution is a **bundled, read-only runner** — `scripts/query_starrocks.py` — that calls `starrocks_utils.get_list` and refuses anything that isn't a read statement. Because the runner is anchored under the skill dir, the bash execution policy (`core/tools/bash_exec_policy.py`) auto-allows it, so it runs without an approval prompt every time.

## Steps

1. **Get the access pattern from the wiki** (via `wiki-reader`). Read, under `wiki/data-warehouse/`:
   - [[../../../wiki/data-warehouse/querying-starrocks|Querying StarRocks]] — the `starrocks_utils.get_list(query, db_type=DBType.STARROCKS.value, cache_ttl_secs=...)` entry point, the call chain, and other entry points (`get_max_value`, `get_customer_data`, `execute_query`).
   - [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]] — columns and the `t_create` (per-query event time) vs. `analytics_loaded_at` (ETL load time) gotcha, if the target is that table.
   - [[../../../wiki/data-warehouse/starrocks|StarRocks data warehouse]] — region gating and that credentials come from Secrets Manager (you don't supply them).

2. **Compose the SQL** for the request (this is the runtime judgment this skill carries):
   - Filter time windows on the **event-time** column (e.g. `t_create`), never on `analytics_loaded_at`.
   - MySQL-style datetime functions work: `WHERE t_create >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)`.
   - For a count/aggregate over a window, add a **sanity row** first — warehouse `NOW()`, `MIN/MAX` of the event-time column, and `COUNT(*)` over the window — to confirm the window resolved against live data and to reveal ingest lag.
   - Use `cache_ttl_secs=None` in `get_list` for a fresh/live read (the default is 600s).

3. **Run it via the bundled runner.** Put the composed SQL in a scratch `.sql` file (use the `Write` tool — *not* a `cat`/heredoc, which itself prompts), then run the bundled script against that file:
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_starrocks.py" /path/to/query.sql [--region <region>]
   ```
   - **`--region <region>`** — sets `EF_DEFAULT_REGION` for this invocation so warehouse routing targets the right cluster. Valid StarRocks regions: `us-west-2`, `eu-central-1`, `ca-central-1`, `ap-southeast-2`, `westus2` (Azure). When unset, `EF_DEFAULT_REGION` from the environment is used. Runner exits `3` if the resolved region is unsupported (StarRocks region gate).
   - **Pass the SQL by file, not inline.** Warehouse predicates contain `>`/`<`/`;` (e.g. `t_create >= DATE_SUB(...)`); any of those in the command string trips the bash gate and forces a prompt. A file-path argument keeps the command operator-free, so the gate keeps auto-allowing the run. (`--sql "..."` exists for operator-free queries only.)
   - **`PYTHONPATH="$CODE_BASE/www"`**, not `$CODE_BASE`: these packages are `www`-rooted — see [[../../../wiki/vscode-repo/python-import-root|Python import root]]. (`$CODE_BASE` alone fails with `ModuleNotFoundError: No module named 'datawarehouse'`.)
   - The runner **enforces read-only**: it rejects anything not beginning with `SELECT`/`WITH`/`SHOW`/`DESCRIBE`/`EXPLAIN`, and rejects stacked statements, *before* importing the codebase or touching the warehouse — on top of `get_list` already using the read-only cluster. Both guards run on a **comment-stripped** copy of the SQL, so a `;` or a verb that appears inside a `--`/`#`/`/* */` comment is ignored — you can keep `;` and SQL keywords in your comment preamble. Useful flags: `--cache-ttl-secs 600` (default `None` = fresh/live), `--format json`, `--json-out PATH` (write rows as JSON — feed the file to the **`plot-result-set`** skill to chart them; plotting is generic matplotlib, kept as its own skill rather than folded in here).

4. **Interpret and report.** Confirm the window against the sanity row (expect `max(event_time)` to trail `NOW()` slightly due to ingest lag). The runner exits `2` on a read-only-guard rejection and `3` when the region gate blocks (the `db_type in DBType.all_starrocks_values()` assert fails) — there StarRocks isn't supported in the resolved region, so report that exactly rather than guessing.

## Notes

- **Read-only, two ways.** `get_list` already uses `op_type='read'` (the `STARROCKS-CLUSTER-RO` cluster), so a write can't reach a writable connection through it. The runner adds a second, local guard — a leading-keyword allowlist (`SELECT`/`WITH`/`SHOW`/`DESCRIBE`/`EXPLAIN`) plus a no-stacked-statements check — so a stray write fails fast with a clear message instead of being shipped to the warehouse. Writes are a different path entirely (`execute_query`, `op_type='write'`, `STARROCKS-CLUSTER-RW`); this skill never calls it.
- Which warehouse a query hits (StarRocks vs. Redshift vs. Databricks) is normally chosen by [[../../../wiki/data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]]; this skill uses the direct `starrocks_utils` path to target StarRocks specifically.
