#!/usr/bin/env python3
"""Decompose an RDS instance's load (Performance Insights `db.load.avg` = average
active sessions, AAS) by a dimension over a window, and rank each key by mean/peak
AAS with its share — the analytical core of an "RDS CPU too high" oncall.

By default it pulls the four diagnostic breakdowns (`db.wait_event`, `db.sql`,
`db.user`, `db.host`) plus the ungrouped total, so a single call surfaces the
write-storm signature (redo_log_flush + COMMIT dominant, single-row INSERTs, spread
across hosts). Pass `--group` to pull one dimension, or `--sql-id` to fetch the full
statement text behind a `db.sql.id` digest. See the wiki page
`infra/rds-performance-insights`.

The AWS reads are read-only telemetry; bundled under the skill dir so the bash
execution policy auto-allows the clean invocation and it runs unattended. For a
**GovCloud** instance export the GOV creds and pass `--region us-gov-west-1` (see
the wiki page `infra/govcloud-access`):
    export AWS_ACCESS_KEY_ID="$GOV_AWS_ACCESS_KEY_ID" \
           AWS_SECRET_ACCESS_KEY="$GOV_AWS_SECRET_ACCESS_KEY"

Usage (the gate-passing shape — never hardcode the interpreter):
    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pi_load_split.py" \
        --identifier db-XXXX --region us-gov-west-1 \
        --start 1782760200 --end 1782763200
        [--group db.wait_event] [--period 300] [--limit 10]
        [--sql-id <db.sql.id>]

`--start`/`--end` are Unix epoch seconds (PI does not accept ISO times). Convert with
e.g. `date -u -d '2026-06-29 19:10:00' +%s`.
"""
import argparse
import os
import sys

# Import the shared PI logic from learned/hebb_utils/. Walk up to the dir that
# contains `hebb_utils/` (i.e. learned/) — no hardcoded depth.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.aws.performance_insights import (  # noqa: E402
    PerfInsightsError, fetch_load, fetch_sql_text, rank_metric_list, format_ranking,
)

_DEFAULT_GROUPS = ["db.wait_event", "db.sql", "db.user", "db.host"]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rank RDS Performance Insights db.load.avg breakdowns by AAS.")
    ap.add_argument("--identifier", required=True,
                    help="instance DbiResourceId (db-..., from describe-db-instances)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_DEFAULT_REGION env, then "
                         "EF_DEFAULT_REGION, then us-west-2). e.g. us-gov-west-1")
    ap.add_argument("--start", required=True, type=int, help="start time, Unix epoch seconds")
    ap.add_argument("--end", required=True, type=int, help="end time, Unix epoch seconds")
    ap.add_argument("--period", type=int, default=300, help="bucket seconds (default 300)")
    ap.add_argument("--limit", type=int, default=10, help="top-N keys per group (default 10)")
    ap.add_argument("--group", default=None,
                    help="one PI dimension group (db.wait_event|db.sql|db.user|db.host); "
                         "default pulls all four + the ungrouped total")
    ap.add_argument("--sql-id", default=None,
                    help="fetch the full statement text for this db.sql.id digest instead "
                         "of ranking a breakdown")
    args = ap.parse_args(argv)

    region = (args.region
              or os.environ.get("AWS_DEFAULT_REGION")
              or os.environ.get("EF_DEFAULT_REGION")
              or "us-west-2")

    if args.sql_id:
        try:
            doc = fetch_sql_text(args.identifier, args.sql_id, region)
        except PerfInsightsError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        for kd in (doc or {}).get("Dimensions", []) or []:
            print(kd.get("Value", ""))
        # fall back to dumping the dict when the shape is unexpected
        if not (doc or {}).get("Dimensions"):
            import json
            print(json.dumps(doc, indent=2))
        return 0

    print(f"identifier={args.identifier}  region={region}  period={args.period}s")
    print(f"window(epoch)={args.start} -> {args.end}\n")

    # ungrouped total first (AAS to read against the instance vCPU count)
    try:
        total_doc = fetch_load(args.identifier, args.start, args.end, region,
                               group=None, period_seconds=args.period)
        rows, _ = rank_metric_list(total_doc)
        if rows:
            mean, peak, _ = rows[0]
            print(f"=== TOTAL db.load.avg (AAS) ===  mean={mean:.2f}  peak={peak:.2f}")
            print("  (read against the instance vCPU count: AAS >> vCPU = saturation)\n")
    except PerfInsightsError as exc:
        print(f"warning: total load pull failed: {exc}", file=sys.stderr)

    groups = [args.group] if args.group else _DEFAULT_GROUPS
    rc = 0
    for group in groups:
        print(f"=== by {group} ===")
        try:
            doc = fetch_load(args.identifier, args.start, args.end, region,
                             group=group, limit=args.limit, period_seconds=args.period)
        except PerfInsightsError as exc:
            print(f"  ERROR: {exc}\n", file=sys.stderr)
            rc = 1
            continue
        rows, total = rank_metric_list(doc)
        print(format_ranking(rows, total))
        print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
