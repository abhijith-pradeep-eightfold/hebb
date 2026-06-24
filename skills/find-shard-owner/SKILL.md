---
name: find-shard-owner
description: Find which SolrCloud node currently owns (leads) a given shard of a collection. Use when you need a shard's leader node before operating on it — e.g. before replacing, reloading, or evicting a replica.
---

# Find the owner of a Solr shard

Resolves the **active leader** replica of a shard and the node hosting it, from live cluster state. The resolution is deterministic, so it is backed by a bundled script that queries the `CLUSTERSTATUS` API and parses out the leader.

## Use

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/find_shard_owner.py" \
  --solr-url "$SOLR_URL" --collection <collection> --shard <shardN>
```

Prints JSON: `{"node_name": ..., "core": ..., "replica": ...}`.

## Notes
- "Owner" = the replica with `leader: "true"` **and** `state: "active"`. A shard with no active leader is in a bad state (recovery/election) — the script exits non-zero; surface that, do not guess an owner.
- Background on SolrCloud cluster state, the `CLUSTERSTATUS` shape, and what ownership means: see `wiki/solr/cluster-state-and-shard-ownership.md`.
