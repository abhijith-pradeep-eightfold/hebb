#!/usr/bin/env python3
"""Pull and analyze EC2 CPUUtilization from a CloudWatch alarm name-prefix or InstanceId(s).

The "from an alarm name / from a known InstanceId" entry to the CPU path of the
`inspect-cloudwatch-metric` skill, bundled so the read-only AWS calls run unattended
(the alarm-config read AND the metric pull live here, not as raw commands in SKILL.md):

  - `--alarm-name-prefix "<prefix>"` — `describe-alarms` resolves every matching sibling
    alarm (a prefix can match one per replica), each carrying its own InstanceId dimension,
    threshold, and current state; the CPU curve is then pulled and analyzed per instance.
  - `--instance-id i-... [--instance-id i-...]` — skip the alarm read; pull CPU directly
    (e.g. after a DNS->InstanceId resolution). Threshold defaults to 75 (the Solr alarm).

All reusable logic is imported in-process from the shared `hebb_utils.aws.cloudwatch`
module (`describe_alarms_by_prefix`, `instance_id_from_alarm`, `fetch_cpu`,
`series_from_datapoints`, `report`, `report_buckets`) — the same analysis half the
`solr-shard-cpu` skill uses. No `$CODE_BASE` import is needed.

Gate-passing invocation (PYTHONPATH is harmless if set):

    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_cpu.py" \
        --alarm-name-prefix "[us-west-2] P1 Solr CPU Util Too High on profiles shard 21" \
        --region us-west-2 --start 2026-06-15T06:00:00Z --end 2026-06-15T12:00:00Z
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
    describe_alarms_by_prefix,
    fetch_cpu,
    instance_id_from_alarm,
    report,
    report_buckets,
    series_from_datapoints,
)


def _emit(label, instance_id, start, end, region, period, threshold, per_bucket):
    """Pull one InstanceId's CPU and print the breach report. Returns 0/1 exit contribution."""
    if not instance_id:
        print(f"=== {label} ===")
        print("  no InstanceId — cannot pull CPU.\n")
        return 1
    try:
        doc = fetch_cpu(instance_id, start, end, region, period=period,
                        statistics=("Average", "Maximum"))
    except CloudWatchError as exc:
        print(f"=== {label} ===\n  CPU fetch failed: {exc}\n")
        return 1
    datapoints = doc.get("Datapoints", []) if isinstance(doc, dict) else []
    if per_bucket:
        series_by_stat = {s: series_from_datapoints(datapoints, s) for s in ("Average", "Maximum")}
        report_buckets(label, series_by_stat, threshold)
    else:
        for stat in ("Average", "Maximum"):
            report(f"{label} — {stat}", series_from_datapoints(datapoints, stat), threshold, stat)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pull EC2 CPUUtilization from an alarm prefix or InstanceId(s) + flag breaches.")
    ap.add_argument("--alarm-name-prefix", help="resolve matching alarms -> InstanceId(s)+threshold")
    ap.add_argument("--instance-id", action="append", default=[],
                    help="InstanceId to pull directly (repeatable; skips the alarm read)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then EF_DEFAULT_REGION, "
                         "then us-west-2). Pass explicitly for non-default regions.")
    ap.add_argument("--start", help="ISO8601 start (UTC). Default: 3h before end.")
    ap.add_argument("--end", help="ISO8601 end (UTC). Default: now.")
    ap.add_argument("--period", type=int, default=60, help="bucket seconds (default 60)")
    ap.add_argument("--threshold", type=float, default=75.0,
                    help="breach threshold on Average (default 75.0; overridden by the alarm's "
                         "own threshold when --alarm-name-prefix is used)")
    ap.add_argument("--per-bucket", action="store_true",
                    help="one row per period (Average, Maximum, breach flag) vs the aggregate summary")
    args = ap.parse_args(argv)
    region = (args.region or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION") or "us-west-2")

    end = args.end or _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.start:
        start = args.start
    else:
        end_dt = _dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
        start = (end_dt - _dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"region : {region}")
    print(f"window : {start} .. {end}  (period {args.period}s, UTC)")

    rc = 0
    if args.alarm_name_prefix:
        alarms = describe_alarms_by_prefix(args.alarm_name_prefix, region)
        if not alarms:
            print(f"\nerror: no alarms match prefix {args.alarm_name_prefix!r}. "
                  f"Pass --instance-id to pull CPU directly.", file=sys.stderr)
            return 1
        print(f"alarms : {len(alarms)} matching prefix\n")
        for a in alarms:
            iid = instance_id_from_alarm(a)
            thr = a.get("Threshold")
            thr = float(thr) if thr is not None else args.threshold
            label = f"{a.get('AlarmName')}  [{iid}]  state={a.get('StateValue')}"
            print(f"--- {a.get('AlarmName')} ---")
            print(f"    state={a.get('StateValue')}  threshold={thr}  "
                  f"reason={str(a.get('StateReason') or '')[:120]}")
            rc |= _emit(label, iid, start, end, region, args.period, thr, args.per_bucket)
    elif args.instance_id:
        print(f"instances: {', '.join(args.instance_id)}\n")
        for iid in args.instance_id:
            rc |= _emit(f"instance {iid}", iid, start, end, region, args.period,
                        args.threshold, args.per_bucket)
    else:
        print("\nerror: pass --alarm-name-prefix or at least one --instance-id.", file=sys.stderr)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
