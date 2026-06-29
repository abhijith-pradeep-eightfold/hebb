# Host unhealthy (oncall ticket type)

**Summary:** A PagerDuty **"`<env>` Unhealthy (`<region>`)"** page (Base Incident, service **Core Infra**, High urgency) fires when an **Elastic Beanstalk** environment's load-balancer target group reports as many unhealthy hosts as healthy ones. It is backed by a CloudWatch **metric-math** alarm whose breach signal is a **derived difference** — `UnHealthyHostCount − HealthyHostCount ≥ 0` — not a raw metric crossing a fixed line. So this is a third alarm *shape*, distinct from the [[solr-cpu-high|CPU]] (single-metric rate threshold) and [[queue-backed-up|queue-depth]] (single-metric stock SUM) types: characterizing it means **merging two host-count series and computing the difference per bucket**. The driver of an unhealthy-host window is *what made the hosts unhealthy*, and the **metric shape is the fork** — transient deploy/instance-replacement churn (which self-resolves) versus a sustained fault (resource exhaustion, a crashed service). This page covers the alarm, characterizing the spike, the **churn-vs-fault fork**, the two EB evidence sources (the hosts behind the target group and the EB environment event stream), and routing. It is a concrete instance of the [[oncall-investigation|oncall investigation discipline]].

## The alarm

The page is backed by a CloudWatch **metric-math** alarm named for the environment, e.g. `<env> Unhealthy (<region>)`:

- Expression **`e1 = m1 − m2`** (ReturnData), where `m1 = AWS/ApplicationELB · UnHealthyHostCount` and `m2 = AWS/ApplicationELB · HealthyHostCount`, both **Average**, **300s**, dimensioned on the **`TargetGroup`** and **`LoadBalancer`** of the Elastic Beanstalk ALB.
- Trips at **`e1 ≥ 0`** (`GreaterThanOrEqualToThreshold`), **`EvaluationPeriods=3`, `DatapointsToAlarm=3`** → ~15 min of sustained breach. `TreatMissingData=missing`.
- **Interpretation:** fires when the target group's **unhealthy hosts reach or exceed its healthy hosts** for 3 consecutive 5-min periods.

As with the [[queue-backed-up|queue-depth]] alarm, a metric-math alarm has **null top-level `MetricName`/`Namespace`** — the real metrics live in the alarm's `Metrics` array. Read both metrics' dimensions (the `TargetGroup` + `LoadBalancer`) there; those dimensions are what you pull the curve against.

**This alarm class commonly self-resolves.** A brief deploy or instance-replacement window that breaches `e1 ≥ 0` for ~15 min and then recovers is the dominant benign cause. The same alarm class fires independently per environment and per region. Check the **current state** (`StateValue`/`StateUpdatedTimestamp`) first — the incident may already be OK — and check **how chronic** it is with the alarm's state-transition history. **Use the `inspect-cloudwatch-metric` skill** (alarm-history mode); note CloudWatch retains alarm history for **~14 days**, so older firings need PagerDuty for the long view.

## Characterize the spike — merge the two host-count series

The breach signal is the **difference** of two series, so pull **both** `HealthyHostCount` and `UnHealthyHostCount` (`AWS/ApplicationELB`, Average, against the alarm's `TargetGroup` + `LoadBalancer` dimensions) over the incident window plus a baseline, **merge them by timestamp**, compute `e1 = UnHealthy − Healthy` per bucket, and flag the breach buckets where `e1 ≥ 0`. CloudWatch is **UTC** — establish the true breach window before correlating anything (see [[../process/incident-metric-correlation|metric-correlation discipline]]). **To pull and tabulate this, use the `inspect-cloudwatch-metric` skill** (ELB host-health mode), which handles the two-series merge and the `e1 ≥ 0` flag.

The **shape** of the merged curve *is* the fork (next section). A short window of `Healthy=2` (a second host registering) followed by a brief `Healthy=0, UnHealthy=1` and then a clean return to `Healthy=1, UnHealthy=0` is **rolling-deploy churn**; a host that goes unhealthy and **stays** unhealthy is a **fault**.

## The fork — transient churn vs. sustained fault

The metric does not tell you the cause by its breach alone; the **shape and the EB event stream** do. Decide which of two regimes you are in:

- **Transient churn** (the common, benign case) — a config update or deploy triggers an **ASG rolling instance replacement**: capacity temporarily rises (a new host registers and is briefly unhealthy while it warms up), the old host is removed, and there is a short single-host or zero-healthy window before the replacement passes its health checks. The metric recovers on its own and the alarm clears with a metric-eval lag. A **new instance whose `LaunchTime` falls inside the incident window** is the signature of a replacement.
- **Sustained fault** — a host goes unhealthy and stays down: resource exhaustion (an undersized instance type running out of memory/CPU during a deploy), a service that failed to start (`/healthz` returning errors, "following services are not running …"), or nginx refusing connections on the health-check path. The metric does **not** self-recover; this is the case that needs escalation.

The two EB evidence sources below settle which regime applies.

## Evidence source 1 — the hosts behind the target group

Resolve the live host(s) behind the alarm's target group via a deterministic chain — the target group's ARN, then its registered targets (InstanceIds + health), then each instance's details — where each step's input is the prior step's output. The target-group and load-balancer **names** come from the alarm dimensions; **never hardcode the host or instance IDs** — they change on every replacement, so the lookup is always done live. The `inspect-eb-environment` skill runs this whole chain (and evidence source 2) in one bundled, unattended call — **use that skill** rather than issuing the reads by hand.

From the resolved instance details read:
- **`InstanceType`** — verify whether a prior instance-type remediation is still in place (e.g. a past fix that bumped the type up to avoid resource exhaustion). A current type matching the fix **confirms** the remediation holds — do not assume it was reverted.
- **`LaunchTime`** — inside the incident window ⇒ this host is the **replacement** (churn signature).
- **Tags** — the Elastic Beanstalk environment name and **environment-id**, and the AutoScalingGroup. The environment-id feeds evidence source 2.

## Evidence source 2 — the EB environment event stream (the root-cause source)

The EB environment's own event log (read live for the incident window, keyed by the environment-id from the instance tags above) states plainly whether the unhealthy window was a config-update→rolling-replacement (benign), a deploy, or a fault. A churn timeline reads as: *"Updating environment … configuration settings"* → rolling update begins (temporarily raises capacity to keep ≥1 host in service) → a new instance is added and is `Degraded` while warming → *"New application version was deployed"* / *"Environment update completed successfully"* → the original instance is removed → a brief **0-healthy window** (the metric breach) → the replacement passes health checks → environment health returns to **Ok**. A single-host environment (`DesiredCapacity=1`) in a single AZ shows this most starkly because there is no spare capacity to mask the gap. A fault timeline instead shows instances removed *"due to a ELB health check failure"* that do **not** recover, or services failing to start.

**To run both evidence pulls in one step, use the `inspect-eb-environment` skill** — given the alarm name (or the target-group name) and a window, it resolves the hosts (instance type, launch time, EB environment, ASG) and pulls the EB environment event timeline. The read-only AWS calls all live inside the skill's bundled script, so they run unattended.

## Routing — no processor lineage

Unlike [[queue-backed-up|queue-backed-up]] and [[solr-cpu-high|Solr CPU too high]], a host-unhealthy alarm has **no op → file → owner trace** — the cause is infrastructure, not a processor operation. Ownership is the **Elastic Beanstalk environment owner / Core Infra**. For a self-resolved deploy-churn window on a staging environment the routing is **"benign, self-resolved; confirm the config update / deploy was intended"** — no code owner to page. A sustained fault routes to whoever owns the environment and the change that triggered it (the config update, the application version, or the instance-type sizing).

**Treat an auto-triage bot's hypothesis as a lead, not a conclusion.** The on-call auto-triage bot posts a plausible cause and a historical base rate, but it **may not be able to read CloudWatch** for the alarm — in which case its claims about current/resolved state and root cause are unverified. Anchor on the direct telemetry (the merged metric curve + the EB event stream), exactly the [[../process/incident-metric-correlation|metric-correlation discipline]]: in the witnessed incident the bot guessed a prior instance-type fix "may not be in place," and the direct `describe-instances` read disproved it.

## Witnessed incidents

| | `stage0-api5` Unhealthy, eu-central-1 (2026-06-29) |
|---|---|
| **Alarm** | metric-math `e1 = UnHealthyHostCount − HealthyHostCount ≥ 0`, Average/300s, `EvaluationPeriods=3`/`DatapointsToAlarm=3` (~15 min) |
| **State** | self-resolved — ALARM 14:46:48Z → OK 14:53:48Z (**~7 min**); prior trigger ~4d18h earlier; chronic ~1/month, historically self-resolves |
| **Spike shape** | rolling-deploy churn: brief 2-host window (replacement registering) → ~16-min single-host `Healthy=0, UnHealthy=1` (the breach) → clean recovery to `Healthy=1, UnHealthy=0` |
| **Hosts** | single-host staging env (`DesiredCapacity=1`, one AZ); the live host's `LaunchTime` fell inside the incident window ⇒ instance replacement; instance type matched the prior remediation (the past type-bump fix **was** still in place) |
| **EB events** | confirmed: a configuration update triggered an ASG rolling instance replacement → degraded while warming → 0-healthy breach window → replacement healthy → environment Ok; alarm cleared with a ~5-min metric-eval lag |
| **Verdict / routing** | benign deploy churn, self-resolved, staging-only; no code owner to page; disproved the auto-triage bot's guess that the instance-type fix was not in place |

The lesson: the breach is a *symptom of host churn*, and for this alarm class the **shape + the EB event stream** decide churn-vs-fault. A self-resolved single-host staging window driven by a config-update rolling replacement is benign; verify the instance type against any prior remediation rather than trusting a bot's guess.

## Reporting the result

Report a host-unhealthy ticket as a **detailed, table-structured report**, not prose — the shared format is on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For *Host unhealthy* the tables are: **alarm** config (the metric-math `e1` expression, threshold/evaluation) + **state & chronicity** (current state, first/prior trigger); **spike characterization** (the merged Healthy/UnHealthy curve with `e1` per bucket, baseline → onset → breach window → recovery); **hosts behind the target group** (instance type vs. any prior remediation, launch time vs. the window, EB environment + ASG); **EB event timeline** (the config-update → rolling-replacement → recovery sequence, or the fault events); and **verdict / routing** (churn-vs-fault, owner or "benign, self-resolved"). To post it back to the PagerDuty thread, **use the `oncall-post-report` skill** (Canvas + concise threaded reply; it confirms the destination first and renders owner/customer names as plain text so the post pages no one).

## Related skills

- `oncall-host-unhealthy` — the high-level runbook for this ticket type; start here to run the whole investigation (read the page → characterize the merged metric → resolve hosts + EB events → judge churn-vs-fault → route → report).
- `inspect-cloudwatch-metric` — use it to read the metric-math alarm definition + state-transition history (chronicity), and to pull/merge the two `AWS/ApplicationELB` host-count series and flag the `e1 ≥ 0` breach buckets (ELB host-health mode).
- `inspect-eb-environment` — use it to resolve the host(s) behind the alarm's target group (instance type, launch time, EB environment, ASG) and pull the EB environment event timeline, in one call.
- `oncall-post-report` — use it to post the finished table-structured report back to the PagerDuty Slack thread (Canvas + concise threaded reply), with a confirm-before-post gate and plain-text (non-paging) references.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline.
- [[queue-backed-up|Queue backed up]] — a sibling metric-math alarm; its note on reading metric-math alarms (null top-level metric, real metrics in the `Metrics` array) applies here too.
- [[solr-cpu-high|Solr CPU too high]] — the other host-resource ticket type; both anchor on the real metric curve before attributing a cause.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the read-only AWS CLI access pattern (environment/reachability, CloudWatch is UTC) shared by every CloudWatch-backed oncall.
- [[../process/incident-metric-correlation|Incident metric-correlation discipline]] — anchor on direct telemetry; treat a triage bot's hypothesis as a lead, not a conclusion.
