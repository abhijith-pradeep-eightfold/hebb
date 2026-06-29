# CloudWatch CPU alarm + EC2 metric access

**Summary:** How to pull a CloudWatch **alarm definition** and the underlying **EC2 `CPUUtilization`** timeseries from the agent environment, using read-only AWS CLI calls. This is the access pattern for confirming a "Solr CPU Util Too High" PagerDuty alarm (see [[../solr/solr-collection-topology|Solr collection topology]]) against the real metric curve.

## Environment / reachability

The agent box has the AWS CLI and a usable us-west-2 profile:

- AWS CLI present (`aws-cli/1.40.37`, `botocore/1.38.38`).
- `AWS_PROFILE=bedrock-role`, `AWS_DEFAULT_REGION=us-west-2`, `AWS_ACCOUNT_ID=948299231917`; `~/.aws/config` and `~/.aws/credentials` both present.
- The `bedrock-role` profile **could** read CloudWatch in us-west-2 (no `AccessDenied` on `describe-alarms` / `get-metric-statistics`). Whether a role holds `cloudwatch:DescribeAlarms` / `cloudwatch:GetMetricData` is **not** knowable from env inspection alone — it can only be confirmed by an actual call. Check reachability by making the read and reporting plainly if it is denied.

These are **telemetry reads**, not writes — run them unattended. Reachability is only knowable by trying: make the read and report plainly if it is denied.

## Step 0 — if you start from DNS (no alarm in hand)

If you reached this point via a `search_config` DNS lookup (see [[../solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]]) rather than a PagerDuty alarm, the DNS hostname must be resolved to an EC2 InstanceId (a `describe-instances` filter on `dns-name`) before pulling CloudWatch metrics. That resolution is bundled inside the **`solr-shard-dns-lookup` skill** (and the combined **`solr-shard-cpu` skill**) — you don't issue the lookup by hand.

- One InstanceId is resolved per replica DNS hostname; the result (`i-...`) is what the Step 2 timeseries pull keys on.
- If you *do* have an alarm name, you don't need this — the alarm definition in Step 1 already carries the `InstanceId` dimension.
- This is also the path for a **plain current-state question** ("what is the CPU of `<collection>` shard `<N>` right now?") with no incident or alarm behind it: resolve the InstanceIds, **skip the alarm read entirely**, and go straight to the Step 2 timeseries. Report each replica per [[../solr/solr-collection-topology|topology]] — a shard's CPU is per-replica. (Confirmed on positions shard 2: both replicas ~5% Average, no alarm firing.) For this collection+shard case the **`solr-shard-cpu` skill** runs the whole pipeline in one call — see the [[skills/index|Skills catalog]].

## Step 1 — the alarm definition

The alarm definition (a `describe-alarms` read on the alarm-name prefix) is pulled by the **`inspect-cloudwatch-metric` skill** — its `pull_cpu.py --alarm-name-prefix` resolves it and feeds the result straight into the metric pull. A single Solr alarm-name prefix can match **multiple** sibling alarms (one per replica): the observed "profiles shard 21" prefix returned two — replica 0 and replica 1 — each carrying its own `InstanceId` dimension (see [[../solr/solr-collection-topology|topology]] for the host↔InstanceId table). The alarm config (both replicas identical):

| Field | Value |
|---|---|
| `MetricName` | `CPUUtilization` |
| `Namespace` | `AWS/EC2` |
| `Statistic` | `Average` |
| `Threshold` | `75.0` |
| `ComparisonOperator` | `GreaterThanOrEqualToThreshold` |
| `Period` | `300` (s) |
| `EvaluationPeriods` | `6` |
| `DatapointsToAlarm` | `5` |
| `TreatMissingData` | `breaching` |
| `AlarmActions` | SNS `errors_volkscience_com` + PagerDuty |

So the alarm fires when CPU is **≥ 75% Average for 5 of any 6 consecutive 300s periods** — it tolerates one low datapoint, and missing data counts as breaching. This is why the page fired ~30 min into the spike, not at its onset (5×300s = 25 min of breach must accrue).

The alarm's **`StateReasonData`** records the most recent transition: the queried transition time (e.g. ALARM→OK at `2026-06-15T08:51:59Z`) and the `recentDatapoints` array (300s, Average) that drove it — useful to confirm the breach window without a separate metric pull.

> The alarm's last-transition time is **not** a proxy for "currently healthy/quiet": replica 1 ran hotter than replica 0 yet its alarm last transitioned 2025-09-15 (config only updated 2026-06-23). Read the metric, not just the alarm state.

## Step 2 — the CPU timeseries

The CPU timeseries (a `get-metric-statistics` pull on `AWS/EC2 CPUUtilization`) and the breach analysis are run by the same **`inspect-cloudwatch-metric` skill** (`pull_cpu.py`, from the resolved alarm or an explicit `--instance-id`); for the collection+shard entry the **`solr-shard-cpu` skill** runs host-lookup → CPU in one call. The facts those bundled scripts encode:

- The metric dimension is the **`InstanceId`** (from the alarm definition), not the hostname.
- Pull a few hours either side of the suspected spike; one-minute buckets (`--period 60`) and both `Average` and `Maximum` are requested.
- AWS returns datapoints unordered; the script sorts by `Timestamp` and flags `Average ≥ threshold` buckets (no `$CODE_BASE` import is involved — a pure transform over the JSON).

Observed for the alarming host (replica 0) over the 6h band: 72 one-minute Average buckets, mean ~31%, with a contiguous **08:20–08:35 UTC** block at ~98–99% Average (max ~99.7) — a genuine sustained breach, not a one-minute blip.

**Average vs. Maximum — read the right statistic.** The alarm evaluates **Average**, so that is the breach signal; **Maximum** is a per-bucket peak that can momentarily hit ~100% without lifting the Average, and on its own is **not** a breach. Witnessed on `profiles` shard 21 (2026-06-29, current-state pull): hourly Average stayed ~5% (mean 5.15%, max 17%) across 24h with **0** buckets ≥ 75%, even though per-minute Maximum within an hour spiked to 99.77% (then 68%, 60%, 53%) — short peaks that left the Average low. Report both, but judge breach on the Average; a lone high Maximum is normal.

## Timezone — CloudWatch is UTC

CloudWatch metric and alarm timestamps are **UTC**. (The PagerDuty console may *display* the page time in IST — e.g. 14:22 IST = 08:52 UTC, IST = UTC + 5:30 — but that is a console-rendering detail; the underlying metric is UTC.)

When correlating against [[../data-warehouse/search-query-log|log.search_query_log]], its `t_create` is also stored in **UTC** (so is `processor_event_log.t_create`) — the two are on the **same clock; no shift is needed**.

## Related

- [[../solr/solr-collection-topology|Solr collection topology]] — what the alarm coordinate (collection/shard/replica/host) means and the host↔InstanceId mapping.
- [[../solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]] — how to get DNS hostnames (and then InstanceIds) when you start from a collection name + shard ID rather than an alarm.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — using this CPU curve as the anchor before correlating to query load.
- [[../data-warehouse/search-query-log|log.search_query_log table]] — the secondary source you correlate against (its `t_create` is UTC, same clock as CloudWatch — no shift needed).


---
*Sources:* witness `inputs/2026-06-24-solr-cpu-spike-debug.md` (`[17:06]` env/reachability, `[17:09]` prepared commands, `[17:14]` both `describe-alarms` + `get-metric-statistics` results and the UTC/IST resolution); witness `inputs/2026-06-26-positions-shard2-cpu.md` (`[12:50]` current-state, no-alarm `get-metric-statistics` on both `positions` shard 2 replica InstanceIds, Average + Maximum, threshold 75 — Step 0 → Step 2 with Step 1 skipped); witness `inputs/2026-06-29-profiles-shard21-cpu.md` (`[09:26]` per-hour buckets on `profiles` shard 21 replica 0 — Average ~5% all 24h while per-minute Maximum spiked to ~100%; the Average-vs-Maximum reading rule).
