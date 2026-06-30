#!/usr/bin/env python3
"""Pull a generic CloudWatch counter metric curve (Sum per bucket) and tabulate it.

For a custom-namespace counter alarm — e.g. the per-namespace Redis-errors counter
behind a "Redis Errors Detected" page (`--namespace ranking_service
--metric-name prod-ranking-service-redis-errors.sum --stat Sum`, no dimensions,
threshold 100, 300s period) — see the wiki page `oncall/redis-errors-detected`.

Unlike `pull_rds_cpu.py`/`analyze_cpu_metrics.py` (which hardwire the CPU metric),
this takes an arbitrary namespace / metric name / optional dimensions / statistic,
so it characterizes any counter or gauge metric. It prints one row per bucket (so a
single-bucket burst decaying to a trickle is visible) plus the aggregate breach
report (min/max/mean, buckets over threshold, contiguous breach blocks).

Shells out to the read-only AWS CLI; region/credentials come from the environment.
Bundled under the skill dir so the bash execution policy auto-allows the clean
invocation and it runs unattended (the AWS read is read-only telemetry).

Usage (the gate-passing shape — never hardcode the interpreter):
    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_metric_sum.py" \
        --namespace ranking_service \
        --metric-name prod-ranking-service-redis-errors.sum \
        --region eu-central-1 \
        --start 2026-06-29T18:00:00Z --end 2026-06-29T21:00:00Z
        [--stat Sum] [--period 300] [--threshold 100]
        [--dimension Name=X,Value=Y ...]
"""
import argparse
import os
import sys

# Import the shared fetch + analysis logic from learned/hebb_utils/. Walk up to the
# dir that contains `hebb_utils/` (i.e. learned/) and put it on sys.path — no
# hardcoded depth. `hebb_utils` never clashes with vscode's own top-level `utils`.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.aws.cloudwatch import (  # noqa: E402
    CloudWatchError, fetch_metric_sum, series_from_datapoints, report,
)


def _parse_dimension(s):
    """Parse a 'Name=X,Value=Y' argument into a (name, value) tuple."""
    name = value = None
    for part in s.split(","):
        k, _, v = part.partition("=")
        if k.strip() == "Name":
            name = v.strip()
        elif k.strip() == "Value":
            value = v.strip()
    if not name or value is None:
        raise argparse.ArgumentTypeError(
            f"--dimension must be 'Name=X,Value=Y', got {s!r}")
    return (name, value)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pull a CloudWatch counter metric (Sum per bucket) and flag breaches.")
    ap.add_argument("--namespace", required=True,
                    help="metric namespace (e.g. ranking_service — NOT an AWS/* namespace "
                         "for the Redis-errors counter)")
    ap.add_argument("--metric-name", required=True,
                    help="metric name (e.g. prod-ranking-service-redis-errors.sum)")
    ap.add_argument("--dimension", action="append", type=_parse_dimension, default=None,
                    help="optional 'Name=X,Value=Y' dimension (repeatable; omit for a "
                         "metric with no dimensions, like the Redis-errors counter)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then "
                         "EF_DEFAULT_REGION, then us-west-2). e.g. eu-central-1")
    ap.add_argument("--start", required=True, help="ISO8601 start (UTC)")
    ap.add_argument("--end", required=True, help="ISO8601 end (UTC)")
    ap.add_argument("--period", type=int, default=300, help="bucket seconds (default 300)")
    ap.add_argument("--threshold", type=float, default=100.0,
                    help="breach threshold (default 100.0, the Redis-errors alarm threshold)")
    ap.add_argument("--stat", default="Sum",
                    help="evaluated statistic (default Sum — the Redis-errors alarm statistic)")
    args = ap.parse_args(argv)

    region = (args.region
              or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION")
              or "us-west-2")

    dims = args.dimension or []
    dim_str = " ".join(f"{n}={v}" for n, v in dims) or "(none)"
    print(f"namespace={args.namespace}  metric={args.metric_name}  region={region}")
    print(f"period={args.period}s  stat={args.stat}  threshold={args.threshold:g}  "
          f"dimensions={dim_str}")
    print(f"window={args.start} -> {args.end} (UTC)\n")

    try:
        doc = fetch_metric_sum(
            args.namespace, args.metric_name, args.start, args.end, region,
            dimensions=dims, period=args.period, statistics=(args.stat,))
    except CloudWatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rows = series_from_datapoints(doc.get("Datapoints", []), args.stat)

    # Per-bucket curve, so a single-bucket burst decaying to a trickle is visible.
    print(f"=== {args.metric_name} ({args.stat}) per-bucket ===")
    if not rows:
        print("  (no datapoints — for a notBreaching counter, near-zero baseline "
              "means no datapoints outside an error burst)")
    else:
        for ts, val in rows:
            flag = f">{args.threshold:g}" if val > args.threshold else ""
            print(f"  {ts.strftime('%Y-%m-%d %H:%M')}  {val:>10.1f}   {flag}")
    print()

    # Aggregate breach report (reuses the shared report()).
    report(f"{args.metric_name} ({args.stat})", rows, args.threshold, args.stat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
