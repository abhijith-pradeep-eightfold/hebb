# Incident metric-correlation discipline

**Summary:** When debugging a metric-driven incident (e.g. a CPU alarm), **anchor on the real metric curve first** — confirm the breach is genuine and pin its true window/shape — and only then correlate that window against a secondary source (query load, deploys, etc.). Do not assume the alarm's narrative; verify it, then test causes against the confirmed window plus a baseline.

## The discipline

1. **Pull the primary metric and confirm the breach.** Get the alarm definition and the underlying timeseries (for a Solr CPU page: [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]]). Establish the **real spike window and shape** from the data, and decide whether it is a sustained breach or a one-minute blip. In the 2026-06-15 incident CPU genuinely sat at ~99% Average for ~15–20 min on replica 0 — a real breach that cleared the alarm's 5-of-6 300s threshold.

2. **Define the window and a baseline band.** Use the confirmed spike window (here 08:20–08:35 UTC, with an 08:45 UTC echo) and a nearby quiet baseline (07:30–08:10 UTC) so "elevated" is measured against the host's own normal, not against zero. Both the CloudWatch curve and the [[../data-warehouse/search-query-log|log.search_query_log]] `t_create` column are in **UTC** (see the timezone section below), so the window is the same literal on both sources.

3. **Correlate the candidate cause over that window.** Test the hypothesis (here: did query load drive the CPU spike?) against the secondary source — [[../data-warehouse/search-query-log|log.search_query_log]] — bucketed over the band, broken down by the dimensions that would localize a cause (tenant `group_id`, `shard_id`, `search_host`, `api`, `rows_requested`, latency).

4. **State the verdict from the data, including the negative.** A non-correlation is a real finding. Here query throughput did **not** correlate with the CPU spike:
   - profiles-core volume was **flat** (~12–20k/min) and actually in a **trough** (~13–16k/min) *during* the spike, below the earlier 07:35–07:50 UTC peak.
   - shard-21 load during the spike window was **≤ baseline** (baseline `eightfolddemo-*` sandbox tenants drove *more* shard-21 load than the spike window did).
   - the only large-fanout reads (`rows_requested = 300000`) were spread across *other* shards (58, 50, 74), not concentrated on shard 21.
   - Conclusion: the CPU breach was real but **off-query-path** — likely Solr merge / GC / host-local work not visible in the query log.

## Timezones

All warehouse table timestamps — `log.search_query_log.t_create`, `processor_event_log.t_create`, and the other `log.*` tables — are stored in **UTC**, and CloudWatch metric/alarm times are **UTC**. So the metric and warehouse sources share one clock: overlay them directly, no shift. A CPU spike at 08:20–08:35 UTC is matched against `t_create` literals `08:20–08:35` directly.

## Stock vs flow metrics

Some alarm metrics are a **flow** (a rate — CPU%, queries/min) and some are a **stock** (a level that accumulates — a queue's `ApproximateNumberOfMessagesVisible`, a disk-fill, a backlog). For a **stock metric the correlation must overlay both sides of the integral**, because the level is `∫(inflow − drain)`: a single "candidate cause" timeseries is not enough. Bucket and overlay **inflow** *and* **drain** over the window + baseline, and compare their **net delta** per bucket against the stock curve.

The non-correlation rule sharpens here: if **inflow is flat** while the stock spikes, that is the finding — it redirects you from the producer to the **drain side** (slower/fewer consumers, higher per-item latency, capacity contention). In the 2026-06-23 `index_requests` backup the inflow rate was flat (~55–94k/15min) across the whole window while depth doubled the threshold; the net-delta buckets matched the depth curve exactly and pinned the cause to a drain dip, not a producer surge. See [[../oncall/queue-backed-up#depth-is-a-stock-fork-on-inflow-vs-drain|Queue backed up → inflow vs drain]] for the worked queue-depth application (and the corollary that a storm's dispatch spike can land on the *upstream* queue, leaving the backed-up queue's own inflow flat). Both inflow and drain come from [[../processor/processor-event-log|processor_event_log]] `t_create` (UTC) — same clock as the CloudWatch stock curve, overlay directly.

## Related

- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — the stock-metric (queue-depth) worked example: inflow-vs-drain fork and the drain-side diagnostics.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]] — pulling and confirming the primary metric.
- [[../solr/solr-collection-topology|Solr collection topology]] — what the alarm coordinate means; which hosts a shard spans.
- [[../data-warehouse/search-query-log|log.search_query_log table]] — the secondary source; `t_create` timezone and the scoping columns used to break down load.
- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — sanity-row + `time_slice` bucketing techniques used for the correlation.

---
*Sources:* witness `inputs/2026-06-24-solr-cpu-spike-debug.md` (`[17:06]` revised plan to confirm CPU first, `[17:25]` band/tenant/host breakdowns, `[17:30]` overlay + correlation verdict).
