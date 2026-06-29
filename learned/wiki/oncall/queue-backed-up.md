# Queue backed up (oncall ticket type)

**Summary:** A PagerDuty `[<region>] Queue backed up-<queue> (<region>)` page (High urgency, "Core Infra") fires when an SQS queue consumed by the `www` processor holds more in-flight messages than its consumers drain. Queue depth is a **stock** (backlog = ∫(inflow − drain)), so a depth spike has **two** possible causes — an **inflow surge** *or* a **drain dip** — and the investigation forks between them before hunting a driver op. This page covers the backing CloudWatch alarm, how to pull the queue-depth metric, the **inflow-vs-drain fork**, and the per-branch flow to find and route the cause. It is a concrete instance of the [[oncall-investigation|oncall investigation discipline]].

## The alarm

The page is backed by a CloudWatch **metric-math** alarm:
- Expression `e1 = SUM(METRICS())` over a single metric `m1` = **`AWS/SQS · ApproximateNumberOfMessagesVisible`**, dimension **`QueueName = <queue>`**, Stat **Maximum**, Period **900s** (15 min).
- Trips at **`SUM ≥ 50000`** (`GreaterThanOrEqualToThreshold`) for **4 datapoints** → ~60 min of sustained backlog. (Threshold observed for `ai_interview_op_queue`; other queues may differ — read the alarm.)

**Method to read it** (metric-math alarms have **null** `MetricName`/`Namespace` at top level — the real metric is inside the `Metrics` array):
```bash
aws cloudwatch describe-alarms --region <region> \
  --alarm-name-prefix "[<region>] Queue backed up-<queue>" \
  --query "MetricAlarms[].{Threshold:Threshold,Op:ComparisonOperator,Period:Period,Eval:EvaluationPeriods,DP:DatapointsToAlarm,State:StateValue,Reason:StateReason}"
aws cloudwatch describe-alarms --region <region> \
  --alarm-name-prefix "[<region>] Queue backed up-<queue>" --query "MetricAlarms[].Metrics"
```

## The metric — characterize the spike

```bash
aws cloudwatch get-metric-statistics --region <region> \
  --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions Name=QueueName,Value=<queue> \
  --start-time <ISO8601Z> --end-time <ISO8601Z> \
  --period 900 --statistics Maximum Average \
  --query "sort_by(Datapoints,&Timestamp)[].{t:Timestamp,max:Maximum,avg:Average}" --output table
```
CloudWatch timestamps are **UTC**. Establish baseline → onset → peak (vs the 50k threshold) → decay, and decide sustained breach vs one-minute blip. The shape **does not** tell you the cause: depth is a backlog, so a sustained climb can be a producer outpacing consumers **or** consumers slowing under a steady producer. Do not assume "bulk fan-out" — confirm it in the next section.

To pull this in one step from an alarm name or InstanceId, **use the `inspect-cloudwatch-metric` skill** (it handles CloudWatch alarm + metric pulls, including metric-math/queue-depth alarms, not only EC2 CPU).

## Depth is a stock — fork on inflow vs drain

Queue depth = backlog = the running integral of **(inflow − drain)**. So the **first** diagnostic is not "what flooded it" but **which side moved** — did inflow surge, or did drain dip? Overlay, per time bucket (UTC, same clock as CloudWatch — see [[../process/incident-metric-correlation#stock-vs-flow-metrics|stock vs flow]]):

- **`dispatched_in`** = `COUNT(message_dispatched)` for the queue per bucket (inflow rate), and
- **`processed_out`** = `COUNT(message_processed)` for the queue per bucket (drain rate).

The **net delta (in − out)** per bucket, summed, reconstructs the CloudWatch depth curve: backlog-building buckets have in > out, draining buckets out > in. **To pull this overlay, use the `query-queue-throughput` skill** (per-bucket inflow vs drain + net delta for a queue).

- **Inflow surge** (inflow rises with the depth) → go to **Inflow branch** below (find the driver op that flooded it).
- **Drain dip** (inflow flat/falling while depth rises; the delta is drain-driven) → go to **Drain branch** below. A flat inflow against a rising depth is **not** a contradiction with "there is a storm" — see the downstream/upstream corollary next.

### Downstream vs upstream: where a storm's dispatch spike actually lands

A bulk fan-out shows its dispatch spike on the **upstream** queue, *not* necessarily on the backed-up downstream queue. Downstream messages (e.g. `index_requests`) are emitted only **as upstream ops get processed** — so they are gated by throughput, and a surge accumulates as **depth on the upstream queue** rather than a higher downstream dispatch *rate*. If the backed-up queue's own inflow is flat, look one hop upstream (the queue whose op produced these messages — see the Inflow branch's parent attribution) for the real dispatch spike. A **simultaneous drain dip across two unrelated queues** in the same bucket is a **fleet-wide** processing dip (shared backend/infra), distinct from a queue-specific latency hit.

## Inflow branch — what flooded the queue

The queue is drained by the `www` processor; each message logs to [[../processor/processor-event-log|processor_event_log]] with a `queue_name` and an `operation0`.

**(a) Direct composition (what is *in* the queue).** Break the **`message_dispatched`** events on the queue down by `operation0 × group_id` over the spike window to find the outlier op/tenant:

```sql
SELECT operation0, group_id, COUNT(*) AS cnt
FROM log.processor_event_log
WHERE TRIM(queue_name) = '<queue>'        -- queue_name may carry a trailing space
  AND event_type = 'message_dispatched'
  AND t_create >= '<start>' AND t_create <= '<end>'
GROUP BY operation0, group_id ORDER BY cnt DESC
```
**Gotcha:** `processor_event_log.queue_name` can store a **trailing space** (e.g. `'ai_interview_op_queue '`) — the SQS `QueueName` dimension does not. Use `TRIM(queue_name)`. To run this, **use the `query-processor-event-log` skill** (filter by queue / event_type / window and aggregate by op).

**(b) Parent attribution (what *produced* the queue's contents).** To rank the **driver ops** — the parents that dispatched the flood — count the **distinct parent messages**, and **do NOT filter the outer query on `event_type`**:

```sql
SELECT operation0, COUNT(DISTINCT processor_msg_id) AS distinct_msgs
FROM log.processor_event_log
WHERE t_create >= '<wide-start>' AND t_create <= '<end>'
  AND processor_msg_id IN (
        SELECT DISTINCT processor_parent_msg_id FROM log.processor_event_log
        WHERE event_type = 'message_dispatched' AND TRIM(queue_name) = '<queue>'
          AND t_create >= '<start>' AND t_create <= '<end>'
          AND processor_parent_msg_id IS NOT NULL)
GROUP BY operation0 ORDER BY distinct_msgs DESC
```
**Why distinct-msg over all event types, not `COUNT(*)` of `message_dispatched`:** the right per-op metric for a *parent* is `COUNT(DISTINCT processor_msg_id)` (≈1 message, many event rows). Filtering the **outer** query on `event_type='message_dispatched'` **undercounts** scheduled/retry parents — a retry message is dispatched with a backoff delay (`schedule_after_secs`, see [[../processor/trigger-event-fanout|trigger_event fan-out]]), so its own `message_dispatched` row lands *outside* the window even though its fan-out lands inside. The `message_dispatched` filter is right for **direct composition** (a) — counting the messages *in* the queue — but wrong for **parent attribution** (b). In the witnessed `index_requests` incident this distinction flipped the #1 driver from an apparent 5th place to the true top (`trigger_event`, 120,176 distinct vs an undercounted 5,306). **The `query-queue-throughput` skill** runs this correct parent breakdown.

Then **trace and route** the driver:
1. **Root op:** take a representative culprit SMID and walk `processor_parent_msg_id` to the parentless root — **use the `trace-processor-op` skill**.
2. **Owner:** map the root and culprit `operation0` to their source files via [[../processor/op-registry|op_registry]], then resolve each file's owners via [[../repo/codeowners-ownership|CODEOWNERS ownership]] (with git-author fallback) — **use the `codeowners-owner` skill**. Route the incident to the owning team / author.

## Drain branch — when inflow is flat but depth rose

If the fork shows inflow flat/falling while depth climbed, the backlog is a **throughput** problem, not a producer surge. Work down the drain-side causes (each rules a cause in or out):

1. **Op errors / reroutes** — failures retry and re-occupy the queue. Break `message_processed` down by `operation0 × status` over the breach window: **use `query-processor-event-log --event-type message_processed --count-by operation0,status`**. Near-100% `PASS` (witnessed: ~99.95%, ~0.05% non-PASS) ⇒ errors are *not* the cause; reroute markers (`REROUTE_TO_HIGH_MEM`, `SEARCH_ERROR`) at background levels are normal.
2. **Per-message latency** — slower processing on a fixed worker pool halves effective drain. Pull per-bucket `percentile_approx(latency_milliseconds, 0.5)` / `0.9` (NOT `MAX` — `latency_milliseconds` has a pathological multi-million-ms tail; see [[../processor/processor-event-log#latency_milliseconds|latency_milliseconds]]), optionally `--by operation0` / `--by group_id`, via **the `query-queue-throughput` skill**. A drain trough that coincides with a p90 latency spike (witnessed: index p90 ~3× during troughs) is the throttle. Crucially, `latency_milliseconds` is **op processing latency** (dequeue→done), *not* queue wait — so a rising value is a genuine *cause* of reduced drain, not the backlog re-expressed (queue wait is the separate `lag_seconds` field).
3. **Worker-pool contention** — does a queue-group sibling steal workers? Resolve the queue's [[../processor/queue-worker-pool-segregation|worker-pool groups]] and its sibling queues, then test whether any sibling's inbound spiked in-window. **Use the `resolve-queue-worker-pool` skill** to get the groups + siblings, then `query-queue-throughput` for each sibling's inflow. (Witnessed: no sibling spiked — contention ruled out.)
4. **Volume × latency (capacity share, not count share)** — a *small* request-count rise in a *very slow* tenant can dominate the pool. Aggregate inflow can stay flat while a few high-latency tenants saturate workers. Rank tenants by **`total_proc_sec = SUM(latency_milliseconds)/1000`** (and **worker-equivalents = total_proc_sec / window_seconds**), not by raw count: in the witnessed incident a tenant up only ~2× in count but at p90 ~66s consumed ~29% of the worker pool, and three such tenants ≈ 50% — their synchronized burst aligned exactly with the drain trough and depth peak. **The `query-queue-throughput --by group_id` latency mode** produces this.
5. **Backend confirmation** — if latency points at indexing, confirm the Solr backend is hot: **use the `solr-shard-cpu` skill** on the relevant index shards (see [[../solr/solr-collection-topology|Solr collection topology]]).

## Reading the driver breakdown — burst vs. baseline noise

The `operation0 × group_id` count is over the **breach window**, but a queue can carry heavy *baseline* traffic for the same op outside the spike. Widen the window much past the breach and the burst hides under that baseline — a high-volume op (e.g. `index`) looks the same whether it spiked or not. **Narrow the breakdown to the confirmed breach window** (from the metric step) so the burst separates from baseline; a driver that dispatched most of its *whole-day* volume inside that window is the spike, not the baseline. Same metric-first discipline: the [[../infra/cloudwatch-cpu-alarm|metric]] gives you the true window before you attribute a cause.

## Witnessed incidents

| | `ai_interview_op_queue` (1st) | `batch_requests`, us-west-2 (2nd) | `index_requests`, us-west-2 (3rd) |
|---|---|---|---|
| **Threshold** | `SUM ≥ 50000`, 4 datapoints | `SUM ≥ 50000`, 4 datapoints | `SUM ≥ 50000`, 4 datapoints |
| **Spike shape** | sudden onset, near-linear climb | sudden ramp + sharp drain; peak **77,365 (155%)**, 4×900s buckets | sustained breach **15:00–18:30 UTC (~3.5h)**, peak **114,770 (230%)**, two humps |
| **Cause side** | **inflow** (single-tenant fan-out) | **inflow** (multi-tenant `index` burst) | **drain** — inflow *flat* (~55–94k/15min, no surge); backlog = drain dips |
| **Driver** | one op × one tenant = **95.6%** | `index` across **two** tenants (≈30k+≈21.6k) on top of baseline | top *parent* `trigger_event` (120,176 distinct; bulk-ATS interceptor fan-out) — but the **proximate** cause was index-op latency (p90 ~3×), not the inflow |
| **Root op** | `sync_ats` (queue `ingest_sync_requests`) | `employee_role_association_manager` → `_batch` → `index` (two-hop) | `sync_ats` → `batch_store_and_index` → `trigger_event` (re-seed); storm = interceptor `post_save` fan-out |
| **Owners** | (per that incident) | `index` → `@EightfoldAI/core-search` `@EightfoldAI/dp-data-flow`; `employee_role_association_*` → `hpatel@eightfold.ai` | `sync_ats` + `write_back_sor.py` → `@EightfoldAI/dp-integrations`; `batch_store_and_index` → `@EightfoldAI/dp-data-flow`; `trigger_event_operation.py` → no CODEOWNERS rule |

The 3rd incident is the cautionary one: the inflow-branch headline (`trigger_event` flooded it → route to its owner) was a **real upstream load source but the wrong proximate cause** of the *queue depth* — that was drain-side index latency on a fixed worker pool. Always run the inflow-vs-drain fork before committing to a driver. The 2nd's lineage (`manager → batch → index`) was recovered by [[../processor/tracing-processor-op-lineage|walking `processor_parent_msg_id`]]; op→file→owner came from [[../processor/op-registry|op_registry]] + [[../repo/codeowners-ownership|CODEOWNERS]]. Source anchors: `www/processor/op_registry.py:17`/`:142`/`:143` (2nd), `:42`/`:67`/`:125` (3rd); the write_back retry loop at `www/ats/write_back_sor.py:286,291-304,324-334`.

## Reporting the result

Report an oncall ticket as a **detailed, table-structured report**, not a prose summary — see the shared format on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For *Queue backed up* the tables are: alarm config; spike characterization (baseline → onset → peak vs threshold → decay); **inflow-vs-drain rate** over the window ±2h (the fork evidence); driver breakdown (`operation0 × group_id`, and parent attribution); op-lineage (root → target, per hop); and ownership/routing (op → file → owner). For a drain-side finding add the **latency-by-op/group** table (p50/p90 + `total_proc_sec`) and the **per-tenant volume × latency** table. To post it back to the PagerDuty thread, **use the `oncall-post-report` skill** (Canvas + concise threaded reply; it confirms the destination first and renders owner/customer names as plain text so the post pages no one).

## Related skills

- `oncall-queue-backed-up` — the high-level runbook for this ticket type; start here to run the whole investigation (it runs the inflow-vs-drain fork, then the per-branch building blocks below).
- `inspect-cloudwatch-metric` — use it to pull the queue-depth alarm definition + the `ApproximateNumberOfMessagesVisible` curve and characterize the spike.
- `query-processor-event-log` — use it to break `message_dispatched` down by `operation0`/`group_id` (direct composition) and `message_processed` by `operation0`/`status` (drain-branch op errors).
- `query-queue-throughput` — use it for the inflow-vs-drain overlay, the correct parent-attribution breakdown, and per-bucket latency (p50/p90 + `total_proc_sec`, by op/group).
- `resolve-queue-worker-pool` — use it (drain branch) to find the queue's worker-pool groups, capacities, and sibling queues to test for noisy-neighbor contention.
- `trace-processor-op` — use it to walk a culprit SMID to its root processor op.
- `codeowners-owner` — use it to resolve the owning team/author of the root/culprit op's source file.
- `oncall-post-report` — use it to post the finished table-structured report back to the PagerDuty Slack thread (Canvas + concise threaded reply), with a confirm-before-post gate and plain-text (non-paging) owner references.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline.
- [[../processor/processor-event-log|processor_event_log]] · [[../processor/tracing-processor-op-lineage|tracing processor-op lineage]] — the event log and the parent-walk.
- [[../processor/queue-worker-pool-segregation|Processor worker-pool / queue-group segregation]] — which queues share a worker pool (drain-branch contention check).
- [[../processor/trigger-event-fanout|trigger_event fan-out]] — the interceptor re-seed + write_back retry mechanisms behind the witnessed storm.
- [[../processor/op-registry|op_registry]] · [[../repo/codeowners-ownership|CODEOWNERS ownership]] — op→file→owner routing.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — metric-first method; stock-vs-flow correlation for backlog metrics (CloudWatch and warehouse `t_create` are both UTC).
- [[../solr/solr-collection-topology|Solr collection topology]] — the indexing backend behind `index`/`entity_index` latency.
