#!/usr/bin/env python3
"""Report the CPU utilization of a Solr shard, end-to-end, in one invocation.

This is the bundled script for the `solr-shard-cpu` combined skill. It collapses
the no-judgment chain `solr-shard-dns-lookup` -> `inspect-cloudwatch-cpu` into a
single pipeline the agent runs once:

    collection + shard-id  ->  replica DNS + InstanceIds  ->  per-replica CPU

There is no runtime judgment between the stages (the InstanceIds from the lookup
feed straight into the CPU pull), so the whole thing is one deterministic
transform — a script, per Rule A2.

Two reuse mechanisms, chosen to respect the `learned/utils` namespace:
  - **Host stage (www-coupled):** run the existing, canonical
    `solr-shard-dns-lookup/scripts/get_shard_hosts.py` as a **subprocess**, with
    `PYTHONPATH=$CODE_BASE/www` in the child env. That stage imports vscode's
    own top-level `utils` package, which would shadow `learned/utils` if imported
    in-process — so it stays in its own process. The lookup logic is reused, not
    duplicated.
  - **CPU stage (www-free):** import the shared `learned/utils/aws/cloudwatch.py`
    (also used by `inspect-cloudwatch-cpu`) in-process. This script never puts
    `$CODE_BASE/www` on its path, so `import utils.*` resolves unambiguously to
    `learned/utils`.

Gate-passing invocation shape (www-free — `PYTHONPATH="$CODE_BASE"`, not /www):

    PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/shard_cpu.py" \
        --collection positions --shard-id 2

By default it pulls the last 3 hours at 1-minute resolution and reports both the
Average (the statistic the Solr alarm evaluates) and the Maximum (per-minute
peaks) for every replica, flagging any bucket at or above --threshold.
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# Import the shared, www-free CloudWatch logic from learned/utils/. Walk up to the
# dir that contains `utils/` (i.e. learned/) and put it on sys.path — no hardcoded
# depth. This script is www-free, so there is no clash with vscode's `utils`.
_LEARNED = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_LEARNED, "utils")):
    _parent = os.path.dirname(_LEARNED)
    if _parent == _LEARNED:
        raise RuntimeError("could not locate learned/utils/ above this script")
    _LEARNED = _parent
sys.path.insert(0, _LEARNED)
from utils.aws.cloudwatch import (  # noqa: E402
    CloudWatchError,
    fetch_cpu,
    report,
    series_from_datapoints,
)

# The canonical host-lookup script (a sibling learned skill), reused via subprocess.
GET_SHARD_HOSTS = os.path.join(
    _LEARNED, "skills", "solr", "solr-shard-dns-lookup", "scripts", "get_shard_hosts.py")


def lookup_hosts(collection, shard_id, region):
    """Run get_shard_hosts.py (www-coupled) in a subprocess and parse its output.

    Returns a dict: {region, available_shards, replicas: [{dns, instance_id}, ...]}.
    Raises RuntimeError (message includes the child's stderr — e.g. the available
    shard IDs when the shard doesn't exist) if the lookup fails.
    """
    code_base = os.environ.get("CODE_BASE")
    if not code_base:
        raise RuntimeError("CODE_BASE is not set; cannot run the shard-hosts lookup.")
    if not os.path.exists(GET_SHARD_HOSTS):
        raise RuntimeError(f"shard-hosts lookup script not found at {GET_SHARD_HOSTS}")

    cmd = [sys.executable, GET_SHARD_HOSTS,
           "--collection", collection, "--shard-id", str(shard_id)]
    if region:
        cmd += ["--region", region]
    child_env = {**os.environ, "PYTHONPATH": os.path.join(code_base, "www")}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"shard-hosts lookup failed (exit {proc.returncode}):\n{proc.stderr.strip()}")

    # Parse the machine-readable `key=value` block (stop at the human-readable part).
    fields = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("---") or not line:
            if fields:
                break
            continue
        m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2)

    count = int(fields.get("replica_count", "0"))
    replicas = []
    for i in range(count):
        replicas.append({
            "dns": fields.get(f"replica_{i}_dns", ""),
            "instance_id": fields.get(f"replica_{i}_instance_id", ""),
        })
    return {
        "region": fields.get("region", region or ""),
        "available_shards": fields.get("available_shards", ""),
        "replicas": replicas,
    }


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

    # Stage 1: resolve replica hosts + InstanceIds (www-coupled subprocess).
    try:
        info = lookup_hosts(args.collection, args.shard_id, args.region)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    region = info["region"] or args.region or "us-west-2"
    replicas = info["replicas"]

    print(f"collection : {args.collection}")
    print(f"shard_id   : {args.shard_id}")
    print(f"region     : {region}")
    print(f"window     : {start_time} .. {end_time}  (period {args.period}s, UTC)")
    print(f"replicas   : {len(replicas)}")
    print("note       : 'the CPU of a shard' is per-replica — one figure per replica host.")
    print()

    # Stage 2: per-replica CPU (www-free, shared util). No judgment between stages.
    exit_code = 0
    for i, r in enumerate(replicas):
        iid = r["instance_id"]
        label_base = f"replica {i} ({r['dns']} / {iid})"
        if not iid or iid in ("UNKNOWN", "(skipped)"):
            print(f"=== {label_base} ===")
            print(f"  no InstanceId resolved for this replica — cannot pull CPU.")
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
