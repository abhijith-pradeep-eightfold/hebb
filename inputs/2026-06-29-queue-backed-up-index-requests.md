---
task: Debug PagerDuty "[us-west-2] Queue backed up-index_requests (us-west-2)" incident from Slack thread C07NZL0PL9K ts 1782754781.403199
date: 2026-06-29
skills_used:
  - {name: wiki-reader, note: read queue-backed-up and oncall-investigation pages; confirmed oncall-queue-backed-up skill is the right runbook}
  - {name: oncall-queue-backed-up, note: main runbook skill — sequences all sub-skills}
  - {name: inspect-cloudwatch-metric, note: pull_queue_depth.py — confirmed spike curve, 5 breach buckets, peak 74811 at 17:30 UTC}
interventions: 0
---

# Debug "Queue backed up-index_requests (us-west-2)" — 2026-06-29

**Task:** User gave a Slack thread link for a PagerDuty "Queue backed up-index_requests (us-west-2)" incident and asked to debug it.

## Log

### [17:55] external-context-puller (Slack MCP read)
- **observed:** Read Slack thread C07NZL0PL9K ts 1782754781.403199 via slack_read_thread.
  - Parent from PagerDuty bot: incident "[us-west-2] Queue backed up-index_requests (us-west-2)", type Base Incident, Urgency High, Service "Core Infra", assigned to Mohit Uniyal (muniyal@eightfold.ai). PagerDuty incident Q3W5WUM7V4Y6LC. Triggered via CloudWatch alarm "[us-west-2] Queue backed up-index_requests (us-west-2)" in us-west-2.
  - Reply 1 from "Messiah" / DP On-Call Auto-Triage (v2) bot at 23:14 IST:
    - Cause: traffic spike, not a failure. `message_received` on `index_requests` jumped from ~180K/hr morning baseline to 277K/hr peak at 15:00 UTC (~55% lift, sustained 3+ hrs); ~65% above yesterday's same-hour rate.
    - Top drivers (last 4hr): starbucks.com (~75K, 3x lift), eaton.com (~40K, 3x lift), eightfolddemo-cheryl.com entity_index (27K, 11x lift 13:00→14:00 UTC — backfill signature), appliedmaterials.com (~44K, 3.5x lift).
    - Consumers healthy: failure rate <0.1%; peak hour 16:00 UTC: 73 FAIL + 35 REROUTE_TO_HIGH_MEM + 32 SEARCH_ERROR out of 207K processed.
    - State: self-resolving. Most recent 5-min bins (17:20–17:35 UTC) had processed >= dispatched in 3 of 4 bins; backlog peaked ~16:25 UTC and is shrinking.
    - 240 prior firings of this class in 1yr, ~234 self-resolved with no eng action.
  - Reply 2 from Mohit Uniyal at 23:17 IST: "the spike is indeed flattening now, waiting and watching" (with an image attached).
- **effort:** one Slack MCP read. No dead-ends.

### [17:56] wiki-reader — queue backed up / oncall
- **observed:** Read wiki index and then the oncall/queue-backed-up and oncall/oncall-investigation pages. The wiki clearly maps this ticket type to the `oncall-queue-backed-up` skill. The oncall investigation page specifies delivering a table-structured report and using `oncall-post-report` to post it to the PagerDuty Slack thread. Wiki had full coverage for this ticket type.

### [17:58] inspect-cloudwatch-metric — pull_queue_depth.py for index_requests us-west-2
- **observed:** First run crashed with `UnboundLocalError: cannot access local variable 'region'` at line 65 of pull_queue_depth.py. The line read `region = (region or ...)` — `region` was used before any assignment. Fixed to `region = (args.region or os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("EF_DEFAULT_REGION") or "us-west-2")`. Second run succeeded.
  - **proof:** `learned/skills/infra/inspect-cloudwatch-metric/scripts/pull_queue_depth.py:65`
  - Output (window 12:00–20:00 UTC, period=900s, threshold=50000):
    ```
    queue=index_requests  region=us-west-2  period=900s
    window=2026-06-29T12:00:00Z -> 2026-06-29T20:00:00Z (UTC)
    alarm threshold=50000.0  datapoints_to_alarm=None

    timestamp (UTC)              max          avg  breach
    ----------------------------------------------------------------
    2026-06-29T12:00:00Z        2943         ...
    2026-06-29T12:15:00Z        7933         ...
    ...
    2026-06-29T16:45:00Z       50466         ...    <<<
    2026-06-29T17:00:00Z       68867         ...    <<<
    2026-06-29T17:15:00Z       70943         ...    <<<
    2026-06-29T17:30:00Z       74811         ...    <<<  (peak)
    ...
    ```
  - 5 consecutive breach buckets starting 16:45 UTC. Peak depth 74,811 (150% of 50k threshold) at 17:30 UTC. Depth building from ~12:00 UTC, crossed threshold at 16:45 UTC.
- **script:** ran existing bundled script `pull_queue_depth.py` — no new scratch script. Bug fix was a one-line edit to the existing script.
- **effort:** one script run (failed), one code read to diagnose, one-line edit, second run succeeded. The bug was in the existing bundled script, not derived from scratch.

### [18:02] query-queue-throughput — inflow-vs-drain rates for index_requests us-west-2
- **observed:** Ran `query_queue_throughput.py --mode rates` for index_requests, 12:00–20:00 UTC, 15-min buckets. db_type=starrocks, table=log.processor_event_log, 24 rows returned.
  Key findings:
  - Inflow broadly stable ~51k–67k/bucket through 12:00–16:45 UTC, with one notable spike to 90,353 at 15:00 UTC (matching auto-triage bot's "277K/hr peak" = ~69k/15min; 90k = spike bucket).
  - Depth built with oscillation from 12:15 (net +6,795) — small positive net_deltas mixed with negative throughout the morning.
  - Sustained positive net_delta began at 16:15: +18,163 → +10,405 → +20,851 → +7,107 across 16:15–17:00 UTC — drain dropped while inflow held steady.
  - Self-resolution confirmed: 17:30 net_delta = -862, 17:45 = -17,672 (drain now outpacing inflow).
  - Fork verdict: **inflow-driven** (15:00 UTC spike was the root cause volume surge), with a **drain contribution** (drain fell from ~16:15 UTC, amplifying the backlog). The 15:00 spike created a wave of work that arrived at workers ~16:15–17:00, overloading drain capacity transiently.
- **script:** ran existing bundled script; no new scratch.

### [18:05] query-processor-event-log — message_dispatched × operation0,group_id for index_requests 14:00–18:00 UTC
- **observed:** Ran `query_processor_event_log.py --count-by operation0,group_id` over the breach window. db_type=starrocks, table=log.processor_event_log, groups=200 (cap hit — there are more). Top-30 drivers by raw dispatch count (14:00–18:00 UTC):
  | rank | count | operation0 | group_id |
  |------|-------|-----------|---------|
  | 1 | 42,221 | index | starbucks.com |
  | 2 | 37,465 | entity_index | starbucks.com |
  | 3 | 35,320 | entity_index | eightfolddemo-cheryl.com |
  | 4 | 28,957 | index | eaton.com |
  | 5 | 27,359 | entity_index | salesforce.com |
  | 6 | 26,697 | index | mercadolibre.com |
  | 7 | 26,667 | index | appliedmaterials.com |
  | 8 | 24,303 | entity_index | mercadolibre.com |
  | 9 | 22,360 | index | bcg.com |
  | 10 | 21,617 | entity_index | ngc.com |
  starbucks.com dominates: index (42k) + entity_index (37k) = 79,686 combined. eightfolddemo-cheryl.com entity_index at 35,320 is the 3rd highest single entry — consistent with the auto-triage bot's 11x backfill signal. Operations are `index`, `entity_index`, `store`, `populate_model` — standard indexing ops. No single-tenant smoking gun; multi-tenant broad spike across 200+ tenants.
- **script:** ran existing bundled script; no new scratch.
- **effort:** one clean run.

### [18:09] oncall-post-report — loaded, awaiting surface confirmation from user
- **observed:** Loaded oncall-post-report skill. Per the skill's non-negotiable rule 1, must confirm destination/surface before posting. Three options to offer: (a) Canvas in PD alert thread + concise threaded reply, (b) Markdown message inline in PD alert thread, (c) new message in a named channel.

### [18:08] query-queue-throughput — parent attribution (--mode parents) for index_requests 14:00–18:00 UTC
- **observed:** Ran `query_queue_throughput.py --mode parents` with parent window 12:00–18:30 UTC. db_type=starrocks, table=log.processor_event_log, 55 distinct parent ops returned.
  Top parent ops by distinct_msgs:
  | operation0 | distinct_msgs |
  |-----------|-------------|
  | trigger_event | 100,383 |
  | batch_store_and_index | 51,529 |
  | stage_advance | 34,347 |
  | write_back | 27,220 |
  | course_autocalibration_operation | 25,451 |
  | workflow_automation_trigger_reevaluation | 2,470 |
  | bulk_ta_profile_update | 2,196 |
  | store | 2,159 |
  `trigger_event` (the interceptor post_save re-seed mechanism) is the dominant parent — 100,383 distinct msgs, nearly 2x the next (`batch_store_and_index`). This is expected for a broad entity-update traffic surge: any entity save triggers `trigger_event` fan-out, which dispatches `index`/`entity_index` messages downstream.
  No single parent op is a rogue outlier — this is broad-based multi-op traffic (5 ops each contributing >25k msgs).
- **script:** ran existing bundled script; no new scratch.
- **effort:** one clean run.

### [18:12] [INTERVENTION] user redirected: investigate what caused the 15:00 UTC inflow spike
- **observed:** Had assembled the full report and was about to ask the user where to post it.
- **human supplied:** "We need to check what lead to the spike, at that time, we need to check for that window."
- **type:** direction
- **source:** actual-user
- **what was missing:** The report characterized the spike but did not drill into what triggered the 15:00 UTC inflow surge at the parent-op x group_id x time level for that specific 15:00 UTC window.

### [18:14] query-processor-event-log + query-queue-throughput — spike window drill (14:45–15:30 UTC)
- **observed:** Ran two queries narrowed to the 15:00 UTC spike window (14:45–15:30 UTC):
  1. query-queue-throughput --mode parents (parent-since 13:00, parent-until 15:45 UTC):
     - trigger_event: 32,726 distinct msgs (dominant)
     - batch_store_and_index: 5,576
     - stage_advance: 5,532
     - write_back: 531
     - (remaining ops all <100 each)
  2. query-processor-event-log --count-by operation0,group_id (spike window only):
     Top spike-window drivers:
     | count | operation0 | group_id |
     |-------|-----------|---------|
     | 17,742 | index | deloitte.com |
     | 10,000 | entity_index | eightfolddemo-cheryl.com |
     | 10,000 | entity_index | deloitte.com |
     | 7,333 | entity_index | houstonisd.org |
     | 7,179 | index | starbucks.com |
     | 6,749 | entity_index | starbucks.com |
     | 5,915 | index | eaton.com |
     | 5,233 | index | mercadolibre.com |
     | 4,742 | entity_index | mercadolibre.com |
     | 4,544 | index | appliedmaterials.com |
     deloitte.com is the top spike-window tenant: index (17,742) + entity_index (10,000 — at the 200-row per-group_id cap, actual may be higher) = 27,742+ msgs in 45 min. eightfolddemo-cheryl.com entity_index also hit the cap at 10,000. houstonisd.org entity_index at 7,333 is a new entrant not prominent in the 4-hr window. starbucks.com volume (~13,928 combined) is much lower in this 45-min window than across the full 4hr window — its dominance is sustained volume not a spike.
  The spike is driven primarily by trigger_event fan-out (32k distinct msgs), and deloitte.com + eightfolddemo-cheryl.com are the leading tenants for that 15-min burst.
- **script:** ran existing bundled scripts; no new scratch.
- **effort:** two clean runs.

### [18:18] [INTERVENTION] user wants comparative window analysis — spike vs surrounding windows
- **observed:** Had just delivered the spike-window drill (14:45–15:30 UTC top drivers). User wants to compare the spike window against surrounding windows to isolate what actually increased vs what was baseline.
- **human supplied:** "we need to check for some window around also and see what actually spiked for the time, compared to surrounding window."
- **type:** direction
- **source:** actual-user
- **what was missing:** The spike-window drill showed absolute counts but no before/after comparison to distinguish spike-specific tenants from high-baseline tenants.

### [18:20] three-window comparison scratch — spike vs pre/post windows
- **observed:** Wrote and ran a scratch script that fetches dispatch counts for three windows (pre-spike 13:30–14:45, spike 14:45–15:30, post-spike 15:30–16:30 UTC), normalises each to per-hour rate, and computes spike/pre lift ratio. Top 40 rows by spike-window rate printed. Key findings:
  - deloitte.com is unambiguous: ZERO msgs in pre-spike window, 23,656/hr (index) + 13,333/hr (entity_index) in the spike window, then ZERO again in post-spike. Lift >99x for both ops. A concentrated burst of exactly 45 minutes with no baseline before or after.
  - eightfolddemo-cheryl.com entity_index: pre=7,878/hr, spike=13,333/hr, post=8,736/hr. Lift 1.7x — elevated but not a spike; consistent with ongoing backfill job.
  - houstonisd.org entity_index: pre=10,122/hr, spike=9,777/hr. No meaningful lift — just high baseline, not the spike driver.
  - volkscience.com entity_index: pre=622/hr, spike=4,204/hr, post=3,822/hr. 6.8x lift, sustained into post-spike. New job started during spike window.
  - bms.com entity_index: pre=258/hr, spike=1,540/hr. 6x lift. Same pattern — job started around 14:45 UTC.
  - All other top tenants (starbucks, eaton, mercadolibre, appliedmaterials, bcg, qualcomm, ngc): lift 0.9–1.3x — pure baseline traffic, NOT spike contributors.
  Root cause of the 15:00 UTC burst: deloitte.com kicked off a large index + entity_index job at ~14:45 UTC that generated 23k+13k = 36k msgs/hr for exactly 45 minutes then stopped completely. This single tenant's job was the dominant spike driver.
- **script:**
  ```python scratch
  # /tmp/.../scratchpad/compare_windows.py
  # Fetches three windows via query-processor-event-log, normalises to /hr rate, computes lift.
  # Invoked: VSCODE_PYTHON=$VSCODE_PYTHON CODE_BASE=$CODE_BASE "$VSCODE_PYTHON" compare_windows.py
  import json, os, subprocess, sys

  SCRIPT = "/home/ec2-user/hebb/.claude/skills/query-processor-event-log/scripts/query_processor_event_log.py"
  PYTHON = os.environ["VSCODE_PYTHON"]
  CODE_BASE = os.environ["CODE_BASE"]
  ENV = {**os.environ, "PYTHONPATH": f"{CODE_BASE}/www"}

  WINDOWS = {
      "pre":  ("2026-06-29 13:30:00", "2026-06-29 14:45:00", 75),
      "spike":("2026-06-29 14:45:00", "2026-06-29 15:30:00", 45),
      "post": ("2026-06-29 15:30:00", "2026-06-29 16:30:00", 60),
  }

  def fetch(since, until):
      cmd = [PYTHON, SCRIPT, "--queue", "index_requests", "--event-type", "message_dispatched",
             "--since", since, "--until", until, "--count-by", "operation0,group_id",
             "--region", "us-west-2", "--limit", "200", "--format", "json"]
      out = subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=120)
      data = json.loads(out.stdout)
      return {(r["operation0"], r["group_id"]): r["cnt"] for r in data.get("rows", [])}

  counts = {name: (fetch(s, u), m) for name, (s, u, m) in WINDOWS.items()}
  all_keys = set().union(*[set(v[0]) for v in counts.values()])
  rows = []
  for key in all_keys:
      op, grp = key
      pre_hr  = counts["pre"][0].get(key, 0)  / counts["pre"][1]  * 60
      spk_hr  = counts["spike"][0].get(key, 0) / counts["spike"][1] * 60
      pst_hr  = counts["post"][0].get(key, 0)  / counts["post"][1]  * 60
      lift = spk_hr / pre_hr if pre_hr > 0 else (999.0 if spk_hr > 0 else 0.0)
      rows.append((op, grp, pre_hr, spk_hr, pst_hr, lift))
  rows.sort(key=lambda r: r[3], reverse=True)
  # ... prints table
  ```
- **effort:** one scratch script, one run. No dead-ends. The three-window fetch + normalise pattern was derived from scratch (no existing skill covers multi-window comparative lift).

### [18:28] [INTERVENTION] user: "find the root ops for these"
- **observed:** Had just delivered the three-window comparative lift table identifying deloitte.com as the dominant spike driver (>99x lift, zero before and after), with volkscience.com and bms.com as secondary.
- **human supplied:** "find the root ops for these"
- **type:** direction
- **source:** actual-user
- **what was missing:** The driver breakdown identified which tenants spiked but did not trace any SMID to its root processor op to find what kicked off the batch job.

### [18:32] trace-processor-op — root op traces for spike-window tenants
- **observed:** Ran trace_processor_op.py for one SMID each from deloitte.com, volkscience.com, bms.com. Plus two additional deloitte.com SMIDs to confirm root op pattern. All via starrocks log.processor_event_log, region us-west-2.

  **deloitte.com** — SMID 6d105633 (full JSON output available):
  Chain (depth 4):
    depth 4 (root): ingest_data_extract_operation | adhoc_file_ingest_queue | t_create 14:50:08 | parent=null
    depth 3: ingest_data_extract_operation | adhoc_file_ingest_queue | t_create 14:50:42
    depth 2: batch_store_and_index | deloitte_adhoc_batch_requests_queue | t_create 15:01:53
    depth 1: trigger_event | trigger_event_requests | t_create 15:13:39
    depth 0 (target): index | index_requests | t_create 15:13:40 | PASS
  Root: ingest_data_extract_operation on adhoc_file_ingest_queue, started 14:50:08 UTC — an adhoc file ingest job kicked off at ~14:50 UTC.

  **deloitte.com** — SMID e2ad0910 (second sample):
  Chain (depth 0 — root op itself):
    depth 0: import_activity_email | index_requests | t_create 15:14:06 | parent=null (non-UUID parent)
  Root: import_activity_email — an activity-email import dispatching directly to index_requests with no processor parent. Two distinct root ops for deloitte.com: file ingest AND email activity import running concurrently.

  **volkscience.com** — SMID f3f6359e:
  Chain (depth 2):
    depth 2 (root): ingest_data_extract_operation | file_ingest_requests_queue | t_create 15:26:13 | parent=null
    depth 1: course_autocalibration_operation | course_calibration_requests | t_create 15:29:51
    depth 0 (target): entity_index | index_requests | t_create 15:29:59 | PASS
  Root: ingest_data_extract_operation on file_ingest_requests_queue (standard ingest, not adhoc) — a file ingest triggered course calibration which dispatched entity_index to index_requests.

  **bms.com** — SMID adcc2d90:
  Chain terminates immediately: parent = Unknown-2c1a4019b2864f1c93c7d6ed88c627a1 (non-UUID, {group_id}-hex form). The walk ends at depth 0 — this message was dispatched by an external/non-processor caller (no processor_event_log row to walk to).

- **script:** ran existing bundled trace_processor_op.py; no new scratch.
- **effort:** 5 trace runs (3 parallel + 2 follow-up for deloitte confirmation). Two deloitte SMIDs were needed to confirm both root op patterns.

### [18:40] [INTERVENTION] user: "post report to slack"
- **observed:** Had just delivered the root-op trace findings and was offering options for next steps.
- **human supplied:** "post report to slack"
- **type:** direction
- **source:** actual-user
- **what was missing:** No surface specified (Canvas vs inline markdown, which thread). Must confirm per oncall-post-report rule 1 before posting.

### [18:42] [INTERVENTION] user confirmed: "post in slack" — inline markdown in the alert thread
- **observed:** Had asked the user to confirm Canvas vs inline. User said "post in slack" — treating this as inline markdown reply in the PagerDuty alert thread (C07NZL0PL9K, ts 1782754781.403199).
- **human supplied:** "post in slack"
- **type:** direction
- **source:** actual-user
- **what was missing:** No explicit Canvas vs inline disambiguation; defaulting to inline markdown in thread per "post in slack" phrasing.

### [18:44] oncall-post-report — posted inline markdown reply to PD alert thread
- **observed:** Posted full RCA report as an inline threaded reply to channel C07NZL0PL9K, thread_ts 1782754781.403199. All tenant and person references rendered as plain text (no @-mentions). Message ts: 1782757105.370749. Link: https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782757105370749?thread_ts=1782754781.403199&cid=C07NZL0PL9K
- **effort:** one Slack MCP send_message call.

## Session summary

**What was done:**
1. Fetched Slack thread C07NZL0PL9K ts 1782754781.403199 — PagerDuty "[us-west-2] Queue backed up-index_requests" alert, assigned to Mohit Uniyal.
2. Consulted wiki (queue-backed-up, oncall-investigation pages) — confirmed oncall-queue-backed-up skill is the right runbook.
3. Ran inspect-cloudwatch-metric (pull_queue_depth.py) — fixed a pre-existing UnboundLocalError bug in the script (region not assigned before use), confirmed 5 breach buckets 16:45–17:45 UTC, peak 74,811 (150% of 50k threshold).
4. Ran query-queue-throughput --mode rates — confirmed inflow-driven fork: 90k/15-min spike at 15:00 UTC, transient drain dip at 16:15 amplified the backlog, self-resolving by 17:30.
5. Ran query-processor-event-log --count-by operation0,group_id over the 4-hr breach window — top tenants by raw volume: starbucks.com (79k combined), eightfolddemo-cheryl.com (35k), eaton.com (29k).
6. Per user direction: drilled into the spike window (14:45–15:30 UTC) with a three-window comparative scratch script — deloitte.com was the unambiguous spike driver (>99x lift, zero msgs before and after the 45-min window). starbucks.com and eaton.com were flat baseline, not spike drivers.
7. Per user direction: traced representative SMIDs for deloitte.com, volkscience.com, bms.com to root ops. deloitte.com root ops: ingest_data_extract_operation (adhoc_file_ingest_queue, 14:50 UTC) + import_activity_email (external dispatch). volkscience.com: ingest_data_extract_operation (file_ingest_requests_queue) → course_autocalibration_operation → entity_index. bms.com: external non-processor dispatch (no lineage).
8. Posted full table-structured RCA report as inline markdown reply to the PD alert thread.

**Final result:** Root cause confirmed as deloitte.com adhoc file ingest + activity-email import running concurrently ~14:45–15:30 UTC, generating ~37k msgs/hr for 45 minutes. No consumer failure. Self-resolving. Report posted to Slack thread.

**Bug fixed during investigation:** pull_queue_depth.py had UnboundLocalError on line 65 (region used before assignment); fixed to args.region with proper env fallback chain. File: learned/skills/infra/inspect-cloudwatch-metric/scripts/pull_queue_depth.py

**Scratch script:** three-window comparative lift analysis in /tmp/.../scratchpad/compare_windows.py — fetches three time windows, normalises to /hr rate, computes spike/pre lift ratio.
