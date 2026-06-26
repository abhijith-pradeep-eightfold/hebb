---
name: inspect-cloudwatch-metric
description: Pull a CloudWatch alarm definition and its backing metric timeseries via read-only AWS CLI, then tabulate the series and flag breach buckets — for EC2 host CPU (`CPUUtilization`) or SQS queue depth (`AWS/SQS ApproximateNumberOfMessagesVisible`, including metric-math alarms). Use whenever you need to confirm or characterize an alarm against the real metric curve — a "Solr CPU Util Too High" PagerDuty page, an EC2 CPU spike, a "Queue backed up" page, or any CloudWatch alarm you want to verify — to establish the true spike window and shape (sustained breach vs. one-minute blip) before correlating it to anything else. Reach for this whenever a task hands you a CloudWatch alarm name, an EC2 instance/host, an SQS queue, or a PagerDuty CPU/queue incident and asks what actually happened. Also use as the second step when you have already resolved DNS hostnames to InstanceIds (e.g. via solr-shard-dns-lookup) and want to pull the CPU curve — skip describe-alarms and go straight to get-metric-statistics.
knowledge_optional:
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
---

# Inspect CloudWatch alarm + metric (CPU or queue depth)

Confirm a host-CPU alarm against the real metric. The access facts and the alarm config live in the wiki ([[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]]); the runtime judgment this skill carries is **which alarm, which instance, and which window** to pull, and **reading the curve** (sustained breach vs. blip). The two AWS calls are read-only telemetry; the deterministic tabulation is a **bundled script** — `scripts/analyze_cpu_metrics.py` — that runs unattended on the saved JSON.

## When to anchor on this first

A metric alarm tells a story; verify it before acting on it. Pull the alarm definition and the real CPU curve, pin the true spike window and shape, and *then* correlate a candidate cause (query load, a deploy) over that window — see [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]]. A non-correlation is a real finding.

## Entry points

There are two ways to arrive at this skill:

- **From a CloudWatch alarm name or PagerDuty page** (the common case): proceed to Step 1 — `describe-alarms` gives you the `InstanceId`.
- **From EC2 DNS hostnames** (e.g. after running **`solr-shard-dns-lookup`**): you already have InstanceIds; skip Step 2 entirely and go straight to Step 3.

> If the task starts from a **collection + shard ID** and just wants that shard's CPU, the combined **`solr-shard-cpu`** skill runs the whole pipeline (host lookup → per-replica CPU) in one call — use it instead of running the two skills by hand. This skill stays the right choice when you start from an alarm name or a known InstanceId, or want to characterize a known spike.

## Steps

1. **Read the access pattern from the wiki** (via `wiki-reader`):
   - [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — environment/reachability, the alarm config (75% Average, 5-of-6 300s periods), the `InstanceId` dimension, and that CloudWatch times are **UTC**.
   - [[../../../wiki/solr/solr-collection-topology|Solr collection topology]] — if this is a Solr page: how a `<collection> shard N replica R` alarm maps to one EC2 host, and that a shard spans multiple replica hosts.

2. **Get the alarm definition** (read-only). The alarm carries the metric, namespace, threshold, evaluation rule, and — crucially — the **`InstanceId`** dimension you need for step 3. A single name-prefix can match **multiple** sibling alarms (one per replica):
   ```bash
   aws cloudwatch describe-alarms --region us-west-2 --alarm-name-prefix "<alarm name prefix>"
   ```
   Read `StateReasonData` for the recent 300s datapoints + the last transition time. Do **not** treat the alarm's last-transition time as "currently healthy" — a hotter host whose alarm was misconfigured may not have paged; read the metric, not just the state.

3. **Get the CPU timeseries and analyze it in one shot** (read-only). For each `InstanceId`, redirect the AWS output directly to a scratchpad path and immediately pass that path to the analysis script — do not pause between saving and analyzing:
   ```bash
   aws cloudwatch get-metric-statistics --region us-west-2 --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=<i-...> --start-time 2026-06-15T06:00:00Z --end-time 2026-06-15T12:00:00Z --period 60 --statistics Average Maximum --output json > /tmp/cpu_r0.json && "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/analyze_cpu_metrics.py" --threshold 75 --stat Average --label "replica-0" /tmp/cpu_r0.json
   ```
   The `--period 60` gives one-minute buckets. Repeat for each replica, choosing a distinct scratchpad path per instance. The redirect + `&&` chain counts as one logical operation — the AWS fetch is approval-gated regardless; the analysis runs immediately on success.

   **If you have multiple replicas**, fetch and analyze each in a single chained command as above rather than fetching all first and analyzing later. The JSON file is a short-lived intermediate; there is no reason to inspect or preserve it between the two commands.

4. **Reading the analysis output.** `analyze_cpu_metrics.py` sorts the (unordered) AWS datapoints by timestamp, prints min/max/mean, counts buckets `>= --threshold`, and shows each contiguous high-CPU block tagged `SUSTAINED` (>=5 buckets, i.e. it would clear the alarm's 5-of-6 rule) or `blip`. `--threshold` defaults to 75 (the Solr alarm threshold); `--stat` defaults to `Average` (what the alarm evaluates). Tag each file with `--label` (repeatable, paired with the files in order). No `$CODE_BASE` import is involved — this is a pure transform over the JSON.

5. **Resolve the timezone before correlating.** CloudWatch is **UTC**, and `log.search_query_log.t_create` (and the other `log.*` tables) are also stored in **UTC** — same clock, no shift needed. See [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]] and pair this skill with `query-starrocks` + `plot-result-set` for the overlay.

## Queue-depth alarms (SQS "Queue backed up")

For a [[../../../wiki/oncall/queue-backed-up|Queue backed up]] page the alarm is **metric-math** — top-level `MetricName`/`Namespace` are null, the real metric (`AWS/SQS ApproximateNumberOfMessagesVisible`) lives in the `Metrics` array. Pull the alarm threshold + the queue-depth curve and flag breach buckets in one **bundled, unattended** call:
```bash
PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pull_queue_depth.py" --queue <queue> --region <region> --start <ISO8601Z> --end <ISO8601Z>
```
It reads the alarm threshold (`describe-alarms`), pulls `Maximum`+`Average` per 900s bucket (`get-metric-statistics`), tabulates the curve, marks buckets at/over threshold with `<<<`, and reports the peak as a % of threshold. CloudWatch is **UTC**. Then attribute the backlog to an op/tenant with the **`query-processor-event-log`** skill (`--queue <queue> --event-type message_dispatched --count-by operation0,group_id`).

## Notes

- **Reachability is only knowable by trying.** Env inspection (AWS CLI present, `AWS_PROFILE`/region set) cannot tell you whether the role holds `cloudwatch:DescribeAlarms`/`GetMetricData`. Make the read and report plainly if it is denied, rather than guessing.
- **One alarm = one EC2 instance.** The metric dimension is the `InstanceId`, not the hostname; the alarm definition is where you get it.
