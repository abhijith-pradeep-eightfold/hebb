---
task: Debug PagerDuty incident "stage0-api5 Unhealthy (eu-central-1)" from a Slack thread
date: 2026-06-29
skills_used:
  - {name: external-context-puller, note: read the PD Slack thread for the stage0-api5 Unhealthy incident}
interventions: 0
---

# Debug "stage0-api5 Unhealthy (eu-central-1)"

**Task:** User pasted a PagerDuty Slack thread link and said "Lets debug this." The incident is the CloudWatch alarm "stage0-api5 Unhealthy (eu-central-1)".

## Log

### [17:01] external-context-puller (Slack MCP read)
- **observed:** Read Slack thread C07NZL0PL9K ts 1782744411.019549.
  - Parent from PagerDuty bot: incident "stage0-api5 Unhealthy (eu-central-1)", type Base Incident, Service "Core Infra", Urgency High, assigned to Divyendra Mikkileni. PagerDuty incident `Q0S8U8DBKHW3GG`. Triggered via CloudWatch alarm `stage0-api5 Unhealthy (eu-central-1)` in eu-central-1.
  - One reply from "DP On-Call Auto-Triage (v2)" bot. Its claims (NOT independently verified by me): staging-only EB API host "slot 5", `/healthz` failed; staging only, no prod impact; triggered 2026-06-29 14:46:51 UTC; alarm recurs ~1/month and historically self-resolves; base rate from 10 prior eu-central-1 firings over 13 months; two prior eu-central-1 firings 2026-05-13 and 2026-05-06 self-resolved (2026-05-06 top human reply was "Deployment"); worst prior cause 2025-10-25 (incident Q2WN1N1BP8G5A5) = EB box on t3.medium ran out of resources during deploy → "Following services are not running: eb-docker-log" → nginx `connect() failed (111: Connection refused)` on `/healthz`, fixed by bumping instance type to t3.large in `app_environment_version_config`; same alarm class also fires independently in ca-central-1 (x3) and us-gov-west-1 (x5).
  - The auto-triage bot explicitly stated it could NOT read CloudWatch for this alarm, so current/resolved state is unknown from its seat.
- **effort:** one Slack MCP read. No wiki page exists for "host unhealthy" / EB health-check alarms (wiki oncall section covers only queue-backed-up and solr-cpu-high); this alarm type is not a compiled oncall ticket-type.

### [17:05] inspect-cloudwatch-metric (alarm def + state history + metric curve)
- **observed:** Read CloudWatch in eu-central-1 for alarm `stage0-api5 Unhealthy (eu-central-1)`. (Note: the auto-triage bot said it could not read CloudWatch; these are the direct reads.)
  - **Alarm definition** (`describe-alarms`): metric-math alarm. `e1 = m1 - m2` (ReturnData), where `m1 = AWS/ApplicationELB/UnHealthyHostCount` (Average, 300s) and `m2 = AWS/ApplicationELB/HealthyHostCount` (Average, 300s). Dimensions on both: `TargetGroup=targetgroup/awseb-AWSEB-FMEJK7SXHDXP/b486f8d29c5e7ee4`, `LoadBalancer=app/awseb--AWSEB-gn3gcMFbRam1/8071f7dc0f04652e` (an Elastic Beanstalk ALB). Threshold `e1 >= 0` (GreaterThanOrEqualToThreshold), EvaluationPeriods=3, DatapointsToAlarm=3, TreatMissingData=null. Interpretation: fires when UnHealthyHostCount ≥ HealthyHostCount sustained over 3×5min — i.e. the EB target group's unhealthy hosts reach/exceed the healthy ones.
  - **Current state:** `StateValue=OK`, `StateUpdatedTimestamp=2026-06-29T14:53:48.454Z`. StateReason for the OK flip: "1 out of the last 3 datapoints [-0.2 (29/06/26 14:46:00)] was not greater than or equal to the threshold (0.0)". => The incident **self-resolved ~7 min after triggering** (ALARM 14:46:48Z → OK 14:53:48Z).
  - **State-transition history** (`pull_alarm_history.py`, 14d lookback): 3 transitions INTO ALARM, newest-first: 2026-06-29T14:46:48Z (this incident), 2026-06-24T19:47:48Z, 2026-06-24T19:43:48Z. Gap since prior trigger 4d 18h. Not a constant flapper within the 14d window (CloudWatch retains only ~14d, so older firings the bot cited — May, Apr, 2025-10 — are beyond retention here).
  - **Backing metric curve** (`get-metric-statistics`, HealthyHostCount + UnHealthyHostCount, 60s, 13:30–15:30Z, merged by scratch script): steady `Healthy=1/UnHealthy=0` before 14:18; **14:18–14:21** `Healthy=1, UnHealthy=1` (2nd host registered, unhealthy while booting); **14:22–14:24** `Healthy=2` (new host passed); gap 14:25–14:30; **14:31–14:47** `Healthy=0, UnHealthy=1` (single remaining host unhealthy ~16 min — the sustained breach that tripped the alarm); **14:48 onward** `Healthy=1, UnHealthy=0` (recovered). Shape = host-replacement / rolling-deploy churn that briefly left one unhealthy host, then clean recovery. NOT the worst-case sustained resource-exhaustion pattern (host did not stay unhealthy).
- **proof:** N/A — no `$CODE_BASE` code claim; all facts are AWS CloudWatch telemetry reads.
- **script:** scratch merge/tabulate of the two metric series (run as `"$VSCODE_PYTHON" merge_health.py healthy.json unhealthy.json`):
  ```python
  import json, sys
  def load(p):
      d = json.load(open(p))["Datapoints"]
      return {x["Timestamp"]: x for x in d}
  H = load(sys.argv[1]); U = load(sys.argv[2])
  ts = sorted(set(H) | set(U))
  print(f"{'time (UTC)':<22}{'Healthy(avg/max)':>20}{'UnHealthy(avg/max)':>22}{'e1=UH-H avg':>14}  flag")
  print("-"*92)
  for t in ts:
      h = H.get(t); u = U.get(t)
      ha = h["Average"] if h else 0.0; hm = h["Maximum"] if h else 0.0
      ua = u["Average"] if u else 0.0; um = u["Maximum"] if u else 0.0
      e1 = ua - ha
      flag = "<<< ALARM (e1>=0)" if e1 >= 0 else ""
      print(f"{t[11:19]:<22}{ha:>9.2f}/{hm:<9.0f}{ua:>11.2f}/{um:<9.0f}{e1:>14.2f}  {flag}")
  print("-"*92)
  def rng(M, key):
      vals=[x[key] for x in M.values()]; return (min(vals), max(vals)) if vals else (None,None)
  print("Healthy Average range:", rng(H,"Average"), " Maximum range:", rng(H,"Maximum"))
  print("UnHealthy Average range:", rng(U,"Average"), " Maximum range:", rng(U,"Maximum"))
  breach=[t for t in ts if (U.get(t,{}).get("Average",0.0) - H.get(t,{}).get("Average",0.0)) >= 0]
  print(f"buckets with e1>=0 (would feed ALARM): {len(breach)} / {len(ts)}")
  if breach: print("  earliest:", breach[0][11:19], " latest:", breach[-1][11:19])
  ```
  The two AWS pulls that fed it:
  ```bash
  TG="Name=TargetGroup,Value=targetgroup/awseb-AWSEB-FMEJK7SXHDXP/b486f8d29c5e7ee4"
  LB="Name=LoadBalancer,Value=app/awseb--AWSEB-gn3gcMFbRam1/8071f7dc0f04652e"
  aws cloudwatch get-metric-statistics --region eu-central-1 --namespace AWS/ApplicationELB \
    --metric-name HealthyHostCount --dimensions $TG $LB \
    --start-time 2026-06-29T13:30:00Z --end-time 2026-06-29T15:30:00Z --period 60 \
    --statistics Average Maximum Minimum --output json > healthy.json
  # same for UnHealthyHostCount
  ```
- **effort:** the bundled `pull_alarm_history.py` ran unattended for state history. The metric was ApplicationELB host counts (not the CPU/SQS cases the skill's bundled analyzer covers), so I hand-pulled the two series and wrote a one-off merge/tabulate script to read the per-minute shape. `describe-alarms` output had to be extracted with several small targeted `jq` queries because a file-read output compressor was mangling large reads this session.

### [17:07] step: identify EB host behind the target group + its instance type (read-only ELB/EC2)
- **observed:** Resolved the target group and the live host behind this alarm to test the auto-triage bot's open question ("is the t3.large fix still in place?").
  - `aws elbv2 describe-target-groups --names awseb-AWSEB-FMEJK7SXHDXP` → ARN `arn:aws:elasticloadbalancing:eu-central-1:948299231917:targetgroup/awseb-AWSEB-FMEJK7SXHDXP/b486f8d29c5e7ee4`.
  - `aws elbv2 describe-target-health <arn>` → one target: `i-03a392d7a543027d7`, state **healthy** (current).
  - `aws ec2 describe-instances i-03a392d7a543027d7` → **InstanceType=t3.large**, LaunchTime **2026-06-29T14:37:29Z**, state running. Tags: `Name=stage0-api5`, `elasticbeanstalk:environment-name=stage0-api5`, `elasticbeanstalk:environment-id=e-kb56dqz2uh`, ASG `awseb-e-kb56dqz2uh-stack-AWSEBAutoScalingGroup-JR2CCC127Zcf`.
  - => The env is already on **t3.large**, so the 2025-10 preventive fix IS in place — contradicts the auto-triage bot's guess that it "may not still be in place." The current instance launched 14:37:29Z, inside the incident window (14:18–14:47), confirming the breach was an **instance replacement** (fresh t3.large booting → briefly unhealthy → healthy by 14:48 → alarm cleared 14:53:48Z).
- **proof:** N/A — AWS telemetry only, no `$CODE_BASE` code claim.
- **effort:** three chained read-only AWS calls (elbv2 describe-target-groups → describe-target-health → ec2 describe-instances), no codebase exploration.

### [17:08] step: pull EB environment events for the incident window (read-only elasticbeanstalk:DescribeEvents)
- **observed:** `aws elasticbeanstalk describe-events --environment-id e-kb56dqz2uh --start-time 2026-06-29T13:30:00Z --end-time 2026-06-29T15:30:00Z`. The event stream confirms the cause was an **EB configuration update → ASG rolling instance replacement**, not an application/resource fault:
  - 14:11:45Z — "Updating environment stage0-api5's configuration settings" → environment update started (a **config update**, triggering a rolling update).
  - 14:12:12Z — rolling update begins; temporarily raises capacity to 2 to keep ≥1 in service.
  - 14:12:58Z — added instance `i-0e7d92b04f85d7e4e`; 14:19:58Z [WARN] health Degraded ("ELB processes are not healthy on 1 out of 2 instances") while it warmed up.
  - 14:25:59Z — "New application version was deployed"; added `i-01dbc337a19135999`; "Environment update completed successfully."
  - 14:26–14:27Z — removed the original instance(s); capacity collapsing back toward Desired=1.
  - 14:33:59Z [WARN] Severe; 14:35:59Z [WARN] "ELB processes are not healthy on all instances. ELB health is failing or not available for all instances." ← the **0-healthy window** that breached the metric-math alarm.
  - 14:37:59Z — added `i-03a392d7a543027d7` (the current healthy t3.large).
  - 14:40:59Z [WARN] — "Removed instance `i-01dbc337a19135999` from your environment **due to a ELB health check failure**"; "None of the instances are sending data."
  - 14:44:59Z [WARN] Severe → **14:48:59Z Ok** (final recovery). CloudWatch alarm cleared 14:53:48Z (~5 min metric-eval lag after env health returned to Ok).
  - All instances in a single AZ (eu-central-1a); DesiredCapacity=1 (single-host staging env).
- **proof:** N/A — AWS telemetry only.
- **effort:** one read-only `elasticbeanstalk describe-events` call. Output was extracted via `jq` (the file-read compressor mangled the raw event list, but the timestamps + messages were legible enough to reconstruct the sequence).

### [17:10] [INTERVENTION] User directed: post the investigation update in Slack
- **observed:** I had delivered the full debug summary in-chat and asked for feedback. User did not propose an alternative to the investigation; instead directed the next action.
- **human supplied:** "Goof, lets post the update in slack."
- **type:** direction
- **source:** actual-user
- **what was missing:** nothing absent in the investigation — the user is steering to the outward-facing posting step (the natural end of an oncall ticket).

### [17:11] oncall-post-report (Slack MCP: Canvas + thread reply)
- **observed:** Loaded skill, applied safety rule 1 (confirm surface): asked via AskUserQuestion how to post; user chose "Canvas + thread reply (Recommended)".
  - Created Slack Canvas `F0BDGUPBRQX` (title "stage0-api5 Unhealthy (eu-central-1) — Investigation (Q0S8U8DBKHW3GG)") with the full table-structured report → https://eightfoldai.slack.com/docs/T1UL59A9M/F0BDGUPBRQX
  - Posted concise threaded reply in alert thread C07NZL0PL9K (thread_ts 1782744411.019549) with verdict/root-cause + Canvas link → https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782753084127639?thread_ts=1782744411.019549&cid=C07NZL0PL9K
  - Applied safety rule 2 (plain text, no @-mentions): no person/team/@-mention tokens in either post; assignee not @-mentioned.
- **effort:** MCP-only; one confirmation prompt + one Canvas create + one threaded reply.
- **user input:** chose "Canvas + thread reply" surface.
