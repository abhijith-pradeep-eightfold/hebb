"""Fetch and analyze EC2 `CPUUtilization` from CloudWatch.

Deterministic shared logic (Rule A2) used by more than one skill:
  - `inspect-cloudwatch-cpu` (its `analyze_cpu_metrics.py` imports the analysis half);
  - `solr-shard-cpu` (its `shard_cpu.py` imports both the fetch and the analysis halves).

This module is itself www-free (it imports nothing from `$CODE_BASE`), but it lives
in the `hebb_utils` shared library alongside vscode-dependent modules. The library's
import root is `hebb_utils` (never `utils`) so it can be imported in the same process
as vscode code — vscode ships its own top-level `utils` package (`www/utils`), and
two top-level `utils` packages cannot coexist on one `sys.path`. See
learned/hebb_utils/README.md.

Two halves:
  - `fetch_cpu(...)`  — a read-only `aws cloudwatch get-metric-statistics` call,
    returning the parsed AWS JSON dict. Raises `CloudWatchError` on failure.
  - the analysis functions (`series_from_datapoints`, `load_series`,
    `contiguous_breaches`, `report`) — pure transforms over the datapoints that
    sort by timestamp, summarise min/max/mean, and flag breach blocks as
    `SUSTAINED` (>=5 contiguous buckets, i.e. it would clear the Solr alarm's
    5-of-6 rule) or `blip`.
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


def parse_ts(s):
    """Parse an AWS ISO-8601 timestamp (e.g. '2026-06-15T08:20:00Z' or with offset)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def series_from_datapoints(datapoints, stat):
    """Convert AWS `Datapoints` to a timestamp-sorted [(datetime, value)] list for `stat`."""
    rows = []
    for p in datapoints:
        ts = p.get("Timestamp")
        val = p.get(stat)
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
