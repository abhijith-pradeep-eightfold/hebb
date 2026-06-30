---
name: trace-solr-query-to-op
model: sonnet
description: Trace a Solr core+shard's processor-issued query traffic back to the root processor ops behind it, in one step. Given a core + shard_id + UTC window (+region), it pulls the env='processor' query rows, groups them by sequence_message_id, walks each SMID to its root op, and reports the root-op chains by query volume. Use when a task asks "what processor ops are driving the queries hitting profiles shard 10", "trace this shard's env=processor query traffic to its root ops", "which processor ops issued the queries on positions shard 7", or as the proactive follow-on after a query-solr-load drivers breakdown surfaces env=processor as a contributor — don't stop at the breakdown, run this to name the ops. For the indexing-vs-query / per-source breakdown itself use query-solr-load; to trace a single known SMID use trace-processor-op.
knowledge_required:
  - "[[../../../wiki/data-warehouse/search-query-log|log.search_query_log table]]"
  - "[[../../../wiki/processor/tracing-processor-op-lineage|Tracing processor-op lineage]]"
knowledge_optional:
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
---

# Trace Solr query traffic to its root processor ops

When a Solr core+shard receives **processor-issued** query traffic (`env='processor'` in [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]]), the real answer to "what is driving this load" is not the `callerid`/`group_id` breakdown — it is the **processor op** behind those queries. Each `env='processor'` row carries a `sequence_message_id` that **is** the processor SMID that issued the query (the join key to [[../../../wiki/processor/processor-event-log|processor_event_log]]); walking that SMID's `processor_parent_msg_id` chain reaches the [[../../../wiki/processor/tracing-processor-op-lineage|root op]].

That chain — *pull the env=processor SMIDs → walk each to its root op → group identical chains* — has **no decision between its steps**, so it is one bundled, read-only script (not a sequence the agent must drive by hand). It reuses the same shared utils as the constituent skills: `hebb_utils.solr.query_log.processor_query_smids` (the SMID pull, shared with `query-solr-load`) and `hebb_utils.processor.event_log.walk_parent_chain` (the walk, shared with `trace-processor-op`). Both run in one process. Anchored under the skill dir, the runner is auto-allowed by the bash execution policy — no approval prompt.

## Steps

1. **Read the grounding wiki pages** (via `wiki-reader`): [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]] — the [[../../../wiki/data-warehouse/search-query-log#sequence_message_id|`sequence_message_id` bridge]] and that `t_create` is **UTC** — and [[../../../wiki/processor/tracing-processor-op-lineage|tracing processor-op lineage]] — the walk and the [[../../../wiki/processor/tracing-processor-op-lineage#non-uuid-parent-the-walk-terminates-early|non-UUID `{group_id}-hex` parent]] terminal (e.g. `import` ops end the chain there; that is the deepest knowable op, not a failure).

2. **Run the bundled runner** with the core, shard, and UTC window:
   ```bash
   PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/trace_solr_query_ops.py" --core profiles --shard-id 10 --since "2026-06-28 00:00:00" --until "2026-06-29 00:00:00" --region eu-central-1
   ```
   - **`PYTHONPATH="$CODE_BASE/www"`** (not `$CODE_BASE`): the warehouse utils resolve `www`-rooted config — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
   - Flags: `--limit N` (max distinct SMIDs to pull then trace, default 500), `--max-depth N` (per-chain walk cap, default 50), `--format json` (machine-readable), **`--region`** (sets `EF_DEFAULT_REGION`; valid: `us-west-2`, `eu-central-1`, `ca-central-1`, `ap-southeast-2`, `westus2`).
   - **Scope the window tightly.** The trace is the slow part — one warehouse round-trip per hop per distinct SMID (tens of seconds each). A narrow window keeps the distinct-SMID count (and runtime) bounded; widen only if needed.

3. **Read the output.** Rows are grouped by their **root→target op trace** (identical chains collapse into one group), highest query volume first. Each group shows `query_cnt` (how many query rows that chain produced), `distinct_smids`, the `root_op`, the tenants (`group_id`s) and `callerid`s involved, and sample SMIDs. An empty result means this core+shard had **no** `env='processor'` query traffic in the window (a real finding — the shard isn't being driven by processor ops). A group whose `op_trace` ends at an `import`-style op reflects a chain that terminated at a non-UUID parent (the deepest knowable op).

4. **Route to an owner.** Feed a group's `root_op` to `codeowners-owner` (it maps an `operation0` to its source file via `op_registry`, then to the owning team) to find who to ping.

## Notes

- **Constituents stay independently usable.** For only the indexing-vs-query split or the `callerid × group_id × env` breakdown, use `query-solr-load`. For tracing one already-known SMID, use `trace-processor-op`. This skill is the fused breakdown→trace path for the common "which processor ops drive this shard's queries" question.
- A bare `shard_id` does not identify a collection (shard numbering is per-collection) — discover the `core` first if only given a shard number (see [[../../../wiki/data-warehouse/search-query-log#discovering-the-core-for-a-shard_id|discovering the core for a shard_id]]); `query-starrocks` runs that one-line discovery query.
- Read-only by construction: the SMID pull issues a single validated `SELECT`; the walk issues `processor_msg_id`-keyed `SELECT`s it builds itself. Nothing is hardcoded per region.
