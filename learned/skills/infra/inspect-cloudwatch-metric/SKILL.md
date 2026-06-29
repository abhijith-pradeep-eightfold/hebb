---
name: inspect-cloudwatch-metric
model: sonnet
description: Pull a CloudWatch alarm definition and its backing metric timeseries via read-only AWS CLI, then tabulate the series and flag breach buckets — for EC2 host CPU (`CPUUtilization`), SQS queue depth (`AWS/SQS ApproximateNumberOfMessagesVisible`, metric-math), or Elastic Beanstalk ALB host health (`AWS/ApplicationELB` HealthyHostCount/UnHealthyHostCount — the metric-math `UnHealthy − Healthy ≥ 0` "Host Unhealthy" alarm, where the breach signal is the difference of two series). Use whenever you need to confirm or characterize an alarm against the real metric curve — a "Solr CPU Util Too High" PagerDuty page, an EC2 CPU spike, a "Queue backed up" page, a "Host Unhealthy" / EB health-check page, or any CloudWatch alarm you want to verify — to establish the true spike window and shape (sustained breach vs. one-minute blip) before correlating it to anything else. Reach for this whenever a task hands you a CloudWatch alarm name, an EC2 instance/host, an SQS queue, an EB environment ALB, or a PagerDuty CPU/queue/host-health incident and asks what actually happened. Also use as the second step when you have already resolved DNS hostnames to InstanceIds (e.g. via solr-shard-dns-lookup) and want to pull the CPU curve — skip describe-alarms and go straight to get-metric-statistics. It can also pull the alarm's **state-transition history** to answer "is this page chronic or rare" — the most recent trigger (this incident's onset), the prior trigger, and the gap between them — for any CloudWatch alarm (CPU, queue depth, host health, etc.).
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
  - "[[../../../wiki/oncall/host-unhealthy|Host unhealthy (oncall)]]"
---

# Inspect CloudWatch alarm + metric (CPU or queue depth)

Confirm a host-CPU alarm against the real metric. The access facts and the alarm config live in the wiki ([[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]]); the runtime judgment this skill carries is **which alarm, which instance, and which window** to pull, and **reading the curve** (sustained breach vs. blip). The two AWS calls are read-only telemetry; the deterministic tabulation is a **bundled script** — `scripts/analyze_cpu_metrics.py` — that runs unattended on the saved JSON.

## When to anchor on this first

A metric alarm tells a story; verify it before acting on it. Pull the alarm definition and the real CPU curve, pin the true spike window and shape, and *then* correlate a candidate cause (query load, a deploy) over that window — see [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]]. A non-correlation is a real finding.

## Entry points

There are two ways to arrive at this skill:

- **From a CloudWatch alarm name or PagerDuty page** (the common case): run the CPU pull with `--alarm-name-prefix` (Step 2) — it resolves the `InstanceId` from the alarm for you.
- **From EC2 DNS hostnames** (e.g. after running **`solr-shard-dns-lookup`**): you already have InstanceIds; pass them to the CPU pull with `--instance-id` (Step 2) and it skips the alarm read.

> If the task starts from a **collection + shard ID** and just wants that shard's CPU, the combined **`solr-shard-cpu`** skill runs the whole pipeline (host lookup → per-replica CPU) in one call — use it instead of running the two skills by hand. This skill stays the right choice when you start from an alarm name or a known InstanceId, or want to characterize a known spike.

## Steps

1. **Read the access pattern from the wiki** (via `wiki-reader`):
   - [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — environment/reachability, the alarm config (75% Average, 5-of-6 300s periods), the `InstanceId` dimension, and that CloudWatch times are **UTC**.
   - [[../../../wiki/solr/solr-collection-topology|Solr collection topology]] — if this is a Solr page: how a `<collection> shard N replica R` alarm maps to one EC2 host, and that a shard spans multiple replica hosts.

2. **Pull the alarm + CPU curve in one bundled call** (read-only, unattended). `pull_cpu.py` runs the whole CPU path — `describe-alarms` (to resolve each matching replica alarm's `InstanceId` + threshold + current state) and the per-instance `get-metric-statistics` + breach analysis — so no raw `aws` command is issued by hand:
   ```bash
   "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_cpu.py" --alarm-name-prefix "<alarm name prefix>" --region <region> --start <ISO8601Z> --end <ISO8601Z>
   ```
   A single name-prefix can match **multiple** sibling alarms (one per replica); the script reports each. If you already have InstanceIds (e.g. after **`solr-shard-dns-lookup`**), skip the alarm read with `--instance-id i-... [--instance-id i-...]` instead. `--period 60` (default) gives one-minute buckets; `--per-bucket` emits one row per period instead of the aggregate summary. The script also prints the alarm's `StateValue`/`StateReason` — but do **not** treat the alarm's last-transition time as "currently healthy"; a hotter host whose alarm was misconfigured may not have paged, so read the metric, not just the state.

3. **Reading the output.** For each instance the script prints, per statistic, min/max/mean, the count of buckets `>= threshold`, and each contiguous high-CPU block tagged `SUSTAINED` (>=5 buckets, i.e. it would clear the alarm's 5-of-6 rule) or `blip`. It reports both **Average** (what the alarm evaluates — the breach signal) and **Maximum** (per-minute peaks that can momentarily hit ~100% without lifting the Average — *not* a breach on their own; see the wiki page). The threshold defaults to the alarm's own value (75 for the Solr alarm) when `--alarm-name-prefix` is used. *(For a CPU series you have already saved to a JSON file, `scripts/analyze_cpu_metrics.py <file>` runs the same analysis half over the saved datapoints.)*

4. **Resolve the timezone before correlating.** CloudWatch is **UTC**, and `log.search_query_log.t_create` (and the other `log.*` tables) are also stored in **UTC** — same clock, no shift needed. See [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]] and pair this skill with `query-starrocks` + `plot-result-set` for the overlay.

## Alarm history — is this page chronic or rare?

Beyond the *current* breach, an oncall often needs to know whether the alarm pages constantly or almost never — a first trigger in months points at a discrete in-window event, while a frequent flapper points at a trend or a misconfigured threshold. Pull the alarm's **state-transition history** (the transitions *into* ALARM = the trigger events) and summarize the most recent trigger, the prior trigger, and the gap — one **bundled, unattended** call:
```bash
"$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_alarm_history.py" --alarm-name "<exact alarm name>" --region <region>
```
It reads `describe-alarm-history` (`StateUpdate` items), reports the trigger timestamps newest-first, and computes the gap since the prior trigger. No `$CODE_BASE` import — pure AWS read + transform. **Caveat:** CloudWatch retains alarm history for **~14 days**, so a prior trigger older than that will *not* appear — if only this incident's trigger shows, the alarm may still have a much older prior page; confirm the longer history via PagerDuty. Pass the **exact** alarm name (from the page, or from the `describe-alarms` output in Step 2).

## Queue-depth alarms (SQS "Queue backed up")

For a [[../../../wiki/oncall/queue-backed-up|Queue backed up]] page the alarm is **metric-math** — top-level `MetricName`/`Namespace` are null, the real metric (`AWS/SQS ApproximateNumberOfMessagesVisible`) lives in the `Metrics` array. Pull the alarm threshold + the queue-depth curve and flag breach buckets in one **bundled, unattended** call:
```bash
PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_queue_depth.py" --queue <queue> --region <region> --start <ISO8601Z> --end <ISO8601Z>
```
It reads the alarm threshold (`describe-alarms`), pulls `Maximum`+`Average` per 900s bucket (`get-metric-statistics`), tabulates the curve, marks buckets at/over threshold with `<<<`, and reports the peak as a % of threshold. CloudWatch is **UTC**. Then attribute the backlog to an op/tenant with the **`query-processor-event-log`** skill (`--queue <queue> --event-type message_dispatched --count-by operation0,group_id`).

## ELB host-health alarms ("Host Unhealthy")

For a [[../../../wiki/oncall/host-unhealthy|Host unhealthy]] page (an Elastic Beanstalk ALB health-check alarm) the alarm is **metric-math** with a **two-series breach signal**: `e1 = UnHealthyHostCount − HealthyHostCount ≥ 0` (both `AWS/ApplicationELB`, Average, dimensioned on the EB ALB's `TargetGroup` + `LoadBalancer`). The breach is a **difference**, not a single metric crossing a line — so this pulls **both** series, merges them per bucket, computes `e1`, and flags the `e1 ≥ 0` breach buckets in one **bundled, unattended** call:
```bash
"$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_elb_health.py" --alarm-name "<env> Unhealthy (<region>)" --region <region> --start <ISO8601Z> --end <ISO8601Z>
```
`--alarm-name` self-resolves the `TargetGroup` + `LoadBalancer` dimensions from the alarm's `Metrics` array (a metric-math alarm has **null top-level `MetricName`/`Namespace`**); alternatively pass `--target-group` + `--load-balancer` dimension values directly. Default `--period 60` shows the per-minute shape (the alarm itself evaluates Average/300s, `DatapointsToAlarm=3`). The merged-curve **shape is the fork** — transient deploy/instance-replacement churn (brief unhealthy window, clean recovery) vs. a sustained fault. To then resolve the host(s) behind the target group and pull the EB environment event stream (the root-cause source), **use the `inspect-eb-environment` skill**. CloudWatch is **UTC**.

## Notes

- **Reachability is only knowable by trying.** Env inspection (AWS CLI present, `AWS_PROFILE`/region set) cannot tell you whether the role holds `cloudwatch:DescribeAlarms`/`GetMetricData`. Make the read and report plainly if it is denied, rather than guessing.
- **One alarm = one EC2 instance.** The metric dimension is the `InstanceId`, not the hostname; the alarm definition is where you get it.
- **Region fallback for bundled scripts.** `pull_alarm_history.py` and `pull_queue_depth.py` resolve the AWS region in this order: `--region` arg → `AWS_DEFAULT_REGION` env var → `EF_DEFAULT_REGION` env var → `us-west-2`. Always pass `--region` explicitly for non-default regions. AWS-specific services (CloudWatch, EC2, SQS) are not available for Azure-format regions such as `westus2`.
