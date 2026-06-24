#!/usr/bin/env python3
"""Look up DNS hostnames and EC2 InstanceIds for all replicas of a Solr shard.

This is the bundled script for the `solr-shard-dns-lookup` skill.
It imports $CODE_BASE config + search_index_settings to retrieve the live
per-region shard-host map for any collection registered in
SEARCH_INDEX_SETTINGS_REGISTRY — so it needs PYTHONPATH=$CODE_BASE/www
(not $CODE_BASE alone).

The collection's hosts_key is derived from SEARCH_INDEX_SETTINGS_REGISTRY:
  settings = SEARCH_INDEX_SETTINGS_REGISTRY[collection]
  hosts_key = settings.hosts_key
Default pattern is '{tablename}_shard_hosts'; profiles and positions have
hard-coded overrides ('shard_hosts' and 'position_shard_hosts' respectively).

By default the script also resolves each DNS hostname to an EC2 InstanceId
via `aws ec2 describe-instances` (read-only). Pass --no-resolve to skip
the AWS call and emit only DNS hostnames (useful when AWS is unreachable).

Gate-passing invocation shape:

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/get_shard_hosts.py" \
        --collection user_calendar_events --shard-id 0

Output (one key=value pair per replica, plus a human-readable block):

    collection=user_calendar_events
    shard_id=0
    region=us-west-2
    available_shards=0,38,46,79
    replica_count=2
    replica_0_dns=ec2-xx-xx-xx-xx.us-west-2.compute.amazonaws.com
    replica_0_instance_id=i-0123456789abcdef0
    replica_1_dns=ec2-yy-yy-yy-yy.us-west-2.compute.amazonaws.com
    replica_1_instance_id=i-0fedcba9876543210

    --- human-readable ---
    collection : user_calendar_events
    shard_id   : 0
    region     : us-west-2
    replica 0  : ec2-xx-xx-xx-xx.us-west-2.compute.amazonaws.com  (i-0123456789abcdef0)
    replica 1  : ec2-yy-yy-yy-yy.us-west-2.compute.amazonaws.com  (i-0fedcba9876543210)

If the collection is not in the registry the script exits 1 with the list of
valid registry keys.  If the shard does not exist the script exits 1 with a
clear message including the list of available shards.  If InstanceId resolution
fails for a replica (e.g. AccessDenied or no match) the field is printed as
"UNKNOWN" and the script continues; it does not exit 1 for AWS errors.
"""
import argparse
import json
import os
import subprocess
import sys


def resolve_instance_id(dns_hostname, region):
    """Resolve a DNS hostname to an EC2 InstanceId via describe-instances.

    Returns the InstanceId string, or "UNKNOWN" if the call fails or returns
    no results.  Never raises — errors are reported as "UNKNOWN" so callers
    can continue with partial results.
    """
    try:
        result = subprocess.run(
            [
                "aws", "ec2", "describe-instances",
                "--region", region,
                "--filters", f"Name=dns-name,Values={dns_hostname}",
                "--query", "Reservations[*].Instances[*].InstanceId",
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"  warning: describe-instances failed for {dns_hostname}: {result.stderr.strip()}",
                file=sys.stderr,
            )
            return "UNKNOWN"
        ids = json.loads(result.stdout)
        # Response is [[id, ...], ...] — flatten one level
        flat = [iid for group in ids for iid in group]
        if not flat:
            print(
                f"  warning: no InstanceId found for dns-name={dns_hostname}",
                file=sys.stderr,
            )
            return "UNKNOWN"
        return flat[0]
    except Exception as exc:
        print(
            f"  warning: could not resolve InstanceId for {dns_hostname}: {exc}",
            file=sys.stderr,
        )
        return "UNKNOWN"


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Look up Solr shard replica DNS hostnames and EC2 InstanceIds.")
    p.add_argument("--collection", required=True,
                   help="Solr collection name (any entry in SEARCH_INDEX_SETTINGS_REGISTRY, "
                        "e.g. 'profiles', 'positions', 'user_calendar_events')")
    p.add_argument("--shard-id", required=True, type=int,
                   help="Integer shard ID (e.g. 7).  Shard IDs are not contiguous.")
    p.add_argument("--region", default=None,
                   help="AWS region (default: EF_DEFAULT_REGION env var → 'us-west-2')")
    p.add_argument("--no-resolve", action="store_true",
                   help="Skip the DNS→InstanceId AWS call; emit only DNS hostnames.")
    args = p.parse_args(argv)

    # Resolve region
    region = args.region or os.getenv("EF_DEFAULT_REGION")
    if not region:
        print("error: --region not specified and EF_DEFAULT_REGION is not set", file=sys.stderr)
        return 1

    # Import $CODE_BASE packages — requires PYTHONPATH=$CODE_BASE/www
    try:
        from config import config as cfg_module
        from search import search_constants
        from search.search_index_settings import SEARCH_INDEX_SETTINGS_REGISTRY
        from utils.os_constants import EF_DEFAULT_REGION  # noqa: F401  (confirms path resolves)
    except ImportError as exc:
        print(
            f"error: import failed — is PYTHONPATH set to $CODE_BASE/www?\n  {exc}",
            file=sys.stderr,
        )
        return 1

    # Validate collection against the registry and derive hosts_key
    if args.collection not in SEARCH_INDEX_SETTINGS_REGISTRY:
        valid = sorted(SEARCH_INDEX_SETTINGS_REGISTRY.keys())
        print(
            f"error: '{args.collection}' is not in SEARCH_INDEX_SETTINGS_REGISTRY.\n"
            f"  Valid collection names: {', '.join(valid)}",
            file=sys.stderr,
        )
        return 1

    hosts_key = SEARCH_INDEX_SETTINGS_REGISTRY[args.collection].hosts_key

    # Load search_config for this region
    try:
        search_cfg = cfg_module.get(search_constants.SEARCH_CONFIG, region=region)
    except Exception as exc:
        print(f"error: config.get('{search_constants.SEARCH_CONFIG}', region='{region}') failed:\n  {exc}",
              file=sys.stderr)
        return 1

    if hosts_key not in search_cfg:
        print(
            f"error: key '{hosts_key}' not found in search_config for region '{region}'.\n"
            f"  Available top-level keys: {sorted(search_cfg.keys())}",
            file=sys.stderr,
        )
        return 1

    shard_hosts = search_cfg[hosts_key]
    available_shards = sorted(shard_hosts.keys(), key=lambda x: int(x))
    shard_key = str(args.shard_id)

    if shard_key not in shard_hosts:
        print(
            f"error: shard {args.shard_id} does not exist for collection '{args.collection}' "
            f"in region '{region}'.\n"
            f"  Available shard IDs: {', '.join(available_shards)}",
            file=sys.stderr,
        )
        return 1

    replicas = shard_hosts[shard_key]
    # replicas may be a list of DNS strings or a dict; normalise to list
    if isinstance(replicas, dict):
        replica_list = [replicas[str(i)] for i in sorted(int(k) for k in replicas.keys())]
    elif isinstance(replicas, list):
        replica_list = replicas
    else:
        # scalar — single replica
        replica_list = [str(replicas)]

    # Resolve InstanceIds (unless --no-resolve)
    instance_ids = []
    if not args.no_resolve:
        for dns in replica_list:
            instance_ids.append(resolve_instance_id(dns, region))
    else:
        instance_ids = ["(skipped)"] * len(replica_list)

    # --- machine-readable output ---
    print(f"collection={args.collection}")
    print(f"shard_id={args.shard_id}")
    print(f"region={region}")
    print(f"available_shards={','.join(available_shards)}")
    print(f"replica_count={len(replica_list)}")
    for i, (dns, iid) in enumerate(zip(replica_list, instance_ids)):
        print(f"replica_{i}_dns={dns}")
        print(f"replica_{i}_instance_id={iid}")

    # --- human-readable summary ---
    print()
    print("--- human-readable ---")
    print(f"collection : {args.collection}")
    print(f"shard_id   : {args.shard_id}")
    print(f"region     : {region}")
    for i, (dns, iid) in enumerate(zip(replica_list, instance_ids)):
        print(f"replica {i}  : {dns}  ({iid})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
