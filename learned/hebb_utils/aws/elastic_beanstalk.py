"""Resolve the hosts behind an Elastic Beanstalk ALB target group and pull the EB
environment event stream — the two evidence sources for a "Host Unhealthy" oncall page.

Read-only `aws elbv2` / `aws ec2` / `aws elasticbeanstalk` describe calls only. Like
`hebb_utils.aws.ec2`, this module is www-free (no `$CODE_BASE` import) but lives under
`hebb_utils` so the whole shared library has one collision-free import root.

The deterministic chain (each call's input is the prior call's output, no judgment
between them — Rule A2) is:

    target-group name  ->  TargetGroupArn  ->  InstanceId(s) + health
                       ->  instance type / launch time / EB env tags
                       ->  (environment-id)  ->  EB environment events

Every function degrades gracefully (returns None / [] with a stderr warning) rather than
raising, so a partial failure still yields a usable report.
"""
import json
import subprocess
import sys


def _aws(args):
    """Run a read-only aws CLI command; return parsed JSON (or None on any failure)."""
    try:
        out = subprocess.run(["aws"] + args, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001 — any spawn failure degrades to None
        print(f"  warning: aws {' '.join(args[:2])} could not run: {exc}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"  warning: aws {' '.join(args[:2])} failed: {out.stderr.strip()[:300]}",
              file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout or "null")
    except json.JSONDecodeError as exc:
        print(f"  warning: could not parse aws {' '.join(args[:2])} output: {exc}",
              file=sys.stderr)
        return None


def target_group_name_from_dimension(dim_value):
    """Reduce a CloudWatch `TargetGroup` dimension value to the name `describe-target-groups`
    wants. The dimension is `targetgroup/<name>/<id>`; `--names` takes just `<name>`."""
    if not dim_value:
        return None
    parts = dim_value.split("/")
    # ['targetgroup', '<name>', '<id>'] -> '<name>'
    return parts[1] if len(parts) >= 2 and parts[0] == "targetgroup" else dim_value


def target_group_arn(tg_name, region):
    """Resolve a target-group NAME to its ARN via describe-target-groups, or None."""
    data = _aws(["elbv2", "describe-target-groups", "--region", region,
                 "--names", tg_name,
                 "--query", "TargetGroups[0].TargetGroupArn", "--output", "json"])
    return data if isinstance(data, str) else None


def targets_for_arn(tg_arn, region):
    """Return [{instance_id, state, reason}] for the targets in a target group."""
    data = _aws(["elbv2", "describe-target-health", "--region", region,
                 "--target-group-arn", tg_arn, "--output", "json"])
    out = []
    for d in (data or {}).get("TargetHealthDescriptions") or []:
        out.append({
            "instance_id": (d.get("Target") or {}).get("Id"),
            "state": (d.get("TargetHealth") or {}).get("State"),
            "reason": (d.get("TargetHealth") or {}).get("Reason"),
        })
    return out


def instance_details(instance_ids, region):
    """Return [{instance_id, instance_type, launch_time, state, tags}] for the instances.

    `tags` is a {Key: Value} dict (so callers can read `elasticbeanstalk:environment-id`
    etc.). Returns [] if the list is empty or the call fails.
    """
    ids = [i for i in (instance_ids or []) if i]
    if not ids:
        return []
    data = _aws(["ec2", "describe-instances", "--region", region,
                 "--instance-ids", *ids, "--output", "json"])
    out = []
    for res in (data or {}).get("Reservations") or []:
        for inst in res.get("Instances") or []:
            tags = {t.get("Key"): t.get("Value") for t in inst.get("Tags") or []}
            out.append({
                "instance_id": inst.get("InstanceId"),
                "instance_type": inst.get("InstanceType"),
                "launch_time": inst.get("LaunchTime"),
                "state": (inst.get("State") or {}).get("Name"),
                "tags": tags,
            })
    return out


def eb_env_from_tags(tags):
    """Pull the Elastic Beanstalk environment name/id + ASG from an instance's tag dict."""
    tags = tags or {}
    return {
        "environment_name": tags.get("elasticbeanstalk:environment-name") or tags.get("Name"),
        "environment_id": tags.get("elasticbeanstalk:environment-id"),
        "asg": tags.get("aws:autoscaling:groupName"),
    }


def eb_events(environment_id, start_time, end_time, region):
    """Return [{date, severity, message}] for an EB environment's events in the window.

    `start_time`/`end_time` are ISO-8601 UTC strings. Newest-first as AWS returns them.
    """
    if not environment_id:
        return []
    data = _aws(["elasticbeanstalk", "describe-events", "--region", region,
                 "--environment-id", environment_id,
                 "--start-time", start_time, "--end-time", end_time, "--output", "json"])
    out = []
    for ev in (data or {}).get("Events") or []:
        out.append({
            "date": ev.get("EventDate"),
            "severity": ev.get("Severity"),
            "message": ev.get("Message"),
        })
    return out
