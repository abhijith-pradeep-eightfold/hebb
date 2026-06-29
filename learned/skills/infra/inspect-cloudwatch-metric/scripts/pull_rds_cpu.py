#!/usr/bin/env python3
"""Pull an `AWS/RDS CPUUtilization` curve for a cluster role (or both roles) and
tabulate it against the RDS-CPU alarm threshold.

For an "RDS CPU Utilization Too High" oncall: the alarm is on `AWS/RDS
CPUUtilization` over the dimensions `DBClusterIdentifier` + `Role` (WRITER/READER),
evaluated as the extended statistic **p75** (not Average), threshold 90% / 8-of-8 /
60s. This script fetches the p75 (+ Maximum) curve for the requested role(s) over a
window and prints a per-series breach report — pulling **both** WRITER and READER by
default so a cluster-wide rise (both roles up) separates from a writer-only rise. See
the wiki page `oncall/rds-cpu-high`.

Shells out to the read-only AWS CLI; region/credentials come from the environment.
For a **GovCloud** alarm, export the GOV creds and pass `--region us-gov-west-1`
(see the wiki page `infra/govcloud-access`):

    export AWS_ACCESS_KEY_ID="$GOV_AWS_ACCESS_KEY_ID" \
           AWS_SECRET_ACCESS_KEY="$GOV_AWS_SECRET_ACCESS_KEY"

This is bundled under the skill dir so the bash execution policy auto-allows the
clean invocation and it runs unattended (the AWS reads are read-only telemetry).

Usage (the gate-passing shape — never hardcode the interpreter):
    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_rds_cpu.py" \
        --cluster shared-log-cluster-0-mysql57 --region us-gov-west-1 \
        --start 2026-06-29T18:30:00Z --end 2026-06-29T20:00:00Z
        [--role WRITER] [--threshold 90] [--stat p75] [--period 60]
"""
import argparse
import os
import sys

# Import the shared analysis + fetch logic from learned/hebb_utils/. Walk up to the
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
    CloudWatchError, fetch_rds_cpu, series_from_datapoints, report,
)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pull AWS/RDS CPUUtilization (p75) for a cluster role and flag breaches.")
    ap.add_argument("--cluster", required=True,
                    help="DBClusterIdentifier (e.g. shared-log-cluster-0-mysql57)")
    ap.add_argument("--role", choices=["WRITER", "READER", "BOTH"], default="BOTH",
                    help="cluster role to pull (default BOTH — pulls WRITER and READER)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then "
                         "EF_DEFAULT_REGION, then us-west-2). e.g. us-gov-west-1")
    ap.add_argument("--start", required=True, help="ISO8601 start (UTC)")
    ap.add_argument("--end", required=True, help="ISO8601 end (UTC)")
    ap.add_argument("--period", type=int, default=60, help="bucket seconds (default 60)")
    ap.add_argument("--threshold", type=float, default=90.0,
                    help="breach threshold (default 90.0, the RDS-CPU alarm threshold)")
    ap.add_argument("--stat", default="p75",
                    help="evaluated statistic (default p75 — the RDS-CPU alarm statistic)")
    args = ap.parse_args(argv)

    region = (args.region
              or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION")
              or "us-west-2")
    roles = ["WRITER", "READER"] if args.role == "BOTH" else [args.role]

    print(f"cluster={args.cluster}  region={region}  period={args.period}s  "
          f"stat={args.stat}  threshold={args.threshold:g}")
    print(f"window={args.start} -> {args.end} (UTC)\n")

    for role in roles:
        try:
            doc = fetch_rds_cpu(
                args.cluster, role, args.start, args.end, region,
                period=args.period, extended_statistics=(args.stat,),
                statistics=("Maximum",))
        except CloudWatchError as exc:
            print(f"=== {args.cluster} / {role} ===\n  ERROR: {exc}\n", file=sys.stderr)
            continue
        rows = series_from_datapoints(doc.get("Datapoints", []), args.stat)
        report(f"{args.cluster} / {role} ({args.stat})", rows, args.threshold, args.stat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
