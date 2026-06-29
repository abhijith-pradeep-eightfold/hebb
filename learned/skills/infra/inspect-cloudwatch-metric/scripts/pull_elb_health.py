#!/usr/bin/env python3
"""Pull an Elastic Beanstalk ALB health-check ("Host Unhealthy") metric-math alarm's two
host-count series and tabulate the breach signal e1 = UnHealthyHostCount - HealthyHostCount.

For a "Host Unhealthy" oncall (a CloudWatch metric-math alarm `e1 = m1 - m2 >= 0` where
m1 = AWS/ApplicationELB UnHealthyHostCount and m2 = HealthyHostCount, both dimensioned on
the EB ALB's TargetGroup + LoadBalancer): the breach signal is a DIFFERENCE, not a single
metric crossing a line, so this pulls BOTH series and merges them per bucket.

Two ways to supply the metric dimensions:
  - `--alarm-name "<env> Unhealthy (<region>)"` — reads the alarm via describe-alarms and
    self-resolves the TargetGroup + LoadBalancer dimensions and which metric is
    healthy/unhealthy from its `Metrics` array (the top-level MetricName/Namespace of a
    metric-math alarm are null). PREFERRED.
  - `--target-group "targetgroup/awseb-AWSEB-.../..." --load-balancer "app/awseb--.../..."`
    — pass the two dimension values directly (skip describe-alarms).

Read-only AWS CLI (region/profile from the environment); bundled so it runs unattended.
The merge + e1>=0 flagging lives in the shared `hebb_utils.aws.cloudwatch` module
(`report_health_diff`), shared with any other consumer of the ELB host-health shape.

Gate-passing invocation (no $CODE_BASE import needed; PYTHONPATH is harmless if set):

    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_elb_health.py" \
        --alarm-name "stage0-api5 Unhealthy (eu-central-1)" --region eu-central-1 \
        --start 2026-06-29T13:30:00Z --end 2026-06-29T15:30:00Z
"""
import argparse
import datetime as _dt
import os
import sys

# Import shared logic from learned/hebb_utils/ — walk up to the dir containing it.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.aws.cloudwatch import (  # noqa: E402
    CloudWatchError,
    describe_alarm,
    fetch_metric,
    metric_math_metrics,
    report_health_diff,
    series_from_datapoints,
)

_HEALTHY = "HealthyHostCount"
_UNHEALTHY = "UnHealthyHostCount"


def _resolve_dims_from_alarm(alarm_name, region):
    """Return (healthy_dims, unhealthy_dims, threshold) from the alarm, or (None, None, None)."""
    alarm = describe_alarm(alarm_name, region)
    if not alarm:
        return None, None, None
    metrics = metric_math_metrics(alarm)
    healthy = next((m for m in metrics if m["metric_name"] == _HEALTHY), None)
    unhealthy = next((m for m in metrics if m["metric_name"] == _UNHEALTHY), None)
    h_dims = healthy["dimensions"] if healthy else None
    u_dims = unhealthy["dimensions"] if unhealthy else None
    return h_dims, u_dims, alarm.get("Threshold")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pull EB ALB Healthy/UnHealthy host counts + flag e1 = UH - H >= 0.")
    ap.add_argument("--alarm-name", help="exact metric-math alarm name (self-resolves dims)")
    ap.add_argument("--target-group", help="TargetGroup dimension value (if no --alarm-name)")
    ap.add_argument("--load-balancer", help="LoadBalancer dimension value (if no --alarm-name)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then EF_DEFAULT_REGION, "
                         "then us-west-2). Pass explicitly for non-default regions.")
    ap.add_argument("--start", help="ISO8601 start (UTC). Default: 3h before end.")
    ap.add_argument("--end", help="ISO8601 end (UTC). Default: now.")
    ap.add_argument("--period", type=int, default=60,
                    help="bucket seconds (default 60 for shape; the alarm itself evaluates "
                         "Average over 300s with DatapointsToAlarm=3)")
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="breach threshold on e1 = UH - H (default 0.0, the alarm's e1 >= 0)")
    args = ap.parse_args(argv)
    region = (args.region or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION") or "us-west-2")

    end = args.end or _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.start:
        start = args.start
    else:
        end_dt = _dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
        start = (end_dt - _dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    threshold = args.threshold
    if args.alarm_name:
        h_dims, u_dims, alarm_thr = _resolve_dims_from_alarm(args.alarm_name, region)
        if h_dims is None or u_dims is None:
            print(f"error: could not resolve Healthy/UnHealthy metric dimensions from alarm "
                  f"{args.alarm_name!r}. Pass --target-group/--load-balancer explicitly.",
                  file=sys.stderr)
            return 1
        if alarm_thr is not None:
            threshold = float(alarm_thr)
    elif args.target_group and args.load_balancer:
        dims = [f"Name=TargetGroup,Value={args.target_group}",
                f"Name=LoadBalancer,Value={args.load_balancer}"]
        h_dims = u_dims = dims
    else:
        print("error: pass --alarm-name, or both --target-group and --load-balancer.",
              file=sys.stderr)
        return 2

    print(f"region   : {region}")
    print(f"window   : {start} .. {end}  (period {args.period}s, UTC)")
    print(f"breach   : e1 = UnHealthyHostCount - HealthyHostCount >= {threshold:g}")
    print(f"dims     : {' '.join(u_dims)}")
    print()

    try:
        h_doc = fetch_metric("AWS/ApplicationELB", _HEALTHY, h_dims, start, end, region,
                             period=args.period, statistics=("Average", "Maximum"))
        u_doc = fetch_metric("AWS/ApplicationELB", _UNHEALTHY, u_dims, start, end, region,
                             period=args.period, statistics=("Average", "Maximum"))
    except CloudWatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    healthy_rows = series_from_datapoints(h_doc.get("Datapoints", []), "Average")
    unhealthy_rows = series_from_datapoints(u_doc.get("Datapoints", []), "Average")
    report_health_diff("ELB host health (Average)", healthy_rows, unhealthy_rows, threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
