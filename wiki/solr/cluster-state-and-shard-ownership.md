# Cluster state and shard ownership (SolrCloud)

Part of [Solr](index.md).

We run **SolrCloud**, so shard and replica placement is *dynamic*: it lives in cluster state (ZooKeeper), not in static config files. To know where anything is right now, you read live cluster state — you cannot infer it from the repo.

## Reading cluster state: CLUSTERSTATUS

The collections API returns the live picture:

```
GET $SOLR_URL/admin/collections?action=CLUSTERSTATUS&collection=<name>&wt=json
```

The shape that matters:

```
cluster.collections.<name>.shards.<shardN>.replicas.<replicaId> = {
  node_name,    # the node hosting this replica
  core,         # the core name on that node
  state,        # "active" | "down" | "recovering"
  leader        # "true" on exactly one healthy replica per shard
}
```

## What "owns a shard" means

The **owner** of a shard, for write or admin purposes, is its **active leader** replica — the one with `leader: "true"` **and** `state: "active"`. A replica that is merely `active` but not leader does not own the shard.

## Failure mode to respect

A shard can briefly have **no** active leader (during recovery or a leader election). In that state there is no owner to assume — surface the bad state rather than picking an arbitrary replica. Reading the raw `CLUSTERSTATUS` JSON by hand is error-prone here: it is easy to mistake a non-leader `active` replica for the leader.

See the `find-shard-owner` skill, which resolves the active leader deterministically.
