# Solr collection topology (collection / shard / replica / host)

**Summary:** A Solr collection (e.g. `profiles`) is sharded, and each shard is served by a small set of replicas, each replica pinned to one EC2 host. A "Solr CPU Util Too High" PagerDuty alarm names exactly one `<collection> shard <N> replica <R>` and the EC2 host behind it — so the alarm points at one host, while the shard as a whole spans the replica hosts.

## How an alarm maps to a host

A PagerDuty **P1 "Solr CPU Util Too High"** alert names the full coordinate, e.g.:

> `[us-west-2] P1 Solr CPU Util Too High on profiles shard 21 replica 0 (ec2-54-188-57-60.us-west-2.compute.amazonaws.com)`

- It is posted automatically; service is **Core Infra**.
- It is triggered by a **CloudWatch alarm of the same name** — see [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]] for the alarm definition and how to pull the underlying CPU curve.
- The coordinate is `collection / shard_id / replica` plus the EC2 **host**; the alarm's CloudWatch dimension is the host's `InstanceId` (the alarm definition carries the `InstanceId`).

## How to look up the hosts for a shard

Replica-to-host assignments are **not static** — instances get replaced, re-balanced, or scaled. Never hardcode hostnames; query the live topology instead.

**Solr Collections API** (authoritative):
```
GET /solr/admin/collections?action=CLUSTERSTATUS&collection=<collection>&wt=json
```
Returns the full shard tree: shard → replica → core URL, which contains the hostname. The replica `state` field confirms whether it is `active`. This tells you every host serving a given shard right now.

**Cross-checking via CloudWatch** — if a PagerDuty alarm has already fired, the alarm definition (from `aws cloudwatch describe-alarms --alarm-names "<name>"`) carries the `InstanceId` dimension. Resolve the hostname from the instance ID:
```
aws ec2 describe-instances --instance-ids <InstanceId> \
  --query "Reservations[*].Instances[*].PublicDnsName" --output text
```

**Via `search_host` in `log.search_query_log`** — grouping by `search_host` WHERE `core` and `shard_id` match gives you the hosts that actually served traffic in a window; cross-reference against the Solr API to confirm whether any replica is silent (down or not receiving reads). See [[../data-warehouse/search-query-log|log.search_query_log]].

**Pattern to watch:** The replica with the highest CPU is not necessarily the one whose CloudWatch alarm fired — an alarm threshold may be misconfigured or stale for a given replica. Always verify the alarm state for each replica independently via `describe-alarms`; do not assume high CPU implies active alarming.

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
