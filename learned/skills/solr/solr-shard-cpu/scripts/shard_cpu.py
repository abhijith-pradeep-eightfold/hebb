#!/usr/bin/env python3
"""Report the CPU utilization of a Solr shard, end-to-end, in one invocation.

This is the bundled script for the `solr-shard-cpu` combined skill. It collapses
the no-judgment chain `solr-shard-dns-lookup` -> `inspect-cloudwatch-cpu` into a
single pipeline the agent runs once:

    collection + shard-id  ->  replica DNS + InstanceIds  ->  per-replica CPU

There is no runtime judgment between the stages (the InstanceIds from the lookup
feed straight into the CPU pull), so the whole thing is one deterministic
transform — a script, per Rule A2.

All reusable logic is imported **in-process** from the shared `hebb_utils` library:
  - `hebb_utils.solr.shard_hosts.resolve_shard_hosts` — the $CODE_BASE config read;
  - `hebb_utils.aws.ec2.resolve_instance_id`          — DNS -> InstanceId;
  - `hebb_utils.aws.cloudwatch` (fetch_cpu / series_from_datapoints / report).

The host stage reads vscode config, so this script imports `www` in-process — which
is exactly why the shared library is named `hebb_utils` and not `utils`: vscode has
its own top-level `utils` package (`www/utils`), and the two coexist on `sys.path`
only because the learned library's root is distinct.

Gate-passing invocation shape (vscode-dependent — PYTHONPATH includes /www):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/shard_cpu.py" \
        --collection positions --shard-id 2

By default it pulls the last 3 hours at 1-minute resolution and reports both the
Average (the statistic the Solr alarm evaluates) and the Maximum (per-minute
peaks) for every replica, flagging any bucket at or above --threshold.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Import the shared logic from learned/hebb_utils/ — walk up to the dir that
# contains `hebb_utils/` (i.e. learned/) and put it on sys.path (no hardcoded depth).
# `hebb_utils` coexists with vscode's own top-level `utils` package on sys.path.
_LEARNED = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_LEARNED, "hebb_utils")):
    _parent = os.path.dirname(_LEARNED)
    if _parent == _LEARNED:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _LEARNED = _parent
sys.path.insert(0, _LEARNED)
from hebb_utils.solr.shard_hosts import resolve_shard_hosts, ShardLookupError  # noqa: E402
from hebb_utils.aws.ec2 import resolve_instance_id  # noqa: E402
from hebb_utils.aws.cloudwatch import (  # noqa: E402
    CloudWatchError,
    fetch_cpu,
    report,
    series_from_datapoints,
)


def _iso_z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Report per-replica CPU utilization for a Solr shard, end-to-end.")
    p.add_argument("--collection", required=True,
                   help="Solr collection name (any entry in SEARCH_INDEX_SETTINGS_REGISTRY, "
                        "e.g. 'positions', 'profiles', 'user_calendar_events')")
    p.add_argument("--shard-id", required=True, type=int,
                   help="Integer shard ID. Shard IDs are not contiguous; if it does not "
                        "exist the available IDs are reported.")
    p.add_argument("--region", default=None,
                   help="AWS region (default: the lookup's EF_DEFAULT_REGION -> us-west-2)")
    p.add_argument("--hours", type=float, default=3.0,
                   help="size of the look-back window ending now, in hours (default 3)")
    p.add_argument("--start-time", default=None,
                   help="explicit window start, ISO-8601 UTC (e.g. 2026-06-26T09:50:00Z); "
                        "overrides --hours")
    p.add_argument("--end-time", default=None,
                   help="explicit window end, ISO-8601 UTC; defaults to now (UTC)")
    p.add_argument("--period", type=int, default=60,
                   help="CloudWatch bucket size in seconds (default 60 = 1-minute buckets)")
    p.add_argument("--threshold", type=float, default=75.0,
                   help="breach threshold (default 75.0, the Solr CPU alarm threshold)")
    args = p.parse_args(argv)

    # Resolve the time window (UTC).
    end_dt = datetime.now(timezone.utc)
    end_time = args.end_time or _iso_z(end_dt)
    if args.start_time:
        start_time = args.start_time
    else:
        start_time = _iso_z(end_dt - timedelta(hours=args.hours))

    # Stage 1: resolve replica hosts + InstanceIds (vscode-dependent, in-process).
    try:
        region, available_shards, replica_dns = resolve_shard_hosts(
            args.collection, args.shard_id, args.region)
    except ShardLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    replicas = [{"dns": dns, "instance_id": resolve_instance_id(dns, region)}
                for dns in replica_dns]

    print(f"collection : {args.collection}")
    print(f"shard_id   : {args.shard_id}")
    print(f"region     : {region}")
    print(f"window     : {start_time} .. {end_time}  (period {args.period}s, UTC)")
    print(f"replicas   : {len(replicas)}")
    print("note       : 'the CPU of a shard' is per-replica — one figure per replica host.")
    print()

    # Stage 2: per-replica CPU (www-free analysis util). No judgment between stages.
    exit_code = 0
    for i, r in enumerate(replicas):
        iid = r["instance_id"]
        label_base = f"replica {i} ({r['dns']} / {iid})"
        if not iid or iid in ("UNKNOWN", "(skipped)"):
            print(f"=== {label_base} ===")
            print("  no InstanceId resolved for this replica — cannot pull CPU.")
            print()
            exit_code = 1
            continue
        try:
            doc = fetch_cpu(iid, start_time, end_time, region,
                            period=args.period, statistics=("Average", "Maximum"))
        except CloudWatchError as exc:
            print(f"=== {label_base} ===")
            print(f"  CPU fetch failed: {exc}")
            print()
            exit_code = 1
            continue
        datapoints = doc.get("Datapoints", []) if isinstance(doc, dict) else []
        # Report both the alarm-evaluated Average and the per-minute Maximum.
        for stat in ("Average", "Maximum"):
            rows = series_from_datapoints(datapoints, stat)
            report(f"{label_base} — {stat}", rows, args.threshold, stat)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
