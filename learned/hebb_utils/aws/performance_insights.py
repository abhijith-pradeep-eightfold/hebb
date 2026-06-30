"""Fetch and rank RDS Performance Insights `db.load.avg` breakdowns.

Deterministic shared logic (Rule A2) for the `query-rds-performance-insights` skill:
decompose a database instance's load (average active sessions, AAS) by a PI dimension
group (`db.wait_event`, `db.sql`, `db.user`, `db.host`) over a window and rank each
key by mean/peak AAS with its share of the grouped total. This is the analytical core
of an "RDS CPU too high" oncall — see the wiki page `infra/rds-performance-insights`.

This module is www-free (imports nothing from `$CODE_BASE`) but lives in `hebb_utils`
so it can share a process with vscode code if a caller needs it; the import root is
`hebb_utils` (never `utils`) to avoid colliding with vscode's own top-level `utils`.

Two halves:
  - `fetch_load(...)` — a read-only `aws pi get-resource-metrics` call for
    `db.load.avg`, optionally grouped by a dimension; returns the parsed AWS JSON.
    `fetch_sql_text(...)` — `aws pi get-dimension-key-details` for the full statement
    text behind a `db.sql.id` digest (db.sql group; aurora-mysql rejects
    db.sql_tokenized here). Both raise `PerfInsightsError` on failure.
  - `rank_metric_list(...)` / `format_ranking(...)` — pure transforms over the
    `MetricList` AWS returns: mean/peak AAS per key, sorted, with share of total.
"""
import json
import subprocess


class PerfInsightsError(Exception):
    """A Performance Insights CLI call failed; the message is user-facing."""


def _pi(args, what):
    try:
        result = subprocess.run(["aws", "pi"] + args,
                                capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        raise PerfInsightsError(f"`aws pi {what}` could not run: {exc}") from exc
    if result.returncode != 0:
        raise PerfInsightsError(
            f"`aws pi {what}` failed: {result.stderr.strip()[:400]}")
    try:
        return json.loads(result.stdout or "null")
    except Exception as exc:  # noqa: BLE001
        raise PerfInsightsError(
            f"could not parse `aws pi {what}` output: {exc}") from exc


def fetch_load(identifier, start_epoch, end_epoch, region,
               group=None, limit=10, period_seconds=300):
    """Pull `db.load.avg` for one RDS instance (read-only), optionally grouped.

    `identifier` is the instance's **DbiResourceId** (a `db-...` string, from
    `aws rds describe-db-instances`). `group` is a PI dimension group name
    (`db.wait_event`, `db.sql`, `db.user`, `db.host`); omit for the ungrouped total.
    `start_epoch`/`end_epoch` are **Unix epoch seconds** (PI does not take ISO times).
    Returns the parsed AWS JSON dict (its `MetricList` feeds `rank_metric_list`).
    """
    if group:
        metric_query = {"Metric": "db.load.avg",
                        "GroupBy": {"Group": group, "Limit": limit}}
    else:
        metric_query = {"Metric": "db.load.avg"}
    args = [
        "get-resource-metrics", "--region", region, "--service-type", "RDS",
        "--identifier", identifier,
        "--metric-queries", json.dumps([metric_query]),
        "--start-time", str(start_epoch), "--end-time", str(end_epoch),
        "--period-in-seconds", str(period_seconds), "--output", "json",
    ]
    return _pi(args, f"get-resource-metrics ({group or 'total'})")


def fetch_sql_text(identifier, sql_id, region):
    """Fetch the full statement text behind a `db.sql.id` digest (read-only).

    Uses the **`db.sql`** group — on aurora-mysql `get-dimension-key-details` rejects
    `db.sql_tokenized` (`InvalidArgumentException`), so group `db.load.avg` by `db.sql`
    (full statements with literals) to get the `db.sql.id`, then call this. Returns the
    parsed AWS JSON dict.
    """
    args = [
        "get-dimension-key-details", "--region", region, "--service-type", "RDS",
        "--identifier", identifier, "--group", "db.sql",
        "--group-identifier", sql_id, "--requested-dimensions", "statement",
        "--output", "json",
    ]
    return _pi(args, "get-dimension-key-details")


def rank_metric_list(doc):
    """Rank a PI get-resource-metrics result by mean db.load (AAS).

    Returns a list of (mean_aas, peak_aas, label) sorted by mean descending, plus the
    grouped total (excluding any TOTAL/ungrouped row) for computing shares. `label` is
    the dimension dict's values joined (or "TOTAL" for the ungrouped series).
    """
    rows = []
    for item in (doc or {}).get("MetricList", []):
        key = item.get("Key", {}) or {}
        dims = key.get("Dimensions") or {}
        label = dims if dims else "TOTAL"
        pts = [p["Value"] for p in item.get("DataPoints", []) if p.get("Value") is not None]
        mean = sum(pts) / len(pts) if pts else 0.0
        peak = max(pts) if pts else 0.0
        rows.append((mean, peak, label))
    rows.sort(key=lambda r: r[0], reverse=True)
    total = sum(r[0] for r in rows if r[2] != "TOTAL") or 1.0
    return rows, total


def format_ranking(rows, total):
    """Format the ranked rows as a printable table (mean/peak AAS + share + dimension)."""
    out = [f"{'mean_AAS':>9} {'peak_AAS':>9} {'share':>6}  dimension"]
    for mean, peak, label in rows:
        share = "" if label == "TOTAL" else f"{100 * mean / total:5.1f}%"
        if isinstance(label, dict):
            label = " | ".join(f"{k.split('.')[-1]}={v}" for k, v in label.items())
        label = (str(label)[:160] + "…") if len(str(label)) > 160 else label
        out.append(f"{mean:9.3f} {peak:9.3f} {share:>6}  {label}")
    return "\n".join(out)
