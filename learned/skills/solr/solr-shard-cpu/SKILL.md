---
name: solr-shard-cpu
model: sonnet
description: Report the CPU utilization of a Solr shard end-to-end, in one step — given a collection name and shard ID, resolve every replica's EC2 host and pull each replica's CloudWatch CPU (Average + Maximum) against the alarm threshold. Use whenever a task asks for the CPU / utilization / load of a Solr shard starting from collection + shard number rather than an alarm or an InstanceId — e.g. "what is the CPU of positions shard 2", "CPU of profiles shard 21", "is user_calendar_events shard 0 hot", "check the load on positions shard 7". By default it prints an aggregate (min/mean/max + breach blocks) for every replica; pass --per-bucket for a one-row-per-period table (e.g. "hourly/per-bucket CPU table for profiles shard 21", "24h CPU by hour") and --replica N to report just one replica ("CPU of positions shard 2 replica 0"). This is the one-call combination of solr-shard-dns-lookup → inspect-cloudwatch-metric (no judgment between the steps). Use the individual skills instead when you need ONLY the hosts (solr-shard-dns-lookup) or ONLY a CPU curve from a PagerDuty alarm / known InstanceId (inspect-cloudwatch-metric).
knowledge_optional:
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
---

# Solr shard CPU (collection + shard → per-replica CPU)

Answer "what is the CPU of `<collection>` shard `<N>`?" in a single invocation. This skill **collapses a no-judgment chain** — `solr-shard-dns-lookup` → `inspect-cloudwatch-metric` — into one bundled script: the replica InstanceIds the lookup produces feed straight into the CloudWatch pull, with no decision in between, so the whole pipeline is one deterministic run.

The grounding facts live in the wiki — [[../../../wiki/solr/solr-collection-topology|Solr collection topology]] (a shard spans replica hosts; **its CPU is per-replica**) and [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] (the 75% Average alarm, the `InstanceId` dimension, UTC). The runtime judgment this skill carries is just **which collection, which shard, and which window**.

## When to use this skill

- You have a **collection name + shard ID** and want its CPU — a current-state "how hot is this shard right now" question, with no PagerDuty alarm in hand.
- You want **all replicas** of the shard measured (a shard's CPU is per-replica; this skill reports each host's Average and Maximum) — or just **one replica** (`--replica N`).
- You want either an **aggregate** summary (default) or a **per-bucket table**, one row per period (`--per-bucket`) — e.g. an hourly CPU table over 24h.

Use a **constituent** skill instead when the task is narrower (they stay independently usable):
- only need the hosts/InstanceIds → **`solr-shard-dns-lookup`**;
- already have a PagerDuty alarm name or an InstanceId, or want to characterize a known spike → **`inspect-cloudwatch-metric`**.

## How it works (one bundled script)

`scripts/shard_cpu.py` runs the full pipeline, importing every stage **in-process** from the shared `hebb_utils` library:
1. **Resolve replica hosts** via `hebb_utils.solr.shard_hosts` (the same `$CODE_BASE` config read the `solr-shard-dns-lookup` skill uses) and resolve each DNS host to an InstanceId via `hebb_utils.aws.ec2`. This stage imports vscode config, so the script runs with `PYTHONPATH=$CODE_BASE/www` (see [[../../../wiki/vscode-repo/python-import-root|Python import root]]).
2. **Pull + analyze CPU** for each replica InstanceId via `hebb_utils.aws.cloudwatch` (the same analysis module the `inspect-cloudwatch-metric` skill uses), reporting **Average** (the statistic the alarm evaluates) and **Maximum** (per-minute peaks), flagging any bucket at or above the threshold.

The shared library is named `hebb_utils` (not `utils`) so it coexists with vscode's own top-level `utils` package on `sys.path` — letting the vscode-dependent host stage and the shared logic run in one process.

## Steps

### 1. (Optional) skim the wiki

If you need the alarm semantics or the per-replica framing, skim [[../../../wiki/solr/solr-collection-topology|Solr collection topology]] and [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]].

### 2. Run the bundled script (one call, no approval needed)

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/shard_cpu.py" --collection <collection> --shard-id <N>
```

- `PYTHONPATH="$CODE_BASE/www"` — the host stage reads vscode config in-process; the shared logic imports as `hebb_utils`, which coexists with vscode's `utils` (see [[../../../wiki/vscode-repo/python-import-root|Python import root]]).
- Defaults to the **last 3 hours** at `--period 60` (1-minute buckets) and `--threshold 75`. Override the window with `--hours <H>` or an explicit `--start-time`/`--end-time` (ISO-8601 UTC, e.g. `2026-06-26T09:50:00Z`); change the region with `--region` (default resolves to `us-west-2`).
- **Output modes:** by default it prints the **aggregate** summary (min/mean/max + contiguous-breach blocks) for **every** replica. Add `--per-bucket` for a **one-row-per-period table** (`bucket_start_utc`, Average, Maximum, breach flag + a per-replica summary line) — pair it with a coarser `--period` (e.g. `--period 3600 --hours 24` for hourly buckets over a day). Add `--replica N` (0-based, in resolved order) to report a **single** replica; out-of-range exits non-zero and reports the replica count. Example — hourly table for one replica over 24h:
  ```bash
  PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/shard_cpu.py" --collection profiles --shard-id 21 --replica 0 --per-bucket --hours 24 --period 3600
  ```
- If the shard doesn't exist, the script exits non-zero and the error includes the **available shard IDs** (shard numbering is non-contiguous) — report them and confirm the right shard.
- If a replica's InstanceId couldn't be resolved (AWS error), that replica is reported as un-pullable and the others still complete; the script does not abort the whole run.

### 3. Report

For each replica, present its Average (the alarm-comparable figure) and Maximum (peaks), plus whether any bucket reached the threshold. There is **no single shard CPU** — report one figure set per replica host. Include collection, shard_id, region, and the window. With `--per-bucket`, present the per-period table as-is (one row per bucket); judge breach on the **Average** column — a lone high per-minute **Maximum** with a low Average is normal, not a breach (see [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm]]).

## Notes

- **A shard's CPU is per-replica.** Two replicas can both be idle, or asymmetric; report each — see [[../../../wiki/solr/solr-collection-topology|topology]].
- **Constituents stay alive.** This skill shares logic with `solr-shard-dns-lookup` and `inspect-cloudwatch-metric` through the `hebb_utils` library (`hebb_utils.solr.shard_hosts`, `hebb_utils.aws.ec2`, `hebb_utils.aws.cloudwatch`); both skills remain usable on their own.
- **Reachability is only knowable by trying.** If the role can't read CloudWatch/EC2, the script reports the error per replica — report it plainly rather than guessing.
