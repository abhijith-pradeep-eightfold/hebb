# Queue backed up (oncall ticket type)

**Summary:** A PagerDuty `[<region>] Queue backed up-<queue> (<region>)` page (High urgency, "Core Infra") fires when an SQS queue consumed by the `www` processor accumulates more in-flight messages than its consumers can drain. This page covers the backing CloudWatch alarm, how to pull the queue-depth metric, and the four-step flow to find and route the cause. It is a concrete instance of the [[oncall-investigation|oncall investigation discipline]].

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
CloudWatch timestamps are **UTC**. Establish baseline → onset → peak (vs the 50k threshold) → decay. A sudden onset followed by a steep, near-linear climb over hours (then slow drain) is the classic shape of a **producer outpacing maxed-out consumers** — a bulk fan-out, not a blip.

To pull this in one step from an alarm name or InstanceId, **use the `inspect-cloudwatch-metric` skill** (it handles CloudWatch alarm + metric pulls, including metric-math/queue-depth alarms, not only EC2 CPU).

## Find the driver — what flooded the queue

The queue is drained by the `www` processor; each message logs to [[../processor/processor-event-log|processor_event_log]] with a `queue_name` and an `operation0`. Break the **`message_dispatched`** events down by `operation0 × group_id` over the spike window to find the outlier op/tenant:

```sql
SELECT operation0, group_id, COUNT(*) AS cnt
FROM log.processor_event_log
WHERE TRIM(queue_name) = '<queue>'        -- queue_name may carry a trailing space
  AND event_type = 'message_dispatched'
  AND t_create >= '<start>' AND t_create <= '<end>'
GROUP BY operation0, group_id ORDER BY cnt DESC
```

**Gotcha:** `processor_event_log.queue_name` can store a **trailing space** (e.g. `'ai_interview_op_queue '`) — the SQS `QueueName` dimension does not. Use `TRIM(queue_name)`. To run this, **use the `query-processor-event-log` skill** (filter by queue / event_type / window and aggregate by op). In the witnessed incident one op × one tenant was **95.6%** of the window.

## Trace and route

1. **Root op:** take a representative culprit SMID and walk `processor_parent_msg_id` to the parentless root — **use the `trace-processor-op` skill**. (Witnessed: `sync_ats` (root, queue `ingest_sync_requests`) directly dispatched the culprit `ai_interview_competency_generation_operation` onto the queue.)
2. **Owner:** map the root and culprit `operation0` to their source files via [[../processor/op-registry|op_registry]], then resolve each file's owners via [[../repo/codeowners-ownership|CODEOWNERS ownership]] (with git-author fallback) — **use the `codeowners-owner` skill**. Route the incident to the owning team / author.

## Related skills

- `oncall-queue-backed-up` — the high-level runbook for this ticket type; start here to run the whole investigation, which sequences the four skills below.
- `inspect-cloudwatch-metric` — use it to pull the queue-depth alarm definition + the `ApproximateNumberOfMessagesVisible` curve and characterize the spike.
- `query-processor-event-log` — use it to break `message_dispatched` down by `operation0`/`group_id` for a queue over the incident window.
- `trace-processor-op` — use it to walk a culprit SMID to its root processor op.
- `codeowners-owner` — use it to resolve the owning team/author of the root/culprit op's source file.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline.
- [[../processor/processor-event-log|processor_event_log]] · [[../processor/tracing-processor-op-lineage|tracing processor-op lineage]] — the event log and the parent-walk.
- [[../processor/op-registry|op_registry]] · [[../repo/codeowners-ownership|CODEOWNERS ownership]] — op→file→owner routing.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — metric-first method (and the UTC/IST timezone caution).
