# build_log table (global db)

**Summary:** `build_log` is the table that stores **deployment records** — one row per build/deploy, with scalar status columns plus a heavy compressed `data_json` payload (status/release/lint/pytest logs). It is the **source of truth for "was a deploy actually triggered"** for a given app/revision. It lives on the **`global`** db (broadcast there; deployments propagate to all regions), is modelled by the `BuildLog` DBLoader, and is read via the standard `DBLoader.load(...)` path. A `SELECT *` over a multi-day window **times out** on the compressed `data_json` — select scalar columns only and push filters into SQL, then pull `data_json` for the one matched id.

## What it records, and where deploys surface

- One row per build/deploy: `id`, `namespace` (the app, e.g. `mcp`, `api`, `www_stage`), `t_create`, `git_revision`, `status`, `tag`, `release_branch`, `t_prod_deploy`, `prod_deploy_duration_sec`, and a compressed `data_json`.
- **Deployments propagate to all regions**, and deploy alerts land in the **`#build_alerts`** Slack channel (`slack_utils.Channels.BUILD_ALERTS`). This is the authoritative deploy-record path — not the per-app `azure-deployments` Slack notification that the Azure deploy script posts (`production/release/deploy_azure_server.py:72`), which is a separate, secondary feed and is easy to mistake for the deploy record.
- *anchor:* `www/internal/build_log.py:184` (`BUILD_ALERTS` channel).

### `status` and `tag` enums

- `BuildStatus`: `DEPLOYMENT_SUCCESS = 'Deployment Passed'`, `DEPLOYMENT_FAILURE = 'Deployment Failed'`, `BUILDING = 'Building'` (plus variants like `'Hotfix Deployment Passed'`). A row that is still `'Building'` has passed tests but not yet recorded a deploy outcome.
- `BuildLogTag`: skip / prod / canary / qa.
- *anchors:* `www/internal/build_log.py:62,68-69,63` (BuildStatus values), `:117-121` (BuildLogTag).

### `data_json` is compressed

`data_json` is **base64-compressed** and decompressed in `load_from_dict`. Observed keys: `Status Log`, `Release Log`, `Lint Log` ("Overall pylint status = …"), `Pytest Log` ("Overall pytest status: …"); the Release/Status logs name a GitHub compare (`<base>...<head>`) and a `build_log?namespace=<ns>` save link.
- *anchor:* `www/internal/build_log.py:156-159` (data_json decompress), `:134-145` (attrs).

## How to read it

`build_log` is registered in the table registry and loaded with the generic DBLoader:

- Registry: `'build_log': ('internal.build_log', 'BuildLog')`.
- `BuildLog` resolves to the **`global`** db — `get_default_db()` returns `'global'`. Pass `db='global'` explicitly. The read goes to the global read-only cluster (`global-database-cluster-1-cluster-1.cluster-ro-…`).
- Load via `BuildLog().load(filter_by=<dict>, order_by=..., limit=..., columns=<scalar cols>, db='global', return_dict=True)`.
- `filter_by` supports range and `LIKE` keys: `{'t_create>=': start, 't_create<': end}` and `{'<col> LIKE': '%mcp%'}`.
- *anchors:* `www/db/db_table_registry.py:44` (registry entry); `www/internal/build_log.py:147-148` (`get_default_db` → `'global'`); `www/db/db_loader.py:1208` (`load(... columns=, filter_by=)`); `www/db/db_query_builder.py:190` (the `'<col> LIKE'` filter key).

### Gotcha — `SELECT *` over a window times out

A `SELECT *` (or a `load` with no `columns`) over a multi-day ordered window **times out** (`pymysql OperationalError (2013) Lost connection … read operation timed out`) because it pulls the heavy compressed `data_json` for every row. The fix:

1. Select **scalar columns only** (`columns=['id','namespace','t_create','git_revision','status','tag','release_branch','t_prod_deploy','prod_deploy_duration_sec']`) and push `namespace LIKE` / `status LIKE` / `t_create` range filters into SQL via `filter_by`.
2. Then pull `data_json` only for the **single matched id** (`load(filter_by={'id': <id>}, db='global')`).

This is automated by the `query-build-log` skill, which bakes the scalar-cols + SQL-side-filter pattern into its bundled script.

## Related skills

- `query-build-log` — use it to read deployment records from `build_log` by `namespace` / `status` / `tag` / time window (scalar columns first, then `data_json` for a matched id), avoiding the `SELECT *` timeout. The building-block read for confirming whether a deploy was actually triggered.
- `oncall-airflow-dag-failure` — the Airflow DAG Failure runbook; it uses `build_log` (via `query-build-log`) to confirm the deploy a failing `deploy_to_azure` page ran from.

## Related

- [[../oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]] — uses `build_log` to confirm the deploy a failing `deploy_to_azure` page ran from.
- [[azure-app-deployer-resource-groups|Azure App Service deployer resource-group asymmetry]] — the deployer that consumes a build's revision; `build_log` is where you confirm which app/revision was deploying.
- [[../vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — `BuildLog` is `www`-rooted (`internal.build_log`); run with `PYTHONPATH=$CODE_BASE/www`.

---
*Sources:* witness `inputs/2026-06-30-airflow-dag-failure-deploy-to-azure.md` — `[10:42]` intervention: the deploy record is the `build_log` table in the global db (deploy info in `data_json`), alerts at `#build_alerts`, deployments propagate to all regions; `[10:48]` the `BuildLog` model, the `global`-db default, the scalar-cols + `LIKE` access, the `SELECT *` timeout dead-end and its fix, and the enum values. Anchors cited against `www/internal/build_log.py`, `www/db/db_table_registry.py`, `www/db/db_loader.py`, `www/db/db_query_builder.py`.
</content>
</invoke>
