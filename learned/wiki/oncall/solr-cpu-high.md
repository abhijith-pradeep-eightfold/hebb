# Solr CPU too high (oncall ticket type)

**Summary:** A PagerDuty **P1 "Solr CPU Util Too High on `<collection> shard <N> replica <R>`"** page (service Core Infra) fires when one Solr replica host's `CPUUtilization` stays over the alarm threshold. A replica's CPU is a **flow/rate** metric — it is the cost of the **total search work** the host did per unit time, and that work is the sum of two streams: **indexing** (writes) and **query** (reads). So a CPU spike has two possible drivers — an **indexing surge** *or* a **query surge** — and the investigation **splits the load by stream first**, then hunts the driver within the stream that rose. This is the rate-metric analog of the [[queue-backed-up|Queue backed up]] **inflow-vs-drain fork**. This page covers the alarm, characterizing the spike, the **indexing-vs-query split**, the per-source driver breakdown, the bridge from a query surge back to its **processor** origin, and routing. It is a concrete instance of the [[oncall-investigation|oncall investigation discipline]].

## The alarm

A **P1 "Solr CPU Util Too High"** page names the full Solr coordinate and the EC2 host behind it, e.g. `[us-west-2] P1 Solr CPU Util Too High on profiles shard 21 replica 1 (<host>)`. It is posted automatically; service is **Core Infra**.

- It is backed by a **CloudWatch `AWS/EC2 · CPUUtilization` alarm** of the same name — **75% Average, 5-of-6 300s datapoints** (~25 min sustained). The alarm's metric dimension is the host's `InstanceId`. See [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] for the definition and how to pull the curve, and [[../solr/solr-collection-topology|Solr collection topology]] for how a `<collection> shard N replica R` coordinate maps to one EC2 host (and that a shard spans its replica hosts).
- Topology (replica→host) is **not static** — never hardcode it; resolve it live (the `solr-shard-cpu` skill does this from the collection + shard).

## Characterize the spike

Pull the named replica's CPU curve over the incident window plus a baseline, and **measure every replica of the shard, not just the alarming one** — a quiet sibling localizes the load to the alarming host (a read-driven spike lands on one replica; a write/merge-driven spike shows on both — see [[../solr/solr-collection-topology#solr-replica-traffic-semantics|replica traffic semantics]]). To do collection+shard → per-replica CPU in one call, **use the `solr-shard-cpu` skill**; CloudWatch is **UTC**, so establish the true breach window before correlating anything.

Also check **how chronic the alarm is**: pull its history to see the first-trigger time and the prior-trigger cadence — a first page in ~9 months reads very differently from a daily flapper, and it tells you whether to hunt a one-off event or a creeping trend. **Use the `inspect-cloudwatch-metric` skill** (alarm-history mode) for this. Brief earlier-in-the-day blips that did not sustain long enough to clear the 5-of-6 rule are normal and not the incident.

## CPU is a flow metric — split indexing vs query

A replica's CPU is the running cost of the work it served, so the **first** diagnostic is *which stream of work rose* — **indexing** or **query**. Break the per-query fact table [[../data-warehouse/search-query-log|log.search_query_log]] down per time bucket, scoped to this `core` (= collection) and `shard_id`, over the breach window plus a baseline (`t_create` is **UTC** — same clock as CloudWatch, no shift; see [[../process/incident-metric-correlation|metric-correlation discipline]]):

- **Indexing** = rows with **`callerid = 'index'`** (the write stream; equivalently `api = 'update/json/docs'` — see [[../data-warehouse/search-query-log#used-as-an-incident-correlation-source|the two split keys]]).
- **Query** = **every other `callerid`** (the read stream).

Compare each stream's per-bucket rate against its baseline. The stream that rose with the CPU curve is the driver; the flat one is exonerated. **To pull this split, use the `query-solr-load` skill** (`--mode split`). A **non-correlation is a real finding** — indexing can be flat while query doubled (this incident), or query flat while a merge/indexing burst drove CPU; do not assume which without the split (in an earlier `profiles` shard-21 incident query load did *not* correlate at all — see [[../process/incident-metric-correlation|metric-correlation discipline]]).

## Driver breakdown — who drove the stream that rose

Once the split names the stream, break that stream down by the dimensions that identify the **source**, over the breach window vs. the baseline, **normalized per-minute** so unequal windows compare and a small absolute count is not mistaken for a surge:

- **`callerid`** — the calling **feature / code path** (e.g. `pipeline_v2_leads:recommended`, `get_implicit_employee_counts_of_roles`, `ideal-candidate-by-pos`).
- **`group_id`** — the customer/tenant.
- **`env`** — the **originating service** of the traffic (e.g. `github-ci`, `processor`) — the single most useful discriminator for *why* the load appeared. See [[../data-warehouse/search-query-log#env|the `env` column]].

Rank sources by spike/baseline **ratio** and flag any that are **brand-new in the spike** (zero baseline). **Use the `query-solr-load` skill** (`--mode drivers`). Two independent sources can coincide — this incident had a `github-ci` CI suite *and* a `processor` batch starting in the same bucket, each roughly doubling its callers; the CPU spike was their sum, not one cause.

## Bridge a query surge back to its processor origin

When the surging `env` is **`processor`**, the queries were issued by processor ops, and you can trace them to the **root processor op** (and its owner) exactly as the [[queue-backed-up|queue-backed-up]] flow does — the join column is **`search_query_log.sequence_message_id`**, which carries the **processor SMID (`processor_msg_id`)** of the message that issued the query (see [[../data-warehouse/search-query-log#sequence_message_id|sequence_message_id]]).

1. Take a representative culprit `sequence_message_id` and **walk `processor_parent_msg_id` to the parentless root** — **use the `trace-processor-op` skill** (see [[../processor/tracing-processor-op-lineage|tracing processor-op lineage]]). Multiple surging callerids often converge on one root op (a fan-out).
2. Map the root and culprit `operation0` to their source files via [[../processor/op-registry|op_registry]], then resolve each file's owners via [[../repo/codeowners-ownership|CODEOWNERS ownership]] — **use the `codeowners-owner` skill**. Route the incident to the owning team.

In this incident the converging callerids traced to a `position file ingest` root op (`ingest_data_extract_operation`, resolved via `op_registry.py:61`), which re-seeded and fanned out `pos_stats_v2` + `position_calibration` work (the delayed-fan-out shape mirrors [[../processor/trigger-event-fanout|trigger_event fan-out]]); the owning team came from `CODEOWNERS:353` on `www/processor/ingest_data_extract_operation.py`. A `github-ci` surge has no such processor lineage — route it by asking whether a CI run was scheduled and whether it should hit production Solr at all.

## Reading the breakdown — burst vs. baseline noise

Scope the split and the driver breakdown to the **confirmed breach window** (from the metric step). A `core`/`shard_id` carries heavy *baseline* traffic; widen the window much past the breach and the burst hides under it — a high-volume caller looks the same whether it spiked or not. Per-minute normalization plus an explicit baseline bucket is what separates the burst from the baseline. Same metric-first discipline as every oncall: the [[../infra/cloudwatch-cpu-alarm|metric]] gives you the true window before you attribute a cause.

## Witnessed incidents

| | `profiles` shard 21 replica 1, us-west-2 (2026-06-29) |
|---|---|
| **Alarm** | `CPUUtilization` ≥ 75% Average, 5-of-6 300s |
| **Spike shape** | sustained breach **11:01–11:26 UTC**; per-minute peaks ~98–99% Average; one mid-window dip; first page in **~9 months** |
| **Sibling replica** | other replica ~5% mean over the same window — idle, so load localized to the alarming replica |
| **Stream that rose** | **query** — roughly **doubled** at 11:00 UTC (~10k → ~20k per 15 min) and stayed elevated; **indexing flat** (~2–3.5k/15min) throughout |
| **Drivers** | two coincident sources: **`env=github-ci`** (a parallel CI suite across 15+ `eightfolddemo-*` tenants, one brand-new caller) **and** **`env=processor`** (a tenant's `pipeline_v2_*` callers ~3×, a batch job) |
| **Root op (processor source)** | `ingest_data_extract_operation` (position file ingest) → re-seed → `pos_stats_v2` + `position_calibration` fan-out → the `pipeline_v2_*` / `ideal-candidate-by-pos` query callerids |
| **Owner** | `@EightfoldAI/dp-file-ingestion` (`CODEOWNERS:353`) |

The lesson: a CPU page need not have a single cause. The split (query, not indexing) narrowed it to reads; the `env` breakdown then revealed **two** unrelated read sources peaking in the same bucket. Only the processor source had a traceable owner; the CI source routed as an open question (scheduling + whether CI should target production Solr).

## Reporting the result

Report a Solr-CPU ticket as a **detailed, table-structured report**, not prose — the shared format is on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For *Solr CPU too high* the tables are: **alarm** config + how chronic (first/prior trigger); **spike characterization** (baseline → onset → peak vs threshold → decay, per-replica so the alarming host is localized vs. an idle sibling); **indexing-vs-query split** over the window ± baseline (the fork evidence); **driver breakdown** (`callerid × group_id × env`, spike vs baseline, per-minute, flagging new sources); **op-lineage** (root → query callerid, per hop) for any processor source; and **ownership / routing** (op → file → owner) plus open questions for non-processor sources. To post it back to the PagerDuty thread, **use the `oncall-post-report` skill** (Canvas + concise threaded reply; it confirms the destination first and renders owner/customer names as plain text so the post pages no one).

## Related skills

- `oncall-solr-cpu-high` — the high-level runbook for this ticket type; start here to run the whole investigation (characterize → split indexing vs query → driver breakdown → trace processor source → route → report).
- `solr-shard-cpu` — use it to characterize the spike: collection + shard → every replica's EC2 host and per-replica CloudWatch CPU (Average + Maximum) against the 75% threshold.
- `inspect-cloudwatch-metric` — use it to read the alarm definition and its history (first-trigger time, prior-trigger cadence — how chronic the page is).
- `query-solr-load` — use it for the indexing-vs-query split (`--mode split`) and the per-source `callerid × group_id × env` driver breakdown (`--mode drivers`) against `log.search_query_log`.
- `trace-processor-op` — use it to walk a culprit `sequence_message_id` SMID to its root processor op when the surging `env` is `processor`.
- `codeowners-owner` — use it to resolve the owning team/author of the root/culprit op's source file.
- `oncall-post-report` — use it to post the finished table-structured report back to the PagerDuty Slack thread (Canvas + concise threaded reply), with a confirm-before-post gate and plain-text (non-paging) references.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline.
- [[queue-backed-up|Queue backed up]] — the SQS queue-depth ticket type; its inflow-vs-drain fork is the stock-metric analog of this page's indexing-vs-query split.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the alarm definition and the CPU-curve pull.
- [[../solr/solr-collection-topology|Solr collection topology]] — coordinate → host mapping, per-replica CPU, and read (load-balanced) vs. write (fan-out) traffic semantics.
- [[../data-warehouse/search-query-log|log.search_query_log table]] — the per-query fact table carrying `callerid`, `env`, `group_id`, `sequence_message_id`; the indexing-vs-query split and the processor bridge.
- [[../processor/tracing-processor-op-lineage|Tracing processor-op lineage]] · [[../processor/op-registry|op_registry]] · [[../repo/codeowners-ownership|CODEOWNERS ownership]] — the processor-source trace and op→file→owner routing.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — metric-first method; CloudWatch and warehouse `t_create` are both UTC.
