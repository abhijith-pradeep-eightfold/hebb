# Python import root ($CODE_BASE/www)

**Summary:** The `vscode` repo is not pip-installed, so scripts that import its source must put the repo on `PYTHONPATH`. For the large family of packages that live under `www/` (`datawarehouse`, `db`, …), the import root is **`$CODE_BASE/www`**, not `$CODE_BASE`.

## The gotcha

The codebase imports those packages as top-level names — `from datawarehouse.starrocks import starrocks_utils`, `from db.db_type import DBType` — but on disk they live under `www/`, not the repo root:

- `www/datawarehouse`, `www/db` exist.
- `datawarehouse/`, `db/` at the repo root do **not**.

So running a script with `PYTHONPATH="$CODE_BASE"` fails with `ModuleNotFoundError: No module named 'datawarehouse'`. The fix is to root the import path at `www`:

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" your_script.py
```

(Observed: first run with `PYTHONPATH=$CODE_BASE` raised `ModuleNotFoundError: No module named 'datawarehouse'`; re-running with `PYTHONPATH=$CODE_BASE/www` succeeded.)

## Relation to the env contract

The two env vars are `CODE_BASE` (repo root, `/home/ec2-user/vscode`) and `VSCODE_PYTHON` (the interpreter whose venv holds the deps). The general guidance says "run with the repo root on `PYTHONPATH`" — but for these `www`-rooted packages the correct root is `$CODE_BASE/www`. Use `$CODE_BASE/www` whenever the script imports `datawarehouse`, `db`, `cloud_interfaces`, or other `www`-level packages.

(Second confirming instance: a processor-trace script importing `db.base_log_event` and `cloud_interfaces.datawarehouse` first failed under `PYTHONPATH=$CODE_BASE` with `ModuleNotFoundError: No module named 'db'`, then succeeded under `$CODE_BASE/www` — `inputs/2026-06-26-smid-processor-trace.md`.)

## Installed in the venv

The `$VSCODE_PYTHON` venv also carries common third-party libraries beyond the repo's own packages. Observed available: **`matplotlib` 3.10.0** (with `matplotlib.dates`) — usable for headless plotting via the Agg backend (`matplotlib.use("Agg")`) to render query results to a PNG without a display. See [[../data-warehouse/querying-starrocks#plotting-the-result-to-a-png|Querying StarRocks → plotting]].

## Related skills

- `config-get` — use it to read a config value (`config.get(name, field_name=...)`); it runs with this `$CODE_BASE/www` import root.

## Related

- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — a concrete script that imports `datawarehouse.starrocks` and `db.db_type` and therefore needs `$CODE_BASE/www`.
- [[../infra/config-get|Reading a config value (`config.get`)]] — a config read that runs with this import root.

---
*Sources:* observed at runtime in `inputs/2026-06-24-starrocks-query-count.md`; package layout under `www/` in the `vscode` repo. matplotlib-in-venv observed in `inputs/2026-06-24-solr-query-buckets.md`.
