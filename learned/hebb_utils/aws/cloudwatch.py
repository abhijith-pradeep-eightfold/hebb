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
  - the fetch functions — `fetch_cpu(...)` (the AWS/EC2 `CPUUtilization` special case)
    and `fetch_metric(...)` (generic over namespace/metric/dimensions, e.g. the
    `AWS/ApplicationELB` HealthyHostCount / UnHealthyHostCount series behind a "Host
    Unhealthy" metric-math alarm) — read-only `aws cloudwatch get-metric-statistics`
    calls returning the parsed AWS JSON dict. Raise `CloudWatchError` on failure.
  - the analysis functions (`series_from_datapoints`, `load_series`,
    `contiguous_breaches`, `report`, `report_buckets`) — pure transforms over the
    datapoints that sort by timestamp, summarise min/max/mean, flag breach blocks as
    `SUSTAINED` (>=5 contiguous buckets, i.e. it would clear the Solr alarm's
    5-of-6 rule) or `blip` (`report`), or print one row per bucket (`report_buckets`).
    `report_health_diff` is the two-series analog: it merges Healthy + UnHealthy host
    counts and flags the derived difference `e1 = UnHealthy - Healthy >= 0`.
"""
import json
import subprocess
import sys
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


def fetch_metric(namespace, metric_name, dimensions, start_time, end_time, region,
                 period=60, statistics=("Average",)):
    """Pull one CloudWatch metric's datapoints (read-only; generic over namespace/metric).

    `dimensions` is a list of "Name=...,Value=..." strings, one per CLI --dimensions
    token, e.g. ["Name=TargetGroup,Value=targetgroup/...", "Name=LoadBalancer,Value=app/..."].
    `start_time`/`end_time` are ISO-8601 UTC strings (e.g. '2026-06-29T14:00:00Z').
    Returns the parsed AWS JSON dict {"Label": ..., "Datapoints": [...]}. Raises
    CloudWatchError on failure. `fetch_cpu` is the AWS/EC2 CPUUtilization special case
    of this; this generic form backs the AWS/ApplicationELB HealthyHostCount /
    UnHealthyHostCount pulls behind a "Host Unhealthy" metric-math alarm.
    """
    cmd = [
        "aws", "cloudwatch", "get-metric-statistics",
        "--region", region,
        "--namespace", namespace,
        "--metric-name", metric_name,
        "--dimensions", *dimensions,
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
            f"get-metric-statistics could not run for {metric_name}: {exc}") from exc
    if result.returncode != 0:
        raise CloudWatchError(
            f"get-metric-statistics failed for {metric_name}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        raise CloudWatchError(
            f"could not parse get-metric-statistics output for {metric_name}: {exc}") from exc


def describe_alarm(alarm_name, region):
    """Return the first MetricAlarm dict for `alarm_name` (read-only), or None.

    For a metric-math alarm (e.g. a "Host Unhealthy" `UnHealthy - Healthy >= 0` alarm)
    the top-level MetricName/Namespace are null — the real metrics live in the returned
    dict's `Metrics` array; pass that dict to `metric_math_metrics` to extract them.
    Never raises — returns None (with a warning on stderr) so callers can fall back to
    explicit dimensions.
    """
    cmd = ["aws", "cloudwatch", "describe-alarms", "--region", region,
           "--alarm-names", alarm_name, "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: describe-alarms could not run for {alarm_name!r}: {exc}",
              file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  warning: describe-alarms failed for {alarm_name!r}: {result.stderr.strip()}",
              file=sys.stderr)
        return None
    try:
        alarms = (json.loads(result.stdout) or {}).get("MetricAlarms") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: could not parse describe-alarms output for {alarm_name!r}: {exc}",
              file=sys.stderr)
        return None
    return alarms[0] if alarms else None


def metric_math_metrics(alarm):
    """Extract the underlying metrics of a metric-math alarm's `Metrics` array.

    Returns a list of dicts (one per concrete MetricStat entry; pure-Expression entries
    like `e1 = m1 - m2` are skipped) with keys: `id`, `namespace`, `metric_name`,
    `dimensions` (a list of "Name=...,Value=..." strings ready for `fetch_metric`), and
    `stat`. Returns [] if `alarm` is falsy or has no Metrics array.
    """
    out = []
    for m in (alarm or {}).get("Metrics") or []:
        ms = m.get("MetricStat")
        if not ms:
            continue  # an Expression entry (e.g. the `e1 = m1 - m2` math) — no raw metric
        metric = ms.get("Metric") or {}
        dims = [f"Name={d.get('Name')},Value={d.get('Value')}"
                for d in metric.get("Dimensions") or []]
        out.append({
            "id": m.get("Id"),
            "namespace": metric.get("Namespace"),
            "metric_name": metric.get("MetricName"),
            "dimensions": dims,
            "stat": ms.get("Stat"),
        })
    return out


def describe_alarms_by_prefix(name_prefix, region):
    """Return the list of MetricAlarm dicts matching an alarm-name PREFIX (read-only).

    A single prefix (e.g. "[us-west-2] P1 Solr CPU Util Too High on profiles shard 21")
    can match multiple sibling alarms (one per replica), each carrying its own top-level
    `Dimensions` (the InstanceId) and `Threshold`. Never raises — returns [] (with a
    warning on stderr) on failure.
    """
    cmd = ["aws", "cloudwatch", "describe-alarms", "--region", region,
           "--alarm-name-prefix", name_prefix, "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: describe-alarms could not run for prefix {name_prefix!r}: {exc}",
              file=sys.stderr)
        return []
    if result.returncode != 0:
        print(f"  warning: describe-alarms failed for prefix {name_prefix!r}: "
              f"{result.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return (json.loads(result.stdout) or {}).get("MetricAlarms") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: could not parse describe-alarms output for {name_prefix!r}: {exc}",
              file=sys.stderr)
        return []


def instance_id_from_alarm(alarm):
    """Return the InstanceId from a (non-metric-math) EC2 alarm's top-level Dimensions, or None."""
    for d in (alarm or {}).get("Dimensions") or []:
        if d.get("Name") == "InstanceId":
            return d.get("Value")
    return None


def report_health_diff(label, healthy_rows, unhealthy_rows, threshold=0.0, sustained=3):
    """Print the merged Healthy/UnHealthy host-count curve and flag e1 = UH - H >= threshold.

    The analysis half of a "Host Unhealthy" (Elastic Beanstalk ELB health-check)
    metric-math alarm, whose breach signal is the DIFFERENCE
    `UnHealthyHostCount - HealthyHostCount >= 0`, not a single metric crossing a line.
    `healthy_rows`/`unhealthy_rows` are timestamp-sorted [(datetime, value)] lists (from
    `series_from_datapoints`). Buckets are merged on the timestamp union (a side missing
    at a timestamp counts as 0). Prints one row per bucket (Healthy, UnHealthy, e1,
    breach flag), the Healthy/UnHealthy ranges, the count of breach buckets
    (e1 >= threshold), and the contiguous breach block(s) tagged SUSTAINED (>= `sustained`
    buckets) or blip.

    Note: the live alarm evaluates Average over 300s periods with DatapointsToAlarm=3.
    When this is pulled at a finer --period (e.g. 60s) for shape, a sustained breach
    spans more buckets than 3; the block's start..end timestamps give the true duration.
    """
    print(f"=== {label} ===")
    H = {ts: v for ts, v in healthy_rows}
    U = {ts: v for ts, v in unhealthy_rows}
    times = sorted(set(H) | set(U))
    if not times:
        print("  (no datapoints — check target group / load balancer dimensions / window)")
        print()
        return
    print(f"  {'bucket (UTC)':<22}{'Healthy':>9}{'UnHealthy':>11}{'e1=UH-H':>10}  flag")
    print("  " + "-" * 56)
    diff_rows = []
    for ts in times:
        h = H.get(ts, 0.0)
        u = U.get(ts, 0.0)
        e1 = u - h
        diff_rows.append((ts, e1))
        flag = f"<<< breach (e1>={threshold:g})" if e1 >= threshold else ""
        print(f"  {ts.strftime('%Y-%m-%d %H:%M'):<22}{h:>9.2f}{u:>11.2f}{e1:>10.2f}  {flag}")
    print("  " + "-" * 56)
    hv = [v for _, v in healthy_rows] or [0.0]
    uv = [v for _, v in unhealthy_rows] or [0.0]
    n_breach = sum(1 for _, e in diff_rows if e >= threshold)
    print(f"  Healthy range  : {min(hv):.2f} .. {max(hv):.2f}")
    print(f"  UnHealthy range: {min(uv):.2f} .. {max(uv):.2f}")
    print(f"  buckets with e1 >= {threshold:g} (would feed ALARM): {n_breach} / {len(times)}")
    blocks = contiguous_breaches(diff_rows, threshold)
    if blocks:
        print(f"  contiguous e1 >= {threshold:g} block(s):")
        for start, end, count, peak in blocks:
            kind = "SUSTAINED" if count >= sustained else "blip"
            print(f"    {start.isoformat()} .. {end.isoformat()}  "
                  f"({count} bucket(s), peak e1={peak:.2f})  [{kind}]")
    else:
        print(f"  e1 never reached {threshold:g} — no breach in this window")
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
