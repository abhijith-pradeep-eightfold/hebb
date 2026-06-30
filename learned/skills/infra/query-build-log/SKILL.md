---
name: query-build-log
model: sonnet
description: Read deployment records from the build_log table (the global db) — one row per build/deploy with namespace / git_revision / status / tag / t_create scalar columns plus a compressed data_json payload (status/release/lint/pytest logs). Use when a task needs to confirm whether a deploy was actually triggered or find a specific build/deploy — "was an mcp deploy triggered", "find the build_log row for namespace X in this window", "what revision/tag did app Y deploy", "did a Deployment Failed row appear for Z", "show the data_json for build id N". The source of truth for deploy records (deploys propagate to all regions; alerts land in #build_alerts) — reach for it as the deploy-confirmation step of an "Airflow DAG Failure-deploy_to_azure" oncall, or any time you need to pin a deploy by namespace/revision/status. Encodes the critical gotcha that a SELECT * over a window times out on the compressed data_json, so it selects scalar columns and pushes LIKE/range filters into SQL, then pulls data_json only for a matched id.
knowledge_required:
  - "[[../../../wiki/infra/build-log-table|build_log table (global db)]]"
knowledge_optional:
  - "[[../../../wiki/vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]]"
  - "[[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]]"
---

# Query build_log (the deployment record)

Read deployment records from the [[../../../wiki/infra/build-log-table|`build_log` table]] on the **`global`** db. The access facts live in the wiki; the runtime judgment this skill carries is **which predicates** to filter on (namespace / status / tag / window) and **reading the result** (is there a matching deploy, what revision/tag/status). Execution is a **bundled, read-only runner** — `scripts/query_build_log.py` — that reads through the shared `hebb_utils.deploy.build_log` module and bakes in the no-`SELECT *` discipline so a windowed scan does not time out.

## When to use

- Confirm whether a deploy was actually triggered for an app, e.g. as the deploy-confirmation step of an [[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure]] (`deploy_to_azure`) oncall — find the matching `namespace`/`tag`/`git_revision`/`status` row in the failure window.
- Pin a specific build/deploy by namespace, revision, status, or tag.
- `build_log` is the **source of truth** for deploy records — deployments propagate to all regions and alerts land in `#build_alerts` (the per-app `azure-deployments` Slack notification is a separate, secondary feed).

## Steps

1. **Read the access pattern from the wiki** (via `wiki-reader`): [[../../../wiki/infra/build-log-table|build_log table (global db)]] — the `BuildLog` DBLoader on the `global` db, the scalar columns vs the compressed `data_json`, the `BuildStatus`/`BuildLogTag` enums, and the `SELECT *`-timeout gotcha.

2. **Choose the predicates** (the runtime judgment): which of `--namespace` / `--status` / `--tag` / `--start` / `--end` narrow to the deploy you're after. `--namespace`/`--status`/`--tag` are `LIKE` matches (partial OK — `--status Deployment` matches both `Deployment Passed` and `Deployment Failed`). A row still at status `Building` has passed tests but not yet recorded a deploy outcome.

3. **Run the bundled runner** (the gate-passing shape — never hardcode the interpreter):
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_build_log.py" --namespace mcp --start "2026-06-29 00:00:00" --end "2026-07-01 00:00:00"
   ```
   - **`PYTHONPATH="$CODE_BASE/www"`**, not `$CODE_BASE`: `BuildLog` is `www`-rooted (`internal.build_log`) — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
   - The runner selects **scalar columns only** by default and pushes `LIKE`/range filters into SQL (avoiding the `SELECT *` timeout on the compressed `data_json`). Add **`--full`** to also fetch + print the decompressed `data_json` for each matched row (it pulls the heavy payload per id, so use it only after narrowing to a few rows). `--data-json-truncate N` caps each value's length (default 300; 0 = no truncation). `--format json` emits rows as JSON.
   - Read-only: it only `load()`s against the `global` read-only cluster; it never writes.

4. **Interpret.** Confirm a matching deploy by `namespace` + `git_revision` (+ `tag`/`status`); line its `t_create` up against the incident timeline. **No matching row** is itself a finding (no deploy was triggered, or it ran under a different namespace).

## Notes

- The shared access logic lives in `learned/hebb_utils/deploy/build_log.py` (`build_filter` / `query_window` / `fetch_full`) so other skills/scripts reuse the same `global`-db read and the same no-`SELECT *` discipline.
- This is a **different** access path from `query-starrocks` (StarRocks warehouse) — `build_log` is a `BuildLog` DBLoader on the global MySQL cluster, loaded via `DBLoader.load(..., db='global')`.
</content>
