---
task: Debug PagerDuty P1 alert — Solr CPU Too High on profiles shard 21 replica 1 (ec2-34-217-117-48.us-west-2.compute.amazonaws.com).
date: 2026-06-29
skills_used:
  - {name: solr-shard-cpu, note: resolved both replicas + confirmed alarm window + per-minute CPU series}
  - {name: inspect-cloudwatch-metric, note: pulled alarm history via `describe-alarm-history` to confirm first-trigger time and prior-trigger cadence}
  - {name: query-starrocks, note: five progressive queries against log.search_query_log — sanity check, 5-min buckets, callerid distribution, 15-min indexing-vs-query split, final callerid+group_id+env spike vs baseline}
  - {name: oncall-post-report, note: Canvas + threaded reply in PD alert Slack thread}
  - {name: query-processor-event-log, note: count-by operation0 for Walmart during spike to enumerate running ops}
  - {name: trace-processor-op, note: traced SMIDs from search_query_log.sequence_message_id → root op via processor_parent_msg_id walk; all four callerids converged to one root}
interventions: 2
---

# profiles shard 21 replica 1 — P1 CPU alarm 2026-06-29

**Task:** Debug PagerDuty alert "[us-west-2] P1 Solr CPU Util Too High on profiles shard 21 replica 1" from Slack thread https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782732699822119. Follow oncall discipline: confirm and characterize CPU spike → correlate indexing vs query throughput → identify source (callerid/group_id/env) driving the surge → post a structured report back to the thread.

## Log

### Confirm and characterize the alarm

- **Alarm resolved:** host `ec2-34-217-117-48.us-west-2.compute.amazonaws.com` → `i-08580e991383820e1`, region us-west-2, collection profiles, shard 21, replica 1.
- **CPU series (1-min buckets, replica 1):** Alarm evaluation window 11:01–11:26 UTC; 5 of 6 five-minute averages breached 75%. Peak buckets: 99.1% (11:01), 98.4% (11:06), 86.0% (11:16), 98.4% (11:21), 99.5% (11:26); the 11:11 bucket dipped to 27.9%.
- **Alarm history:** First trigger at 2026-06-29T11:31:37Z; prior trigger was 2025-09-15 — ~9 months ago. Earlier-morning blips today (07:10, 07:55–08:10, 10:00–10:05) did not sustain long enough to page.
- **Replica 0 comparison:** ec2-54-188-57-60 / i-0d22f39bd3dd3171a, CPU ~5% mean over the same window — idle.

**Intervention #1** — I flagged the 5:1 replica query ratio (replica 1 vs replica 0) as a potential load-balancer anomaly. User corrected: "The shard traffic being distributed is expected, processor related flow, generally hit the 1 more than 0." Accepted as expected behavior; removed from findings.

### Indexing vs query throughput (15-min buckets)

SQL via `query-starrocks` against `log.search_query_log` (profiles, shard_id=21). Spike window 11:00–11:45 UTC, baseline 10:00–11:00 UTC. `callerid='index'` = indexing; all other callerids = query traffic.

| Bucket (UTC) | Query requests | Indexing requests |
|---|---|---|
| 10:00 | 12,292 | 2,415 |
| 10:15 | 9,116 | 2,046 |
| 10:30 | 9,969 | 2,003 |
| 10:45 | 7,764 | 1,572 |
| **11:00** | **17,562 ← SPIKE** | 2,960 |
| 11:15 | 16,929 | 2,024 |
| 11:30 | 19,986 (peak) | 3,520 |

Indexing was within normal range (~2–3.5k/15min) throughout. **Query throughput doubled at 11:00 UTC** and stayed elevated. Indexing NOT the driver.

### Source identification (callerid + group_id + env)

Final query: callerid, group_id, env for profiles shard 21, callerid != 'index', spike window 11:00–11:45 vs baseline 10:00–11:00. Rates normalized per-minute.

**Intervention #2** — I tried to resolve the `env` column via a `system_id` probe. User corrected: "you can get it from column 'env'". Abandoned the probe and used `env` directly.

Two concurrent sources found:

**Source 1 — env=github-ci:** Large parallel CI test suite kicked off at exactly 11:00 UTC. 15+ demo tenants (`eightfolddemo-*`) across a wide variety of callerids. `check_management_permission / eightfolddemo-chevron.com` was brand-new (zero in baseline). Classic parallel CI footprint.

| callerid | group_id | spike/min | baseline/min | ratio |
|---|---|---|---|---|
| get_implicit_employee_counts_of_roles | (none) | 47.4 | 24.0 | 1.98x |
| pipeline_v2_leads:recommended | eightfolddemo-pipline-ux.com | 16.9 | 6.6 | 2.58x |
| get_explicit_employee_counts_of_roles | (none) | 16.1 | 8.8 | 1.83x |
| pipeline_v2_step_count_leads:recommended | eightfolddemo-unittest.com | 10.2 | 4.1 | 2.53x |
| pipeline_v2_leads:recommended | eightfolddemo-rm-testing.com | 9.9 | 4.1 | 2.44x |
| get_associated_employee_docs_solr_response | eightfolddemo-careernavdemo.com | 9.6 | 2.8 | 3.50x |
| pipeline_v2_matched:recommended | eightfolddemo-sourcing-scheduling.com | 8.9 | 2.8 | 3.20x |
| check_management_permission | eightfolddemo-chevron.com | 4.2 | 0.0 | NEW |

**Source 2 — env=processor (Walmart):** All 4 Walmart `pipeline_v2_*` callerids tripled from the processor service simultaneously. Suggests a large pipeline batch job starting at 11:00 UTC.

| callerid | group_id | spike/min | baseline/min | ratio |
|---|---|---|---|---|
| pipeline_v2_leads:oneten-calibrated | eightfoldemployer-walmart.com | 38.5 | 13.0 | 2.95x |
| pipeline_v2_contacted:all_contacted | eightfoldemployer-walmart.com | 23.3 | 7.9 | 2.97x |
| pipeline_v2_applicants:oneten-clicked | eightfoldemployer-walmart.com | 17.1 | 7.0 | 2.44x |
| ideal-candidate-by-pos | eightfoldemployer-walmart.com | 14.8 | 7.6 | 1.94x |

### Report posted

Canvas created: https://eightfoldai.slack.com/docs/T1UL59A9M/F0BDE8UCYA3  
Thread reply posted: https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782734830048059

## Findings

Root cause: a coincidence of two query-load events at 11:00 UTC pushed profiles shard 21 replica 1 from ~10k to ~20k queries/15min, crossing the sustained 5-of-6 alarm threshold for the first time in ~9 months. Indexing was flat throughout.

Open questions routed in the report:
1. Was a CI run scheduled at 11:00? Should github-ci traffic hit production Solr?
2. ~~What triggered the Walmart processor pipeline_v2 surge?~~ **Answered:** Walmart positions file ingest at 09:32 UTC → `ingest_data_extract_operation` root SMID `2353af88` → fan-out of `pos_stats_v2` + `position_calibration` ops starting ~10:47 UTC. Root op traced via `search_query_log.sequence_message_id` → `processor_parent_msg_id` walk using `trace-processor-op`.

### Processor root op chain

```
ingest_data_extract_operation  [file_ingest_requests_queue,           09:32 UTC]  ← root
  └─ ingest_data_extract_operation  [recurring_file_ingest_requests_queue, 10:42 UTC]
       ├─ pos_stats_v2 × many        [batch_requests,  ~10:47+ UTC]  → pipeline_v2_* Solr callerids
       └─ position_calibration × N   [index_requests,  ~10:56+ UTC]  → ideal-candidate-by-pos Solr callerid
```

Owner of root op: `@EightfoldAI/dp-file-ingestion` — matched by CODEOWNERS rule (line 353) on `www/processor/ingest_data_extract_operation.py` (resolved via `processor.ingest_data_extract_operation` → `IngestDataExtractOperation` in op_registry.py:61).

Slack follow-up comment: https://eightfoldai.slack.com/archives/C07NZL0PL9K

## What could be compiled

- **Column name `callerid` (no underscore)** in `log.search_query_log` — already in the wiki page `learned/wiki/data-warehouse/search-query-log.md` presumably, but worth verifying `callerid` vs `caller_id` is explicit there.
- **`env` column in `log.search_query_log`** identifies the originating service (github-ci, processor, stage, www, etc.) — check if this is documented in the wiki page; it was not obvious from the schema without `DESCRIBE`.
- **Inflow-vs-drain pattern for Solr CPU** is symmetric to the queue-backed-up inflow-vs-drain pattern: indexing = inflow (callerid='index'), query = drain-side load. The oncall discipline for Solr CPU (confirm alarm → characterize CPU series → split indexing vs query → find callerid/group_id/env driver) could become a wiki page or be added to the Solr collection topology page.
