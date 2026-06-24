---
skills_used: []
---

# Find which node owns a Solr shard before replacing a replica

## Task
A replica for one shard of our `profile` collection had gone down, and I was asked to figure out which node currently leads that shard before anyone touched it — so we wouldn't operate on the wrong node.

## What I did
- Confirmed we run SolrCloud (multiple nodes; collections sharded across them).
- Queried live cluster state via the collections API:
  `GET $SOLR_URL/admin/collections?action=CLUSTERSTATUS&collection=profile&wt=json`
- Got a large JSON blob. Under `cluster.collections.profile.shards`, each shard (`shard1`, `shard2`, …) had a `replicas` map; each replica had `node_name`, `core`, `state`, and some had `leader: "true"`.
- For `shard2` there were two replicas: one with `state: "down"` (the dead one) and one with `leader: "true"`, `state: "active"`. The active leader's `node_name` was the owner.
- Eyeballing the JSON was tedious, so I wrote a small scratch script to parse it and print the leader node for a given shard. Ran it; it returned the node and core directly.
- Reported the owning node to the user; they proceeded with the replica replacement themselves.

## Skills & scripts in play
No skill fired — I didn't find anything for this and worked from the API directly.

Scratch script I wrote (parses CLUSTERSTATUS, prints the active leader for a shard):
```python
import json, sys, urllib.request, urllib.parse
solr, coll, shard = sys.argv[1], sys.argv[2], sys.argv[3]
q = urllib.parse.urlencode({"action": "CLUSTERSTATUS", "collection": coll, "wt": "json"})
d = json.load(urllib.request.urlopen(f"{solr}/admin/collections?{q}"))
reps = d["cluster"]["collections"][coll]["shards"][shard]["replicas"]
for name, r in reps.items():
    if r.get("leader") == "true" and r.get("state") == "active":
        print(r["node_name"], r.get("core")); break
```
Ran it as `python3 scratch.py "$SOLR_URL" profile shard2` → printed the node and core.

## What I learned
- We run **SolrCloud**, so shard/replica placement is dynamic and lives in cluster state (ZooKeeper), not in config files.
- The **CLUSTERSTATUS** collections API returns the live picture: `cluster.collections.<name>.shards.<shardN>.replicas.<replicaId>` with `node_name`, `core`, `state` (`active`/`down`/`recovering`), and `leader` (`"true"` on exactly one healthy replica).
- The "owner" of a shard for write/admin purposes is its **active leader** replica — the one with `leader: "true"` and `state: "active"`.
- A shard can briefly have **no** active leader (during recovery or leader election); in that state there is no owner to assume.

## Friction & gaps
- No skill fired; I had nothing for "which node owns this shard" and had to reconstruct it from the raw API.
- Reading the raw CLUSTERSTATUS JSON by hand is error-prone — easy to misread which replica is the leader vs. merely active.
