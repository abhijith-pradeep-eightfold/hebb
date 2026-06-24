# Solr collection topology (collection / shard / replica / host)

**Summary:** A Solr collection (e.g. `profiles`) is sharded, and each shard is served by a small set of replicas, each replica pinned to one EC2 host. A "Solr CPU Util Too High" PagerDuty alarm names exactly one `<collection> shard <N> replica <R>` and the EC2 host behind it — so the alarm points at one host, while the shard as a whole spans the replica hosts.

## How an alarm maps to a host

A PagerDuty **P1 "Solr CPU Util Too High"** alert names the full coordinate, e.g.:

> `[us-west-2] P1 Solr CPU Util Too High on profiles shard 21 replica 0 (ec2-54-188-57-60.us-west-2.compute.amazonaws.com)`

- It is posted automatically; service is **Core Infra** (`P7I5DOG`).
- It is triggered by a **CloudWatch alarm of the same name** — see [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]] for the alarm definition and how to pull the underlying CPU curve.
- The coordinate is `collection / shard_id / replica` plus the EC2 **host**; the alarm's CloudWatch dimension is the host's `InstanceId` (the alarm definition carries the `InstanceId`).

## `profiles` shard 21 lives on exactly two hosts

For the `profiles` collection, shard 21 is served by **two replicas on two hosts**. Confirmed two independent ways in the 2026-06-15 incident — by the two sibling CloudWatch alarms, and by the `search_host` breakdown in [[../data-warehouse/search-query-log|log.search_query_log]]:

| Replica | Host | InstanceId | Observed CPU profile (2026-06-15, 6h band) |
|---|---|---|---|
| replica 0 | `ec2-54-188-57-60` | `i-0d22f39bd3dd3171a` | cool/spiky — Average mean ~31%, occasional ~99% spikes (the host that paged) |
| replica 1 | `ec2-34-217-117-48` | `i-08580e991383820e1` | broadly hot — Average mean ~55%, frequently near-saturated |

Notable: the **hotter** replica (replica 1) did **not** page on 2026-06-15. Its CloudWatch alarm last transitioned 2025-09-15 and was only (re)configured **2026-06-23**, after this incident — so high sustained CPU on a host does not imply it was alarming at the time. Always confirm the alarm state separately from the metric.

## Solr replica traffic semantics

From the per-host/api breakdown of [[../data-warehouse/search-query-log|log.search_query_log]] during the incident:

- **`api = update/json/docs`** — indexing **writes**. These **fan out to all replicas** of a shard, so the same write load appears on both replica hosts (replica 0: 4759 ops; replica 1: 4760 ops on shard 21 — nearly identical, as expected for a fan-out write).
- **`api = query`** — **reads**, **load-balanced across replicas**. So read load is attributed to whichever replica served the request (replica 1 served 16,911 shard-21 queries in the window; replica 0's shard-21 rows were almost all writes).

This matters when correlating CPU to load: a read-driven CPU spike on one replica would show up as that replica's `query` rows, while a write/merge-driven spike shows on both.

## Related

- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]] — pull the alarm definition and the EC2 CPUUtilization timeseries behind the page.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor on the real CPU curve before correlating to query load.
- [[../data-warehouse/search-query-log|log.search_query_log table]] — the per-query fact table carrying `core`/`shard_id`/`search_host`/`api`; how shard-21 hosts and traffic were confirmed.

---
*Sources:* witness `inputs/2026-06-24-solr-cpu-spike-debug.md` (`[17:00]` PagerDuty post, `[17:14]` two `describe-alarms` results + InstanceIds, `[17:25]` `search_host`/`api` breakdown).
