"""Shared read access to the `build_log` table (the deployment record).

`build_log` lives on the **`global`** db and is modelled by the `BuildLog`
DBLoader (`www/internal/build_log.py`, registered in `www/db/db_table_registry.py`
as `'build_log': ('internal.build_log','BuildLog')`). It records one row per
build/deploy, with scalar status columns plus a heavy **base64-compressed**
`data_json` payload (decompressed in `BuildLog.load_from_dict`).

This module is **vscode-dependent**: it imports `internal.build_log.BuildLog`
at call time, so its callers must run with `PYTHONPATH=$CODE_BASE/www`.

The crux it encodes — **never `SELECT *` over a window.** A `load` with no
`columns` (i.e. all columns) over a multi-day ordered window times out
(`pymysql OperationalError (2013) ... read operation timed out`) because it
pulls the compressed `data_json` for every row. So `query_window` selects
**scalar columns only** and pushes `LIKE`/range filters into SQL; `fetch_full`
pulls `data_json` for a single id afterward.

See the wiki: learned/wiki/infra/build-log-table.md.
"""

# Scalar (cheap) columns — everything except the heavy compressed `data_json`.
SCALAR_COLS = [
    "id", "namespace", "t_create", "git_revision", "status", "tag",
    "release_branch", "t_prod_deploy", "prod_deploy_duration_sec",
]


def _build_log():
    # Deferred import: this is vscode-dependent (needs $CODE_BASE/www on the path).
    from internal.build_log import BuildLog
    return BuildLog()


def build_filter(namespace=None, status=None, tag=None, start=None, end=None):
    """Compose a `filter_by` dict for BuildLog.load from common predicates.

    `namespace`/`status`/`tag` are matched with a SQL `LIKE` (`%<v>%`) so a
    partial match works (e.g. status `'Deployment'` matches both
    `'Deployment Passed'` and `'Deployment Failed'`). `start`/`end` bound
    `t_create` (inclusive lower, exclusive upper). All are optional.
    """
    f = {}
    if namespace is not None:
        f["namespace LIKE"] = "%%%s%%" % namespace
    if status is not None:
        f["status LIKE"] = "%%%s%%" % status
    if tag is not None:
        f["tag LIKE"] = "%%%s%%" % tag
    if start is not None:
        f["t_create>="] = start
    if end is not None:
        f["t_create<"] = end
    return f


def query_window(filter_by, limit=100):
    """Return scalar-column dict rows matching `filter_by`, newest first.

    Selects SCALAR_COLS only (never the compressed `data_json`) so a windowed
    scan does not time out. Always reads the `global` db. Returns a list of
    dicts (possibly empty).
    """
    res = _build_log().load(
        filter_by=filter_by, order_by="t_create desc", limit=limit,
        columns=SCALAR_COLS, db="global", return_dict=True,
    )
    if isinstance(res, list):
        return res
    return [res] if res else []


def fetch_full(row_id):
    """Load one full BuildLog row by id (including decompressed `data_json`).

    Returns the `data_json` dict (empty dict if the row or its payload is
    absent). Pull this only for a single matched id — never for a whole window.
    """
    full = _build_log().load(filter_by={"id": row_id}, db="global")
    if not full:
        return {}
    return full.data_json or {}
