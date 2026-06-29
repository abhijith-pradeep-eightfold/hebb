---
name: query-solr-load
model: sonnet
description: Indexing-vs-query load breakdown for one Solr core+shard from log.search_query_log — the per-bucket indexing (callerid='index') vs query split, and the per-source callerid×group_id×env driver breakdown (spike window vs baseline, normalized per-minute). Use when diagnosing what drove a Solr replica's load or CPU beyond "is it hot": "did indexing or query traffic rise on profiles shard 21", "split index vs query per 15 min for this core+shard", "which callerid/tenant/env surged on positions shard 7 during the spike", "what feature or service doubled the query rate", or "is this caller new in the spike window". The analytical core of a "Solr CPU too high" oncall. Pairs with solr-shard-cpu (overlay the load split on the per-replica CPU curve) and trace-processor-op (when the surging env is `processor`, trace a culprit sequence_message_id to its root op). For arbitrary StarRocks SQL use query-starrocks; for processor-queue throughput use query-queue-throughput.
knowledge_required:
  - "[[../../../wiki/data-warehouse/search-query-log|log.search_query_log table]]"
knowledge_optional:
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
---

# Query Solr load (indexing-vs-query split + driver breakdown)

Time-bucketed and per-source aggregates of [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]] for one Solr `core` + `shard_id` — the analytical workhorse for a [[../../../wiki/oncall/solr-cpu-high|Solr CPU too high]] page. A replica's CPU is a **flow/rate** metric = the cost of **indexing + query** work, so the investigation **splits the load by stream first** (which kind of work rose), then breaks the rising stream down by **source** (which feature/tenant/service). The aggregate SQL lives in the shared util `hebb_utils.solr.query_log`; a **bundled, read-only runner** wraps it and is auto-allowed by the bash execution policy — no approval prompt.

## Steps

1. **Read the table page** (via `wiki-reader`): [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]] — especially the [[../../../wiki/data-warehouse/search-query-log#source-identifying-columns|source-identifying columns]] (`callerid`, where `callerid='index'` is the indexing stream; `env`, the originating service; `group_id`, the tenant) and that `t_create` is **UTC** (same clock as a CloudWatch CPU curve — no shift when correlating).

2. **Run the bundled runner** in the mode that answers the question (PYTHONPATH must root at `www/` — the util imports `datawarehouse`/`db`):
   - **`--mode split`** — per-bucket indexing (`callerid='index'`) vs query (all other callerids) for a `core`+`shard_id`. Overlay it on the per-replica CPU curve (`solr-shard-cpu`); the stream that rises with CPU is the driver, the flat one is exonerated.
     ```bash
     PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_solr_load.py" --mode split --core profiles --shard-id 21 --since "2026-06-29 10:00:00" --until "2026-06-29 11:45:00" --bucket-minutes 15
     ```
   - **`--mode drivers`** — the **source** breakdown: `callerid × group_id × env` over the spike window vs a baseline window, normalized **per-minute**, with a spike/baseline `ratio` (and `ratio=None` flagging a **NEW** source, zero in baseline). Defaults to the query stream (`--stream query`, i.e. `callerid<>'index'`); use `--stream index` or `--stream all` to break down the write stream. Narrow `--dims` (e.g. `--dims callerid,env`) to coarsen the grouping.
     ```bash
     PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/query_solr_load.py" --mode drivers --core profiles --shard-id 21 --since "2026-06-29 11:00:00" --until "2026-06-29 11:45:00" --baseline-since "2026-06-29 10:00:00" --baseline-until "2026-06-29 11:00:00"
     ```
   `--format json` emits machine-readable rows (the `split` timeseries feeds the `plot-result-set` skill directly).

3. **Read the output.** `split`: the bucket where one stream's count jumps and stays elevated locates the onset; compare its rise to the CPU curve's. `drivers`: the top `spike_per_min` rows with a high `ratio` (or `NEW`) are the sources that drove the surge; **scope the windows to the confirmed breach window** (from the CPU step) so a burst separates from heavy baseline traffic. When the surging `env` is **`processor`**, the queries were issued by processor ops — take a culprit row's `sequence_message_id` (the processor SMID) and walk it to the root op with `trace-processor-op`, then route with `codeowners-owner`.

## Notes

- **Use `query-starrocks` instead** for arbitrary read-only SQL against StarRocks (anything these two fixed shapes don't express). This skill is the parameterized indexing-vs-query split + per-source breakdown over `search_query_log`.
- **Use `query-queue-throughput`** for the analogous diagnostics over `processor_event_log` (a processor SQS queue's inflow-vs-drain, latency, parent attribution).
- **`t_create` is UTC.** Pass spike/baseline bounds as UTC literals — the same clock as the CloudWatch CPU curve you are correlating against (see [[../../../wiki/process/incident-metric-correlation|metric-correlation discipline]]).
- All reads go through `starrocks_utils.get_list` on the read-only StarRocks cluster; every interpolated value (core, shard_id, dims, timestamps, limit) is charset/format-validated — read-only by construction. In a region without StarRocks the run reports the region gate plainly rather than guessing.
