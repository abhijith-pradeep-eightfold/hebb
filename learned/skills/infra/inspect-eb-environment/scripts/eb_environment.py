#!/usr/bin/env python3
"""Resolve the host(s) behind an Elastic Beanstalk ALB target group and pull the EB
environment event stream — the two evidence sources for a "Host Unhealthy" oncall page.

Given a "Host Unhealthy" metric-math alarm name (or a target-group name directly) and a
UTC window, this runs the deterministic chain (no judgment between steps — Rule A2):

    alarm/target-group  ->  TargetGroupArn  ->  InstanceId(s) + health
                        ->  instance type / launch time / EB env tags
                        ->  (environment-id)  ->  EB environment events

and prints (1) a host table — instance type, launch time (a launch inside the incident
window => an instance replacement), health state, EB environment name/id, ASG — and (2)
the EB environment event timeline (config update -> rolling replacement -> recovery, or
the fault events). The agent reads these to judge churn-vs-fault; this script makes no
judgment. All reusable logic is imported in-process from the shared `hebb_utils` library.

Read-only AWS CLI (region/profile from the environment); bundled so it runs unattended.

Gate-passing invocation (www-free; PYTHONPATH is harmless if set):

    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/eb_environment.py" \
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
from hebb_utils.aws.cloudwatch import describe_alarm, metric_math_metrics  # noqa: E402
from hebb_utils.aws import elastic_beanstalk as eb  # noqa: E402


def _tg_name_from_alarm(alarm_name, region):
    """Resolve the TargetGroup NAME from a metric-math alarm's Metrics array, or None."""
    alarm = describe_alarm(alarm_name, region)
    for m in metric_math_metrics(alarm):
        for dim in m["dimensions"]:
            # dim is "Name=TargetGroup,Value=targetgroup/<name>/<id>"
            if dim.startswith("Name=TargetGroup,Value="):
                return eb.target_group_name_from_dimension(dim.split("Value=", 1)[1])
    return None


def _within(launch_time, start, end):
    """True if an AWS LaunchTime ISO string falls within [start, end] (best-effort)."""
    try:
        lt = _dt.datetime.strptime(launch_time[:19], "%Y-%m-%dT%H:%M:%S")
        s = _dt.datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S")
        e = _dt.datetime.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")
        return s <= lt <= e
    except (ValueError, TypeError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve EB hosts behind a target group + pull the EB event stream.")
    ap.add_argument("--alarm-name", help="exact 'Host Unhealthy' alarm name (self-resolves TG)")
    ap.add_argument("--target-group-name",
                    help="target-group NAME (e.g. awseb-AWSEB-...) if no --alarm-name")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then EF_DEFAULT_REGION, "
                         "then us-west-2). Pass explicitly for non-default regions.")
    ap.add_argument("--start", help="ISO8601 start (UTC) for EB events. Default: 3h before end.")
    ap.add_argument("--end", help="ISO8601 end (UTC) for EB events. Default: now.")
    args = ap.parse_args(argv)
    region = (args.region or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION") or "us-west-2")

    end = args.end or _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.start:
        start = args.start
    else:
        end_dt = _dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
        start = (end_dt - _dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.target_group_name:
        tg_name = args.target_group_name
    elif args.alarm_name:
        tg_name = _tg_name_from_alarm(args.alarm_name, region)
        if not tg_name:
            print(f"error: could not resolve a TargetGroup from alarm {args.alarm_name!r}. "
                  f"Pass --target-group-name explicitly.", file=sys.stderr)
            return 1
    else:
        print("error: pass --alarm-name or --target-group-name.", file=sys.stderr)
        return 2

    print(f"region       : {region}")
    print(f"target group : {tg_name}")
    print(f"event window : {start} .. {end}  (UTC)")
    print()

    arn = eb.target_group_arn(tg_name, region)
    if not arn:
        print(f"error: could not resolve TargetGroupArn for {tg_name!r}.", file=sys.stderr)
        return 1
    targets = eb.targets_for_arn(arn, region)
    health_by_id = {t["instance_id"]: t for t in targets}
    details = eb.instance_details([t["instance_id"] for t in targets], region)

    # Host table.
    print("=== hosts behind the target group ===")
    if not details:
        print("  (no instances registered — target group is empty)")
    env_id = None
    for d in details:
        envinfo = eb.eb_env_from_tags(d["tags"])
        env_id = env_id or envinfo["environment_id"]
        lt = str(d["launch_time"])
        repl = "  <-- launched in window (replacement)" if _within(lt, start, end) else ""
        th = health_by_id.get(d["instance_id"], {})
        print(f"  instance     : {d['instance_id']}")
        print(f"    type       : {d['instance_type']}")
        print(f"    launch     : {lt}{repl}")
        print(f"    ec2 state  : {d['state']}    target health: {th.get('state')}"
              f" ({th.get('reason')})")
        print(f"    eb env     : {envinfo['environment_name']}  (id {envinfo['environment_id']})")
        print(f"    asg        : {envinfo['asg']}")
    print()

    # EB environment event timeline.
    print("=== EB environment events (newest first) ===")
    if not env_id:
        print("  (no elasticbeanstalk:environment-id tag found — cannot pull EB events)")
        return 0
    events = eb.eb_events(env_id, start, end, region)
    if not events:
        print(f"  (no events for {env_id} in the window)")
        return 0
    for ev in events:
        print(f"  {str(ev['date']):26s} {str(ev['severity'] or ''):6s} {ev['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
