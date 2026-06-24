---
name: inspect-cloudwatch-cpu
description: Pull a CloudWatch alarm definition and the underlying EC2 CPUUtilization timeseries via read-only AWS CLI, then tabulate the series and flag breach buckets. Use whenever you need to confirm or characterize a host-CPU alarm — a "Solr CPU Util Too High" PagerDuty page, an EC2 CPU spike, a CloudWatch alarm you want to verify against the real metric curve — to establish the true spike window and shape (sustained breach vs. one-minute blip) before correlating it to anything else. Reach for this whenever a task hands you a CloudWatch alarm name, an EC2 instance/host, or a PagerDuty CPU incident and asks what actually happened. Also use as the second step when you have already resolved DNS hostnames to InstanceIds (e.g. via solr-shard-dns-lookup) and want to pull the CPU curve — skip describe-alarms and go straight to get-metric-statistics.
---

# Inspect CloudWatch CPU alarm + EC2 metric

Confirm a host-CPU alarm against the real metric. The access facts and the alarm config live in the wiki ([[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]]); the runtime judgment this skill carries is **which alarm, which instance, and which window** to pull, and **reading the curve** (sustained breach vs. blip). The two AWS calls are read-only telemetry; the deterministic tabulation is a **bundled script** — `scripts/analyze_cpu_metrics.py` — that runs unattended on the saved JSON.

## When to anchor on this first

A metric alarm tells a story; verify it before acting on it. Pull the alarm definition and the real CPU curve, pin the true spike window and shape, and *then* correlate a candidate cause (query load, a deploy) over that window — see [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]]. A non-correlation is a real finding.

## Entry points

There are two ways to arrive at this skill:

- **From a CloudWatch alarm name or PagerDuty page** (the common case): proceed to Step 1 — `describe-alarms` gives you the `InstanceId`.
- **From EC2 DNS hostnames** (e.g. after running **`solr-shard-dns-lookup`**): you already have InstanceIds; skip Step 2 entirely and go straight to Step 3.

## Steps

1. **Read the access pattern from the wiki** (via `wiki-reader`):
   - [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — environment/reachability, the alarm config (75% Average, 5-of-6 300s periods), the `InstanceId` dimension, and that CloudWatch times are **UTC**.
   - [[../../../wiki/solr/solr-collection-topology|Solr collection topology]] — if this is a Solr page: how a `<collection> shard N replica R` alarm maps to one EC2 host, and that a shard spans multiple replica hosts.

2. **Get the alarm definition** (read-only). The alarm carries the metric, namespace, threshold, evaluation rule, and — crucially — the **`InstanceId`** dimension you need for step 3. A single name-prefix can match **multiple** sibling alarms (one per replica):
   ```bash
   aws cloudwatch describe-alarms --region us-west-2 --alarm-name-prefix "<alarm name prefix>"
   ```
   Read `StateReasonData` for the recent 300s datapoints + the last transition time. Do **not** treat the alarm's last-transition time as "currently healthy" — a hotter host whose alarm was misconfigured may not have paged; read the metric, not just the state.

3. **Get the CPU timeseries** (read-only), one call per `InstanceId`, a few hours either side of the suspected spike. `--period 60` gives one-minute buckets; request both `Average` and `Maximum`; save each to its own JSON file:
   ```bash
   aws cloudwatch get-metric-statistics --region us-west-2 --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=<i-...> --start-time 2026-06-15T06:00:00Z --end-time 2026-06-15T12:00:00Z --period 60 --statistics Average Maximum
   ```
   Save each instance's output to its own JSON file (e.g. `... --output json > cpu_r0.json`). The redirect makes the command prompt — that is fine here, because the AWS call is approval-gated anyway (see the approval note below); the gate-clean unattended step is the analysis in step 4, not the AWS pull.

4. **Tabulate and flag breaches with the bundled script.** Pass the saved JSON file(s) **by path** (never inline) so the run stays gate-clean and auto-allowed:
   ```bash
   "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/analyze_cpu_metrics.py" --threshold 75 --stat Average /path/to/cpu_r0.json /path/to/cpu_r1.json
   ```
   It sorts the (unordered) AWS datapoints by timestamp, prints min/max/mean, counts buckets `>= --threshold`, and shows each contiguous high-CPU block tagged `SUSTAINED` (>=5 buckets, i.e. it would clear the alarm's 5-of-6 rule) or `blip`. `--threshold` defaults to 75 (the Solr alarm threshold); `--stat` defaults to `Average` (what the alarm evaluates). Tag each file with `--label` (repeatable, paired with the files in order). No `$CODE_BASE` import is involved — this is a pure transform over the JSON.

5. **Resolve the timezone before correlating.** CloudWatch is **UTC**. If you go on to correlate against [[../../../wiki/data-warehouse/search-query-log|log.search_query_log]], note its `t_create` is stored in **IST** — shift the CPU window **+5:30** (or the SQL window **−5:30**) so both are on one clock. See [[../../../wiki/process/incident-metric-correlation|incident metric-correlation discipline]] and pair this skill with `query-starrocks` + `plot-result-set` for the overlay.

## Notes

- **Reachability is only knowable by trying.** Env inspection (AWS CLI present, `AWS_PROFILE`/region set) cannot tell you whether the role holds `cloudwatch:DescribeAlarms`/`GetMetricData`. Make the read and report plainly if it is denied, rather than guessing.
- **One alarm = one EC2 instance.** The metric dimension is the `InstanceId`, not the hostname; the alarm definition is where you get it.
