#!/usr/bin/env python3
"""Pull an SQS queue-depth CloudWatch metric and tabulate the spike vs the alarm threshold.

For a "Queue backed up" oncall: reads the (metric-math) alarm's threshold via
`describe-alarms`, then pulls `AWS/SQS ApproximateNumberOfMessagesVisible` (Maximum +
Average) for the queue via `get-metric-statistics`, prints the time-bucketed curve, and
flags buckets at/over the threshold. Shells out to the read-only AWS CLI (region/profile
from the environment). Bundled so it runs unattended.

Usage:
    pull_queue_depth.py --queue ai_interview_op_queue [--region us-west-2]
        [--start 2026-06-25T12:00:00Z] [--end 2026-06-26T02:00:00Z]
        [--period 900] [--alarm-prefix "[us-west-2] Queue backed up-ai_interview_op_queue"]
"""
from __future__ import absolute_import
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys


def _aws(args):
    """Run an aws CLI command, return parsed JSON stdout (or None on failure)."""
    try:
        out = subprocess.run(["aws"] + args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"warning: aws call failed: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"warning: aws {' '.join(args[:2])} exited {out.returncode}: "
              f"{out.stderr.strip()[:300]}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout or "null")
    except json.JSONDecodeError:
        return None


def _alarm_threshold(region, prefix):
    """Return (threshold, datapoints_to_alarm) for the queue alarm, or (None, None)."""
    data = _aws(["cloudwatch", "describe-alarms", "--region", region,
                 "--alarm-name-prefix", prefix,
                 "--query", "MetricAlarms[0].{T:Threshold,DP:DatapointsToAlarm,"
                            "Eval:EvaluationPeriods,State:StateValue}", "--output", "json"])
    if not data:
        return None, None
    return data.get("T"), data.get("DP")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pull SQS queue-depth metric + flag breaches.")
    ap.add_argument("--queue", required=True, help="SQS QueueName dimension value")
    ap.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
    ap.add_argument("--start", help="ISO8601 start (UTC). Default: 24h before end.")
    ap.add_argument("--end", help="ISO8601 end (UTC). Default: now.")
    ap.add_argument("--period", type=int, default=900, help="bucket seconds (default 900)")
    ap.add_argument("--alarm-prefix",
                    help="alarm name prefix (default '[<region>] Queue backed up-<queue>')")
    args = ap.parse_args(argv)

    end = args.end or _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.start:
        start = args.start
    else:
        end_dt = _dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
        start = (end_dt - _dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    prefix = args.alarm_prefix or f"[{args.region}] Queue backed up-{args.queue}"
    threshold, dp = _alarm_threshold(args.region, prefix)

    stats = _aws(["cloudwatch", "get-metric-statistics", "--region", args.region,
                  "--namespace", "AWS/SQS", "--metric-name", "ApproximateNumberOfMessagesVisible",
                  "--dimensions", f"Name=QueueName,Value={args.queue}",
                  "--start-time", start, "--end-time", end,
                  "--period", str(args.period), "--statistics", "Maximum", "Average",
                  "--query", "sort_by(Datapoints,&Timestamp)[].{t:Timestamp,max:Maximum,avg:Average}",
                  "--output", "json"])
    rows = stats or []

    print(f"queue={args.queue}  region={args.region}  period={args.period}s")
    print(f"window={start} -> {end} (UTC)")
    print(f"alarm threshold={threshold}  datapoints_to_alarm={dp}\n")
    if not rows:
        print("(no datapoints — check queue name / window / region)")
        return 0
    print(f"{'timestamp (UTC)':25s} {'max':>12s} {'avg':>12s}  breach")
    print("-" * 64)
    peak = 0.0
    for r in rows:
        mx = float(r.get("max") or 0)
        av = float(r.get("avg") or 0)
        peak = max(peak, mx)
        breach = "  <<<" if (threshold is not None and mx >= float(threshold)) else ""
        print(f"{str(r.get('t')):25s} {mx:>12.0f} {av:>12.0f}{breach}")
    print("-" * 64)
    over = f" ({peak/float(threshold)*100:.0f}% of threshold)" if threshold else ""
    print(f"peak max={peak:.0f}{over}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
