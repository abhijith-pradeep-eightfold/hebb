#!/usr/bin/env python3
"""Resolve the node that owns (leads) a given shard of a SolrCloud collection.

Deterministic: queries the CLUSTERSTATUS collections API and returns the active
leader replica's node_name (and core) for the requested shard. Exits non-zero
with a clear message if the shard is missing or has no active leader.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request


def clusterstatus(solr_url, collection):
    q = urllib.parse.urlencode({"action": "CLUSTERSTATUS", "collection": collection, "wt": "json"})
    url = f"{solr_url.rstrip('/')}/admin/collections?{q}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def find_owner(status, collection, shard):
    collections = status["cluster"]["collections"]
    if collection not in collections:
        raise SystemExit(f"collection {collection!r} not found; available: {', '.join(collections)}")
    shards = collections[collection]["shards"]
    if shard not in shards:
        raise SystemExit(f"shard {shard!r} not found; available: {', '.join(shards)}")
    for replica_id, rep in shards[shard]["replicas"].items():
        if rep.get("leader") == "true" and rep.get("state") == "active":
            return {"node_name": rep["node_name"], "core": rep.get("core"), "replica": replica_id}
    raise SystemExit(f"no active leader for shard {shard!r} (recovery/election?) — do not assume an owner")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solr-url", required=True, help="Solr base URL, e.g. http://host:8983/solr")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--shard", required=True, help="e.g. shard1")
    args = ap.parse_args()
    owner = find_owner(clusterstatus(args.solr_url, args.collection), args.collection, args.shard)
    json.dump(owner, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
