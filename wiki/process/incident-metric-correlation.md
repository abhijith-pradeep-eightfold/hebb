# Incident metric-correlation discipline

**Summary:** When debugging a metric-driven incident (e.g. a CPU alarm), **anchor on the real metric curve first** — confirm the breach is genuine and pin its true window/shape — and only then correlate that window against a secondary source (query load, deploys, etc.). Do not assume the alarm's narrative; verify it, then test causes against the confirmed window plus a baseline.

## The discipline

1. **Pull the primary metric and confirm the breach.** Get the alarm definition and the underlying timeseries (for a Solr CPU page: [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]]). Establish the **real spike window and shape** from the data, and decide whether it is a sustained breach or a one-minute blip. In the 2026-06-15 incident CPU genuinely sat at ~99% Average for ~15–20 min on replica 0 — a real breach that cleared the alarm's 5-of-6 300s threshold.

2. **Define the window and a baseline band.** Use the confirmed spike window (here 13:50–14:05 IST, with a 14:15 IST echo) and a nearby quiet baseline (13:00–13:40 IST) so "elevated" is measured against the host's own normal, not against zero.

3. **Correlate the candidate cause over that window.** Test the hypothesis (here: did query load drive the CPU spike?) against the secondary source — [[../data-warehouse/search-query-log|log.search_query_log]] — bucketed over the band, broken down by the dimensions that would localize a cause (tenant `group_id`, `shard_id`, `search_host`, `api`, `rows_requested`, latency).

4. **State the verdict from the data, including the negative.** A non-correlation is a real finding. Here query throughput did **not** correlate with the CPU spike:
   - profiles-core volume was **flat** (~12–20k/min) and actually in a **trough** (~13–16k/min) *during* the spike, below the earlier 13:05–13:20 peak.
   - shard-21 load during the spike window was **≤ baseline** (baseline `eightfolddemo-*` sandbox tenants drove *more* shard-21 load than the spike window did).
   - the only large-fanout reads (`rows_requested = 300000`) were spread across *other* shards (58, 50, 74), not concentrated on shard 21.
   - Conclusion: the CPU breach was real but **off-query-path** — likely Solr merge / GC / host-local work not visible in the query log.

## Watch the timezones — the two sources disagreed

The primary and secondary sources were in **different timezones**, which is the easiest way to mis-correlate:

- **CloudWatch** metric/alarm times are **UTC**.
- **`log.search_query_log.t_create` is stored in IST (local), not UTC** — confirmed by a warehouse-`NOW()` sanity row (it read 17:17, matching IST wall-clock, not UTC ~11:48). See [[../data-warehouse/search-query-log#timestamp-semantics-gotcha|the table page]].

So correlating the two required shifting the CPU curve **+5:30 to IST** (or the SQL window **−5:30 to UTC**). Always pin each source's timezone with a sanity check before overlaying them; a CPU/volume overlay on a twin axis is only meaningful once both are on the same clock.

## Related

- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + metric access]] — pulling and confirming the primary metric.
- [[../solr/solr-collection-topology|Solr collection topology]] — what the alarm coordinate means; which hosts a shard spans.
- [[../data-warehouse/search-query-log|log.search_query_log table]] — the secondary source; `t_create` timezone and the scoping columns used to break down load.
- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — sanity-row + `time_slice` bucketing techniques used for the correlation.
- [[coordinator-authority|Coordinator authority and user confirmation]] — complementary discipline: coordinators cannot assert user confirmation.

---
*Sources:* witness `inputs/2026-06-24-solr-cpu-spike-debug.md` (`[17:06]` revised plan to confirm CPU first, `[17:18]` sanity row pinning IST, `[17:25]` band/tenant/host breakdowns, `[17:30]` overlay + correlation verdict).
