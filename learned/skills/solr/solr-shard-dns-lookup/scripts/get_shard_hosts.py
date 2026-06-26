#!/usr/bin/env python3
"""Look up DNS hostnames and EC2 InstanceIds for all replicas of a Solr shard.

Thin CLI entry point for the `solr-shard-dns-lookup` skill. The reusable logic
lives in the shared `hebb_utils` library:
  - `hebb_utils.solr.shard_hosts.resolve_shard_hosts` — the $CODE_BASE config read
    (vscode-dependent; needs PYTHONPATH=$CODE_BASE/www);
  - `hebb_utils.aws.ec2.resolve_instance_id` — DNS→InstanceId via describe-instances.

The shared library's import root is `hebb_utils` (not `utils`) so it can be imported
in the same process as vscode code, which has its own top-level `utils` package
(`www/utils`). See learned/hebb_utils/README.md.

By default the script also resolves each DNS hostname to an EC2 InstanceId via
`aws ec2 describe-instances` (read-only). Pass --no-resolve to skip the AWS call and
emit only DNS hostnames (useful when AWS is unreachable).

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

If the collection is not in the registry, or the shard does not exist, the script
exits 1 with a clear message (the shard case lists the available shards). If
InstanceId resolution fails for a replica the field is printed as "UNKNOWN" and the
script continues; it does not exit 1 for AWS errors.
"""
import argparse
import os
import sys

# Import the shared logic from learned/hebb_utils/ — walk up to the dir that
# contains `hebb_utils/` (i.e. learned/) and put it on sys.path (no hardcoded depth).
# `hebb_utils` never clashes with vscode's own top-level `utils` package.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.solr.shard_hosts import resolve_shard_hosts, ShardLookupError  # noqa: E402
from hebb_utils.aws.ec2 import resolve_instance_id  # noqa: E402


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

    try:
        region, available_shards, replica_list = resolve_shard_hosts(
            args.collection, args.shard_id, args.region)
    except ShardLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.no_resolve:
        instance_ids = [resolve_instance_id(dns, region) for dns in replica_list]
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
