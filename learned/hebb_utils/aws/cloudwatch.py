"""Fetch and analyze EC2 `CPUUtilization` from CloudWatch.

Deterministic shared logic (Rule A2) used by more than one skill:
  - `inspect-cloudwatch-metric` (its `analyze_cpu_metrics.py` imports the analysis half);
  - `solr-shard-cpu` (its `shard_cpu.py` imports both the fetch and the analysis halves).

This module is itself www-free (it imports nothing from `$CODE_BASE`), but it lives
in the `hebb_utils` shared library alongside vscode-dependent modules. The library's
import root is `hebb_utils` (never `utils`) so it can be imported in the same process
as vscode code — vscode ships its own top-level `utils` package (`www/utils`), and
two top-level `utils` packages cannot coexist on one `sys.path`. See
learned/hebb_utils/README.md.

Two halves:
  - `fetch_cpu(...)`  — a read-only `aws cloudwatch get-metric-statistics` call for
    `AWS/EC2 CPUUtilization` (dimension `InstanceId`), returning the parsed AWS JSON
    dict. `fetch_rds_cpu(...)` is the `AWS/RDS CPUUtilization` variant (dimension
    `DBClusterIdentifier` + `Role`, extended statistic `p75`) used by the RDS-CPU
    oncall. Both raise `CloudWatchError` on failure.
  - the analysis functions (`series_from_datapoints`, `load_series`,
    `contiguous_breaches`, `report`, `report_buckets`) — pure transforms over the
    datapoints that sort by timestamp, summarise min/max/mean, flag breach blocks as
    `SUSTAINED` (>=5 contiguous buckets, i.e. it would clear the Solr alarm's
    5-of-6 rule) or `blip` (`report`), or print one row per bucket (`report_buckets`).
"""
import json
import subprocess
from datetime import datetime


class CloudWatchError(Exception):
    """A `get-metric-statistics` call failed; the message is user-facing."""


def fetch_cpu(instance_id, start_time, end_time, region,
              period=60, statistics=("Average", "Maximum")):
    """Pull CPUUtilization datapoints for one InstanceId (read-only).

    `start_time`/`end_time` are ISO-8601 UTC strings (e.g. '2026-06-26T09:50:00Z').
    Returns the parsed AWS JSON dict: {"Label": ..., "Datapoints": [...]}.
    Raises CloudWatchError if the CLI call fails or its output can't be parsed.
    """
    cmd = [
        "aws", "cloudwatch", "get-metric-statistics",
        "--region", region,
        "--namespace", "AWS/EC2",
        "--metric-name", "CPUUtilization",
        "--dimensions", f"Name=InstanceId,Value={instance_id}",
        "--start-time", start_time,
        "--end-time", end_time,
        "--period", str(period),
        "--statistics", *statistics,
        "--output", "json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001 — surface any spawn failure uniformly
        raise CloudWatchError(
            f"get-metric-statistics could not run for {instance_id}: {exc}") from exc
    if result.returncode != 0:
        raise CloudWatchError(
            f"get-metric-statistics failed for {instance_id}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"could not parse get-metric-statistics output for {instance_id}: {exc}") from exc


def fetch_rds_cpu(db_cluster_identifier, role, start_time, end_time, region,
                  period=60, extended_statistics=("p75",), statistics=("Maximum",)):
    """Pull `AWS/RDS CPUUtilization` datapoints for one cluster role (read-only).

    The RDS-CPU oncall alarm tracks a cluster role's CPU on the dimensions
    `DBClusterIdentifier` + `Role` (WRITER/READER), evaluated as an **extended
    statistic** (`p75`), not `Average` — see the wiki page `oncall/rds-cpu-high`.
    `role` is "WRITER" or "READER". `extended_statistics` (e.g. `p75`) come back
    under each datapoint's `ExtendedStatistics` dict; `statistics` (e.g. `Maximum`)
    come back as top-level keys. `start_time`/`end_time` are ISO-8601 UTC strings.
    Returns the parsed AWS JSON dict. Raises CloudWatchError on failure.

    Pass the GovCloud creds + `--region us-gov-west-1` (via the environment) for a
    gov alarm — see the wiki page `infra/govcloud-access`.
    """
    cmd = [
        "aws", "cloudwatch", "get-metric-statistics",
        "--region", region,
        "--namespace", "AWS/RDS",
        "--metric-name", "CPUUtilization",
        "--dimensions",
        f"Name=DBClusterIdentifier,Value={db_cluster_identifier}",
        f"Name=Role,Value={role}",
        "--start-time", start_time,
        "--end-time", end_time,
        "--period", str(period),
        "--output", "json",
    ]
    if statistics:
        cmd += ["--statistics", *statistics]
    if extended_statistics:
        cmd += ["--extended-statistics", *extended_statistics]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"get-metric-statistics could not run for {db_cluster_identifier}/{role}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise CloudWatchError(
            f"get-metric-statistics failed for {db_cluster_identifier}/{role}: "
            f"{result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"could not parse get-metric-statistics output for "
            f"{db_cluster_identifier}/{role}: {exc}") from exc


def fetch_metric_sum(namespace, metric_name, start_time, end_time, region,
                     dimensions=None, period=300, statistics=("Sum",)):
    """Pull a generic CloudWatch metric curve (read-only) for an arbitrary
    namespace / metric / dimensions / statistic.

    Unlike `fetch_cpu`/`fetch_rds_cpu` (which hardwire `AWS/EC2`/`AWS/RDS
    CPUUtilization`), this is the general `get-metric-statistics` call used for
    custom-namespace counter metrics such as the per-namespace Redis-errors counter
    (`namespace='ranking_service'`, `metric_name='prod-ranking-service-redis-errors.sum'`,
    `statistics=('Sum',)`, no dimensions) — see the wiki page `oncall/redis-errors-detected`.

    `dimensions` is an optional list of `(Name, Value)` tuples (omit for a metric
    with no dimensions, like the Redis-errors counter). `start_time`/`end_time` are
    ISO-8601 UTC strings. Returns the parsed AWS JSON dict. Raises CloudWatchError
    on failure.
    """
    cmd = [
        "aws", "cloudwatch", "get-metric-statistics",
        "--region", region,
        "--namespace", namespace,
        "--metric-name", metric_name,
        "--start-time", start_time,
        "--end-time", end_time,
        "--period", str(period),
        "--statistics", *statistics,
        "--output", "json",
    ]
    if dimensions:
        cmd += ["--dimensions"] + [f"Name={n},Value={v}" for n, v in dimensions]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"get-metric-statistics could not run for {namespace}/{metric_name}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise CloudWatchError(
            f"get-metric-statistics failed for {namespace}/{metric_name}: "
            f"{result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"could not parse get-metric-statistics output for "
            f"{namespace}/{metric_name}: {exc}") from exc


def parse_ts(s):
    """Parse an AWS ISO-8601 timestamp (e.g. '2026-06-15T08:20:00Z' or with offset)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _datapoint_value(p, stat):
    """Read `stat` from one AWS datapoint, supporting extended statistics.

    A standard statistic (`Average`, `Maximum`, …) is a top-level datapoint key.
    An **extended statistic** (a percentile such as `p75`, used by the RDS-CPU
    alarm) lives under the datapoint's `ExtendedStatistics` dict instead. Try the
    top level first, then the extended dict, so EC2 (`Average`) and RDS (`p75`)
    series both load through the same `series_from_datapoints`/`load_series` path.
    """
    if stat in p:
        return p[stat]
    ext = p.get("ExtendedStatistics")
    if isinstance(ext, dict) and stat in ext:
        return ext[stat]
    return None


def series_from_datapoints(datapoints, stat):
    """Convert AWS `Datapoints` to a timestamp-sorted [(datetime, value)] list for `stat`.

    `stat` may be a standard statistic (top-level key) or an extended statistic /
    percentile such as `p75` (read from the datapoint's `ExtendedStatistics`).
    """
    rows = []
    for p in datapoints:
        ts = p.get("Timestamp")
        val = _datapoint_value(p, stat)
        if ts is None or val is None:
            continue
        rows.append((parse_ts(ts), float(val)))
    rows.sort(key=lambda r: r[0])
    return rows


def load_series(path, stat):
    """Load a saved get-metric-statistics JSON file → (rows, label).

    `rows` is the sorted [(datetime, value)] list for `stat`; `label` is the AWS
    `Label` field if present (else None). Accepts either the full AWS dict or a
    bare list of datapoints.
    """
    with open(path) as fh:
        doc = json.load(fh)
    pts = doc.get("Datapoints", []) if isinstance(doc, dict) else list(doc)
    label = doc.get("Label") if isinstance(doc, dict) else None
    return series_from_datapoints(pts, stat), label


def contiguous_breaches(rows, threshold):
    """Return [(start_ts, end_ts, n, peak)] for runs of consecutive >= threshold buckets."""
    blocks = []
    run = []
    for ts, val in rows:
        if val >= threshold:
            run.append((ts, val))
        elif run:
            blocks.append(run)
            run = []
    if run:
        blocks.append(run)
    return [(b[0][0], b[-1][0], len(b), max(v for _, v in b)) for b in blocks]


def report(label, rows, threshold, stat):
    """Print the per-series breach report (min/max/mean, breach count, breach blocks)."""
    print(f"=== {label} ===")
    if not rows:
        print("  (no datapoints)")
        return
    vals = [v for _, v in rows]
    n = len(vals)
    n_breach = sum(1 for v in vals if v >= threshold)
    print(f"  {stat}: {n} buckets, span {rows[0][0].isoformat()} .. {rows[-1][0].isoformat()}")
    print(f"  min={min(vals):.1f}  max={max(vals):.1f}  mean={sum(vals)/n:.1f}")
    print(f"  buckets >= {threshold}: {n_breach}")
    blocks = contiguous_breaches(rows, threshold)
    if blocks:
        print(f"  contiguous >= {threshold} block(s):")
        for start, end, count, peak in blocks:
            kind = "SUSTAINED" if count >= 5 else "blip"
            print(f"    {start.isoformat()} .. {end.isoformat()}  "
                  f"({count} bucket(s), peak {peak:.1f})  [{kind}]")
    else:
        print(f"  no bucket reached {threshold}")
    print()


def report_buckets(label, series_by_stat, threshold, primary_stat="Average"):
    """Print **one row per bucket** for a CPU series — the per-bucket complement to
    `report()`'s aggregate summary.

    `series_by_stat` maps a statistic name (e.g. "Average", "Maximum") to its
    timestamp-sorted [(datetime, value)] list (from `series_from_datapoints`). One row
    is printed per bucket timestamp (the union across stats), with a column per stat
    and a breach `flag` when the `primary_stat` value is >= `threshold`, followed by a
    one-line summary over `primary_stat`. The CloudWatch alarm evaluates the
    **Average**, so `primary_stat` defaults to "Average" — a high per-minute Maximum
    alone is *not* a breach.
    """
    print(f"=== {label} ===")
    stats = list(series_by_stat.keys())
    maps = {s: dict(series_by_stat[s]) for s in stats}
    buckets = sorted(set().union(*[set(m) for m in maps.values()])) if maps else []
    if not buckets:
        print("  (no datapoints)")
        print()
        return
    print(f"  {'bucket_start_utc':<18}" + "".join(f"{s + '_%':>12}" for s in stats) + "   flag")
    for ts in buckets:
        cells = "".join(
            f"{(f'{maps[s][ts]:.2f}' if ts in maps[s] else '-'):>12}" for s in stats)
        pv = maps.get(primary_stat, {}).get(ts)
        flag = f">={threshold:g}" if (pv is not None and pv >= threshold) else ""
        print(f"  {ts.strftime('%Y-%m-%d %H:%M'):<18}{cells}   {flag}")
    pvals = [v for _, v in series_by_stat.get(primary_stat, [])]
    if pvals:
        n_breach = sum(1 for v in pvals if v >= threshold)
        print(f"  summary ({primary_stat}): {len(buckets)} buckets | "
              f"min={min(pvals):.2f} mean={sum(pvals) / len(pvals):.2f} max={max(pvals):.2f} | "
              f"buckets >= {threshold:g}: {n_breach}")
    print()
