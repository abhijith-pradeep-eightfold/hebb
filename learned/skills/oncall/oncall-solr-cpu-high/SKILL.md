---
name: oncall-solr-cpu-high
model: sonnet
description: High-level oncall runbook for a "Solr CPU Util Too High" PagerDuty page (an EC2 host-CPU alarm on a Solr replica). Use when you pick up a "[<region>] P1 Solr CPU Util Too High on <collection> shard <N> replica <R>" alarm and want the end-to-end investigation, not just one step — characterize the CPU spike per-replica, split the load into indexing vs query to find which stream rose, break that stream down by callerid/group_id/env to find the source, trace any processor-issued surge to its root op, and route to the owning team. Sequences solr-shard-cpu → inspect-cloudwatch-metric → query-solr-load → trace-processor-op → codeowners-owner → oncall-post-report. Reach for this whenever a Solr-CPU / Solr host-load alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
---

# Oncall runbook — Solr CPU too high

The high-level flow for a `[<region>] P1 Solr CPU Util Too High on <collection> shard <N> replica <R>` PagerDuty page. The domain facts — the `CPUUtilization` 75%/5-of-6 alarm, that a replica's CPU is a **flow metric** (= indexing + query work), the **indexing-vs-query split** (the rate-metric analog of [[../../../wiki/oncall/queue-backed-up|queue-backed-up]]'s inflow-vs-drain fork), the `sequence_message_id` bridge to the processor, and the table shapes to report — live in [[../../../wiki/oncall/solr-cpu-high|Solr CPU too high]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which window to pull, which stream rose, which source, whether it is processor-driven, who to route to), so read each step's output before the next. Critically, **do not jump straight to "which caller is loudest"** — split indexing vs query first, because the page can be either, and a CPU spike can be the sum of two unrelated sources.

## Execution flow

1. **Confirm & characterize the spike (per-replica).** Pull the alarming replica's CPU curve **and** every sibling replica of the shard — **use the `solr-shard-cpu` skill** (`--collection <collection> --shard-id <N>`, an explicit `--start-time`/`--end-time` UTC window around the page, or `--hours`). A sibling that stayed idle localizes the load to the alarming host (reads are load-balanced per replica; writes/merges show on both). Establish the true breach window (CloudWatch is UTC) before correlating anything.
2. **How chronic is the alarm?** Pull its history — first-trigger time + prior-trigger cadence — to decide whether this is a one-off event or a creeping trend: **use the `inspect-cloudwatch-metric` skill** (alarm-history mode). A first page in months points at a discrete event in-window; a frequent flapper points at a trend or a misconfigured threshold.
3. **Split indexing vs query — which work stream rose?** A replica's CPU is the cost of indexing **+** query work, so decide which rose. Pull the per-bucket indexing (`callerid='index'`) vs query (all other callerids) counts for this `core`(=collection) + `shard_id` over the breach window + a baseline and overlay it on the CPU curve from step 1 — **use the `query-solr-load` skill** (`--mode split --core <collection> --shard-id <N> --since <start> --until <end>`). The stream that rises with CPU is the driver; the flat one is exonerated. A non-correlation (neither stream tracks CPU) is itself a finding — reconsider merges/GC/a noisy-neighbor process.
4. **Break the rising stream down by source.** Break the stream that rose down by `callerid × group_id × env` over the spike window vs the baseline, normalized per-minute — **use the `query-solr-load` skill** (`--mode drivers --since <spike-start> --until <spike-end> --baseline-since <base-start> --baseline-until <base-end>`; defaults to the query stream). Rank by the spike/baseline `ratio` and flag `NEW` sources. **`env` is the key discriminator** — e.g. `github-ci` (a CI suite) vs `processor` (a batch job); more than one source can peak in the same bucket, so the spike may be a *sum*.
5. **Trace & route a processor source.** When the surging `env` is **`processor`**, the queries were issued by processor ops — take a representative culprit row's **`sequence_message_id`** (the processor SMID) and walk `processor_parent_msg_id` to the parentless root — **use the `trace-processor-op` skill** (converging callerids often share one root op). Then map the root/culprit `operation0` to its file and owner — **use the `codeowners-owner` skill** (op→file via [[../../../wiki/processor/op-registry|op_registry]], file→owner via [[../../../wiki/repo/codeowners-ownership|CODEOWNERS]], git-author fallback). A non-processor source (e.g. `github-ci`) has no such lineage — route it as an open question (was a run scheduled? should it hit production Solr?).
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric (`CPUUtilization`), threshold / evaluation (75% Average, 5-of-6 300s), state, and **how chronic** (first / prior trigger).
2. **Spike characterization** — baseline → onset → peak (vs the 75% threshold) → decay, **per replica** so the alarming host is localized against an idle sibling.
3. **Indexing-vs-query split** — per-bucket indexing vs query over the window ± baseline: the fork evidence showing which stream drove the CPU.
4. **Driver breakdown** — `callerid × group_id × env` over the breach window vs baseline (per-minute, with the spike/baseline ratio and any NEW sources), narrowed to the breach window so a burst separates from baseline.
5. **Op-lineage** (when a processor source) — root → query-callerid chain, one row per hop: the op, its queue, time, status.
6. **Ownership / routing** — op → source file → owning team/author for any processor source; open questions (scheduling / production-targeting) for a CI or other non-processor source.
7. **Timeline** — the key timestamps in one place, all on one clock (UTC for CloudWatch + warehouse `t_create`).

## Constituent skills (each independently usable)

- `solr-shard-cpu` — step 1, the per-replica CPU characterization (collection + shard → every replica's host + CloudWatch CPU).
- `inspect-cloudwatch-metric` — step 2, the alarm definition + history (how chronic the page is).
- `query-solr-load` — step 3 (indexing-vs-query split) and step 4 (the `callerid × group_id × env` driver breakdown) over `log.search_query_log`.
- `trace-processor-op` — step 5, the root-op walk from a culprit `sequence_message_id` SMID.
- `codeowners-owner` — step 5, op→file→owner routing.
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
