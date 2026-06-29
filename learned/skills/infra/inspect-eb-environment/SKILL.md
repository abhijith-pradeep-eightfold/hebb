---
name: inspect-eb-environment
model: sonnet
description: Resolve the EC2 host(s) behind an Elastic Beanstalk ALB target group and pull the EB environment event stream — the two evidence sources behind a "Host Unhealthy" / EB health-check PagerDuty page. Given the alarm name (or a target-group name) and a UTC window, it reports each host's instance type, launch time (a launch inside the incident window = an instance replacement), target health, EB environment name/id, and ASG, then the EB environment event timeline (a config-update → ASG rolling instance replacement → recovery sequence, or the fault events). Use when a host-unhealthy / EB-unhealthy alarm pages and you need to know which hosts are behind it, whether the current instance type matches a prior remediation, and what the EB environment actually did — the root-cause source for this ticket type (there is no processor lineage). Pairs with inspect-cloudwatch-metric (which characterizes the UnHealthy − Healthy metric curve first).
knowledge_required:
  - "[[../../../wiki/oncall/host-unhealthy|Host unhealthy (oncall)]]"
---

# Inspect an Elastic Beanstalk environment behind a host-unhealthy alarm

For a [[../../../wiki/oncall/host-unhealthy|Host unhealthy]] page, after you have characterized the `UnHealthy − Healthy` metric curve (with **`inspect-cloudwatch-metric`**, ELB host-health mode), this skill pulls the **two EB evidence sources** that settle the **churn-vs-fault fork** — in one unattended call. The domain facts (the alarm, the fork, EB topology, routing) live on the wiki page; this skill runs the deterministic lookup chain and prints the evidence for you to judge.

## What it does

A single bundled script runs the no-judgment chain (each call's input is the prior call's output — Rule A2):

```
alarm / target-group  ->  TargetGroupArn  ->  InstanceId(s) + health
                      ->  instance type / launch time / EB env tags  ->  EB environment events
```

and prints:
1. **Host table** — per host behind the target group: `InstanceType` (compare against any prior instance-type remediation — a match confirms the fix still holds), `LaunchTime` (flagged when it falls **inside** the incident window ⇒ this host is a **replacement**, the churn signature), EC2 + target-health state, the Elastic Beanstalk environment name/id, and the ASG.
2. **EB environment event timeline** — the environment's own events over the window. A **churn** timeline reads as *"Updating environment … configuration settings"* → rolling update → a new instance added and `Degraded` while warming → version deployed → original removed → brief 0-healthy window (the breach) → healthy → `Ok`. A **fault** timeline shows instances removed *"due to a ELB health check failure"* that do not recover, or services failing to start.

The script makes **no judgment** — you read the host table + timeline and decide churn (benign, self-resolves) vs. fault (escalate). Host/instance IDs are resolved **live**; nothing is hardcoded.

## Run it

Pass the exact alarm name (it self-resolves the target group from the alarm's `Metrics` array), or a target-group name directly, plus the UTC incident window:

```bash
"$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/eb_environment.py" --alarm-name "<env> Unhealthy (<region>)" --region <region> --start <ISO8601Z> --end <ISO8601Z>
```

- `--alarm-name "<env> Unhealthy (<region>)"` — preferred; resolves the `TargetGroup` from the metric-math alarm. Or `--target-group-name awseb-AWSEB-…` to skip the alarm read.
- `--region` — pass explicitly for non-default regions (default resolves `AWS_DEFAULT_REGION` → `EF_DEFAULT_REGION` → `us-west-2`).
- `--start` / `--end` — the EB-events window (UTC; default last 3h). Use the breach window from the metric step, widened slightly, so the config-update/rolling-replacement events fall inside it.

All calls are **read-only** AWS telemetry (`elbv2 describe-target-groups` / `describe-target-health`, `ec2 describe-instances`, `elasticbeanstalk describe-events`) and run unattended. The reusable AWS helpers live in `learned/hebb_utils/aws/elastic_beanstalk.py` (shared, importable by other skills); reachability is only knowable by trying — the script reports plainly if a call is denied.

## Notes

- **No processor lineage.** Unlike a queue/Solr incident there is no op→file→owner trace; ownership is the **EB environment owner / Core Infra**. Route a self-resolved deploy-churn window as "benign; confirm the config update was intended."
- **Treat an auto-triage bot's hypothesis as a lead, not a conclusion.** The bot may not be able to read CloudWatch/EC2; the host table's live `InstanceType` is what confirms or disproves a "the fix may not be in place" guess.
- CloudWatch and the EB event stream are both **UTC** — same clock, no shift.
