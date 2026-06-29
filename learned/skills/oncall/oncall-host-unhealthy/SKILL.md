---
name: oncall-host-unhealthy
model: sonnet
description: High-level oncall runbook for a "Host Unhealthy" PagerDuty page — an Elastic Beanstalk ELB health-check alarm (a CloudWatch metric-math alarm whose breach signal is the difference `UnHealthyHostCount − HealthyHostCount ≥ 0`). Use when you pick up a "<env> Unhealthy (<region>)" alarm (service Core Infra) and want the end-to-end investigation, not just one step — read the alert thread, characterize the merged Healthy/UnHealthy metric curve, resolve the hosts behind the target group + pull the EB environment event stream, judge the churn-vs-fault fork (transient deploy/instance-replacement churn that self-resolves vs. a sustained fault), and route to the EB environment owner (there is no processor lineage). Sequences external-context-puller → inspect-cloudwatch-metric → inspect-eb-environment → oncall-post-report. Reach for this whenever an EB host-unhealthy / ELB health-check alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/host-unhealthy|Host unhealthy (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
---

# Oncall runbook — Host unhealthy (Elastic Beanstalk)

The high-level flow for a `<env> Unhealthy (<region>)` PagerDuty page (service Core Infra). The domain facts — the metric-math alarm whose breach signal is the **difference** `UnHealthyHostCount − HealthyHostCount ≥ 0` (a third alarm *shape*, distinct from [[../../../wiki/oncall/solr-cpu-high|CPU]]'s rate threshold and [[../../../wiki/oncall/queue-backed-up|queue-depth]]'s stock SUM), the **churn-vs-fault fork**, the two EB evidence sources, and the table shapes to report — live in [[../../../wiki/oncall/host-unhealthy|Host unhealthy]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which window to pull, churn vs. fault, whether the instance type matches a prior fix, how to route), so read each step's output before the next. Critically, **do not conclude from the breach alone** — a `UnHealthy ≥ Healthy` window is most often benign deploy/instance-replacement churn that self-resolves; the metric *shape* + the EB event stream decide.

## Execution flow

1. **Read the alert thread for context.** Pull the PagerDuty Slack thread (and any linked incident) — **use the `external-context-puller` skill** — for the alarm name, region, environment, and any auto-triage bot hypothesis. **Treat the bot's claim as a lead, not a conclusion:** it often cannot read CloudWatch, so its current-state and root-cause claims are unverified — confirm everything against direct telemetry below.
2. **Confirm & characterize the spike (merge the two series).** The breach is a *difference*, so pull **both** `HealthyHostCount` and `UnHealthyHostCount` (`AWS/ApplicationELB`, against the alarm's `TargetGroup` + `LoadBalancer` dimensions), merge per bucket, compute `e1 = UnHealthy − Healthy`, and flag the `e1 ≥ 0` breach buckets — **use the `inspect-cloudwatch-metric` skill** (ELB host-health mode: `pull_elb_health.py --alarm-name "<env> Unhealthy (<region>)" --region <region> --start <Z> --end <Z>`). Also check **current state** (it may already be OK — this class commonly self-resolves) and **how chronic** the alarm is (same skill, alarm-history mode). CloudWatch is UTC; establish the true breach window first.
3. **Judge the fork from the shape.** A brief 2-host window (a replacement registering) → a short single-host or zero-healthy window → clean recovery is **transient churn** (benign). A host that goes unhealthy and **stays** down is a **sustained fault**. The shape narrows it; the EB events (next) confirm it.
4. **Pull the two EB evidence sources.** Resolve the host(s) behind the target group (instance type, launch time, EB environment, ASG) **and** the EB environment event timeline — **use the `inspect-eb-environment` skill** (`--alarm-name "<env> Unhealthy (<region>)" --region <region> --start <Z> --end <Z>`). A **`LaunchTime` inside the incident window** confirms an instance replacement; the **`InstanceType`** confirms whether a prior remediation (e.g. an earlier instance-type bump) is still in place — do not trust a bot's guess that it was reverted. The EB events state plainly whether it was a config-update → ASG rolling replacement (churn) or a fault (instances removed due to health-check failures that don't recover, services not starting).
5. **Route.** There is **no op→file→owner trace** — ownership is the **Elastic Beanstalk environment owner / Core Infra**. A self-resolved deploy-churn window (especially a single-host staging env) routes as **"benign, self-resolved; confirm the config update / deploy was intended"** — no code owner to page. A sustained fault routes to whoever owns the environment and the change that triggered it (the config update, the app version, or the instance-type sizing).
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it confirms the destination first and renders owner/customer names as plain text so the post pages no one. (Posting is outward-facing, so it is correctly gated on being asked — stop at the in-chat summary until the user directs the post.)

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, the metric-math `e1 = UnHealthy − Healthy` expression, threshold / evaluation (`e1 ≥ 0`, Average/300s, `DatapointsToAlarm=3`), and **state & chronicity** (current state, first / prior trigger).
2. **Spike characterization** — the merged Healthy/UnHealthy curve with `e1` per bucket: baseline → onset → breach window → recovery (and self-resolution time).
3. **Hosts behind the target group** — instance type (vs. any prior remediation), launch time (vs. the incident window), target health, EB environment + ASG.
4. **EB event timeline** — the config-update → rolling-replacement → recovery sequence, or the fault events.
5. **Verdict / routing** — churn-vs-fault, and the owner or "benign, self-resolved."
6. **Timeline** — the key timestamps in one place, all on one clock (UTC for CloudWatch + the EB event stream).

## Constituent skills (each independently usable)

- `external-context-puller` — step 1, read the PagerDuty Slack thread / linked incident for the alarm name, region, environment, and any bot hypothesis.
- `inspect-cloudwatch-metric` — step 2, the ELB host-health metric pull (merge the two series, flag `e1 ≥ 0`) and the alarm state/history (current state + how chronic).
- `inspect-eb-environment` — step 4, the two EB evidence sources (hosts behind the target group + the EB environment event timeline) in one call.
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
