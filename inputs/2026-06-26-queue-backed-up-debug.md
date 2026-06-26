---
task: Debug PagerDuty "Queue backed up - ai_interview_op_queue (us-west-2)" incident from a Slack thread; user handholds each step
date: 2026-06-26
skills_used:
  - {name: external-context-puller, note: pulled the PagerDuty Slack thread; parsed channel/ts from the slack URL, read via slack_read_thread}
  - {name: log-appender, note: continuous logging throughout the session}
  - {name: query-processor-event-log, note: bundled reader has no GROUP BY / queue_name / event_type / absolute-window filters; exposed reusable util run_select + resolve_db_type_and_table}
  - {name: task-executer, note: wrote scratch op_breakdown.py (ran, approved) and codeowners_for.py (written, NOT run — approval withheld)}
  - {name: trace-processor-op, note: bundled tracer walked culprit SMID to root op sync_ats; auto-allowed, no approval}
interventions: 6
---

# Debug "Queue backed up - ai_interview_op_queue" incident

**Task:** The user linked a Slack PagerDuty thread and asked to debug the queue-backed-up incident, saying they will handhold and I should ask for each step.

## Log

### [18:03] external-context-puller
- **observed:** Read the Slack thread `C07NZL0PL9K / 1782417548.955709`. Parent is a PagerDuty alert: `[us-west-2] Queue backed up-ai_interview_op_queue (us-west-2)`, High urgency, Service "Core Infra", triggered via a CloudWatch alarm `[us-west-2] Queue backed up-ai_interview_op_queue (us-west-2)`. Thread has 7 replies, all from Jack Lin except the last:
  - R1 "Outlier spike" (image.png F0BE3P315NU)
  - R2 "Already at maxed allocation" (image.png F0BDBAR04N5)
  - R3 "Seems like autodesk is the outlier groupid `autodesk-sandbox.com`" — with a db_explorer query against `processor_event_log` (database `redshift_log`): `SELECT group_id, operation0, count(*) FROM processor_event_log WHERE queue_name = 'ai_interview_op_queue ' AND event_type = 'message_dispatched' AND t_create >= '2026-06-25 17:58' and t_create <= '2026-06-25 21:58' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 100` (image F0BD9FYDSRG)
  - R4 "the operation `ai_interview_competency_generation` is spawned by the operation `sync_ats`" — with a query collecting `processor_parent_msg_id` for the autodesk-sandbox spike (group_id=`autodesk-sandbox.com`, operation0=`ai_interview_competency_generation_operation`) then grouping the parents by queue_name, operation0 (image F0BD7E34NNA)
  - R5 "`sync_ats` is the root operation" cc Vishal Shetty + a subteam
  - R6 "coming down now, resolving" (image F0BE4BD47T2)
  - R7 (Vishal Shetty) "ptal at this @Adarsh @Pranati Balodia"
- **observed:** Note the queue_name literal carries a trailing space: `'ai_interview_op_queue '`. The investigation by Jack Lin already reached a root op (`sync_ats`) via the processor_event_log parent-walk; the queue dimension (`queue_name`, `event_type = 'message_dispatched'`) is used here as the filter.

### [18:12] [INTERVENTION] user taught the oncall methodology: start from the alarm + metric and characterize the spike shape
- **observed:** I had summarized Jack's completed triage and asked the user which of three directions to take.
- **human supplied:** This is an oncall ticket. Step 1: note the region (us-west-2) and the resource type (a queue — queues are consumed by the processor). Step 2: find the alarm — it's in CloudWatch here, but be aware some alarms live in Azure / OCI equivalents for non-AWS cloud types, not always CloudWatch. Step 3: open the metric behind the alarm in CloudWatch and analyze what happened when it fired — by how much it rose, and whether it was a sudden rise or a gradual rise.
- **type:** direction
- **source:** actual-user
- **what was missing:** no skill fired for a "queue backed up" oncall alarm; the existing CloudWatch skill (`inspect-cloudwatch-cpu`) covers EC2 CPUUtilization alarms only, not an SQS queue-depth alarm, and no wiki page maps a queue-backed-up alarm to its backing SQS metric or to the processor that consumes the queue. I did not on my own start from the alarm/metric.

### [18:18] step: characterize the alarm + pull the SQS queue-depth metric curve
- **observed:** `describe-alarms` for prefix `[us-west-2] Queue backed up-ai_interview_op_queue` returns a **metric-math** alarm: expression `e1 = SUM(METRICS())`, single underlying metric `m1 = AWS/SQS · ApproximateNumberOfMessagesVisible`, dimension `QueueName = ai_interview_op_queue` (NO trailing space, unlike the `processor_event_log.queue_name = 'ai_interview_op_queue '` literal), Stat `Maximum`, Period `900s`. Threshold **50000**, `GreaterThanOrEqualToThreshold`, EvaluationPeriods 4, DatapointsToAlarm 4 (≈60 min sustained ≥50k to fire). Currently OK; recovery datapoint 49946 at 2026-06-25 21:39:00 UTC.
- **observed:** `get-metric-statistics` for that metric (Maximum + Average, 900s) over 2026-06-25T15:00Z→2026-06-26T02:00Z gives the curve:
  - baseline ≈ 0 (max 17 at 15:00Z, 379 at 15:15Z) — queue essentially idle.
  - **sudden onset** 15:15→15:30Z (max jumps 379 → 4636), then a **steep, near-linear rise** ~3k msgs/15min for ~4.5h.
  - crosses 50k threshold at 19:00Z (max 50473); **peak ≈ 57842 at 19:45–20:00Z** (~16% over threshold).
  - **gradual linear decay** afterward ~1k/15min; back below 50k at 21:30–21:45Z (matches alarm OK datapoint 49946 @ 21:39Z); still elevated ~34205 at 01:45Z.
  - PagerDuty fired 2026-06-26 01:29 IST = 2026-06-25 19:59 UTC — consistent with the 4×15min ≥50k rule given the 19:00Z threshold crossing.
  - Shape = large backlog built over ~4.5h then drained slowly; not a one-off blip. Consistent with Jack's R2 "already at maxed allocation" (producer outpaced maxed consumers).
- **proof:** alarm/metric are AWS resources, not vscode code — no repo link. (queue→processor consumer relationship to be confirmed in a later step.)
- **script:** two read-only AWS CLI calls (region us-west-2, profile bedrock-role from settings), marked `scratch`:
  ```bash
  # 1) alarm definition
  aws cloudwatch describe-alarms --region us-west-2 \
    --alarm-name-prefix "[us-west-2] Queue backed up-ai_interview_op_queue" \
    --query "MetricAlarms[].{Name:AlarmName,Metric:MetricName,NS:Namespace,Stat:Statistic,Threshold:Threshold,Op:ComparisonOperator,Period:Period,Eval:EvaluationPeriods,DP:DatapointsToAlarm,TreatMissing:TreatMissingData,Dims:Dimensions,State:StateValue,Reason:StateReason}" --output json
  # then ...Metrics to get the metric-math array (e1=SUM(METRICS()), m1=AWS/SQS ApproximateNumberOfMessagesVisible, QueueName=ai_interview_op_queue, Maximum, 900s)

  # 2) metric curve
  aws cloudwatch get-metric-statistics --region us-west-2 \
    --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible \
    --dimensions Name=QueueName,Value=ai_interview_op_queue \
    --start-time 2026-06-25T15:00:00Z --end-time 2026-06-26T02:00:00Z \
    --period 900 --statistics Maximum Average \
    --query "sort_by(Datapoints,&Timestamp)[].{t:Timestamp,max:Maximum,avg:Average}" --output table
  ```
- **effort:** adapted the `inspect-cloudwatch-cpu` access pattern (AWS CLI describe-alarms → get-metric-statistics) from EC2 CPU to an SQS queue-depth metric-math alarm; had to read the `Metrics` array (not Metric/Namespace, which are null for metric-math alarms) to discover the backing metric. No CPU-specific script reused. Timezone reconciliation IST↔UTC done by hand.

### [18:19] [INTERVENTION] user directed the next step: from processor_event_log, find which operation0 types drove the spike
- **observed:** I had reported the spike characterization and asked the user to choose the next step (I had earlier offered "reproduce/verify Jack's findings — re-run the outlier-spike and parent-walk queries").
- **human supplied:** "Ok, from processor event log, lets see what all operation types operation0 caused this this spikes."
- **type:** direction
- **source:** actual-user
- **what was missing:** I asked the user to pick the next investigative step rather than proceeding from the spike characterization to the processor_event_log breakdown on my own.

### [18:22] query-processor-event-log (skill) — assessed compiled path for an operation0 breakdown
- **observed:** Invoked the `query-processor-event-log` skill and read its bundled reader + the wiki page. The bundled reader (`scripts/query_processor_event_log.py`) exposes filters `--msg-id/--parent-msg-id/--group-id/--operation/--since-hours/--limit` and returns raw rows (newest-first, limit default 200) — it has **no `operation0` GROUP BY / COUNT aggregation, no `queue_name` filter, no `event_type` filter, and only a relative `--since-hours` window (no absolute time range)**. The user's ask (which operation0 types drove this queue's spike) is a GROUP BY aggregate scoped to `queue_name = ai_interview_op_queue` + `event_type = message_dispatched`, so the reader alone can't produce it.
- **observed:** The shared util `learned/hebb_utils/processor/event_log.py` exposes `resolve_db_type_and_table()` (returns region-resolved `(db_type, table)` for the `REDSHIFT_LOG`→warehouse routing) and `run_select(query, db_type)` (runs an arbitrary read-only SELECT via `dwh.get_list`). Both are reusable to run Jack's GROUP BY through the same adapter-factory path the reader uses, rather than hardcoding StarRocks.
- **observed:** Timezone of `t_create` is unresolved: the wiki (`process/incident-metric-correlation`) states `t_create` is IST, but Jack's thread window `2026-06-25 17:58→21:58` aligns with the CloudWatch spike (15:15–21:45 UTC) only if his `t_create` was treated as **UTC** (17:58–21:58 UTC = the back half of the rise + peak + decay; his investigation replies were ~21:53–21:59 UTC). Will use a wide absolute window covering both interpretations and confirm tz from the data's temporal concentration.
- **proof:** reader filters — `query_processor_event_log.py:34-50`; util read path — `learned/hebb_utils/processor/event_log.py` `run_select`/`resolve_db_type_and_table`; table model + REDSHIFT_LOG routing — `www/db/base_log_event.py:181,199-213` (per wiki `processor/processor-event-log`).
- **effort:** no deep exploration — wiki page + skill scripts already in the compiled artifacts; identified the reader's aggregation gap and the reusable util entrypoints by reading the two skill files.

### [18:50] task-executer — ran the operation0 breakdown (user approved)
- **observed:** User approved the scratch script ("yes"). It ran via `PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON"`. **`resolve_db_type_and_table()` returned `db_type=starrocks`, `table=log.processor_event_log`** — in us-west-2 the logical `REDSHIFT_LOG` resolves to **StarRocks**, confirming the wiki's "observed: resolved to starrocks" note (so Jack's db_explorer `database=redshift_log` and this StarRocks path are the same logical table).
- **observed:** Breakdown of `message_dispatched` on `ai_interview_op_queue` over `2026-06-25 12:00 → 2026-06-26 04:00`, GROUP BY operation0, group_id (TOTAL = 77,752):
  - **`ai_interview_competency_generation_operation` / `autodesk-sandbox.com` = 74,371** — i.e. **95.6%** of all dispatches on the queue in the window. The single dominant driver.
  - distant tail: `ai_interview_competency_generation_operation` / `eightfolddemo-sbailey9.com` ≈ 545; `…eightfolddemo…` ≈ 537; `…volkscience…` ≈ 116; all other ops (`post_ai_interview_call_processing`, `ai_interview_media_processing_operation`, `ai_interview_feedback_generation_operation`, `ai_interview_activity_proctoring_operation`, `ai_interview_scoring_operation`, `ai_interview_rag_eval_operation`, `ai_interview_billing_processing_operation`, …) in the tens — normal background levels.
  - Reproduces and sharpens Jack's R3: outlier group_id = `autodesk-sandbox.com`; outlier op = `ai_interview_competency_generation_operation`. The 74,371 dispatched ≈ the ~57.8k peak-visible (some drained concurrently while enqueuing continued).
- **proof:** warehouse routing / table name — `learned/hebb_utils/processor/event_log.py` `resolve_db_type_and_table` (model `www/db/base_log_event.py:199-213`); the row data is warehouse content, not code.
- **script:** the `op_breakdown.py` scratch script shown in the prior reply; reused `event_log.resolve_db_type_and_table()` + `event_log.run_select(query, db_type)`; query: `SELECT operation0, group_id, COUNT(*) AS cnt FROM log.processor_event_log WHERE TRIM(queue_name)='ai_interview_op_queue' AND event_type='message_dispatched' AND t_create>='2026-06-25 12:00:00' AND t_create<='2026-06-26 04:00:00' GROUP BY operation0, group_id ORDER BY cnt DESC LIMIT 100`.
- **effort:** the wide window made the tz question moot for the aggregate — the autodesk spike dominates either reading. Reused the compiled util's read path directly; the only new code was the GROUP BY query string + presentation.

### [18:58] [INTERVENTION] user directed: trace culprit to root op, find the OP file's owners, and produce a consolidated oncall report
- **observed:** I had reported the operation0 breakdown and offered to trace to the root op next.
- **human supplied:** "here we can see there is clear culprit. For the culprits, lets see what is the starting op for this, are these generated from any parent op. mention the parent op. Lets see who are owners of that OP, from file owner of the op file operation core file in the response and mention that also in the analysis. Also For oncall report always try to provide a good report like all the tables you provided so far should be there in the final summary."
- **type:** direction
- **source:** actual-user
- **what was missing:** (a) no skill/wiki maps a processor `operation0` name to the source file that defines it (the "operation core file") in `$CODE_BASE`; (b) no skill/wiki resolves the owner(s) of a source file (CODEOWNERS / git authorship); (c) no skill/template exists for assembling a consolidated oncall incident report (alarm metric table + group/op breakdown + lineage + ownership) — the user had to specify the report shape and that all prior tables must be carried into the final summary.

### [19:01] trace-processor-op — walked culprit SMID to root op
- **observed:** Grabbed a representative culprit SMID via the bundled `query-processor-event-log` reader (`--group-id autodesk-sandbox.com --operation ai_interview_competency_generation_operation --since-hours 24 --limit 3 --format json`, auto-allowed): `3a29a834-9e60-4177-8dd6-9dee50ac88f4`, parent `a7baecb6-11cf-4197-8ab8-bec0057aafc0`. Its `t_create` = `2026-06-25 20:18:23` — a real spike-window message.
- **observed:** `trace_processor_op.py 3a29a834-…` walked to a 2-hop chain:
  - ROOT: `a7baecb6-…` op **`sync_ats`**, parent = null, queue `ingest_sync_requests`, group `autodesk-sandbox.com`, t_create `2026-06-25 20:13:29`, status PASS.
  - → child `3a29a834-…` op **`ai_interview_competency_generation_operation`**, queue `ai_interview_op_queue`, t_create `2026-06-25 20:18:23`, status PASS.
  - So the culprit op is dispatched **directly by `sync_ats`** (1 hop up = the root), ~5 min after the parent ran. Confirms Jack's R4 ("ai_interview_competency_generation spawned by sync_ats") and R5 ("sync_ats is the root operation").
- **observed (tz):** the trace timestamps align with the **UTC** CloudWatch spike, not IST: parent `sync_ats` 20:13 / child 20:18 read as UTC fall in the spike decay (peak 19:45–20:00 UTC); read as IST (14:43–14:48 UTC) they'd predate the 15:15 UTC onset. This leans toward `t_create` being stored **UTC** in this StarRocks warehouse, contra the `process/incident-metric-correlation` wiki note that says `t_create` is IST.
- **proof:** lineage walk semantics — `trace-processor-op` skill over `learned/hebb_utils/processor/event_log.py` `walk_parent_chain`; parent edge column `processor_parent_msg_id` per wiki `processor/processor-event-log` (`www/db/base_log_event.py:231`).
- **effort:** both reads used bundled, auto-allowed skill scripts (no new code, no approval). The tz observation came from cross-referencing the trace timestamps against the already-established CloudWatch UTC curve.

### [19:06] step: map ops → source files (op_registry) and resolve owners (CODEOWNERS + git)
- **observed:** Op name → source file is resolved through the processor **op registry** `www/processor/op_registry.py`: each entry maps `operation0` → `(module_path, ClassName)`.
  - `'ai_interview_competency_generation_operation'` → module `processor.ai_interview_competency_generation_operation`, class `AIInterviewCompetencyGenerationOperation` (`op_registry.py:230`) → file **`www/processor/ai_interview_competency_generation_operation.py`**.
  - `'sync_ats'` → class `SyncAtsOperation` (`op_registry.py:42`) → file **`www/processor/sync_ats_operation.py`**.
- **observed (owners — root op `sync_ats_operation.py`):** CODEOWNERS line 361 `/www/processor/sync_ats_operation.py @EightfoldAI/dp-integrations`. Git top authors all-time: Sivasankaran Chandrasekar (25), Anurag Nilesh (12), Varun Kacholia (11), kevincherian (11), hareeshp (8). Most recent commit 2026-06-16 (#109634, Chandrakanta Mohapatra) — an ATS-sync change ~10 days before the incident.
- **observed (owners — culprit op `ai_interview_competency_generation_operation.py`):** **No CODEOWNERS owner** — the file matches no pattern: no dedicated line, no `/www/processor/` directory rule (the only one is `workflow_automation_processor/`, line 547), no `*operation*` glob, no `/www/` rule, no `*.py` catch-all, and **no global `*` default** in the 1300-line file. De-facto owner via git: **Adarsh Shah is the sole author (all 4 commits; most recent 2026-01-28)**. This matches the Slack thread's closing R7 "ptal at this @Adarsh …".
- **observed:** CODEOWNERS "last matching pattern wins"; with no glob/default matching the culprit file, the formal owner set is empty.
- **proof:** registry mappings — `www/processor/op_registry.py:42` (`SyncAtsOperation`), `:230` (`AIInterviewCompetencyGenerationOperation`); ownership — `.github/CODEOWNERS:361` (sync_ats → dp-integrations), and the absence checks above; git authorship via `git log --format=%an -- <file>`.
- **effort:** found the op→file mapping by grepping the op name string, which led to `op_registry.py` (the central name→class table). Ownership required two sources because CODEOWNERS doesn't cover the culprit file — had to prove the absence (check globs/dir-rules/default) and fall back to git authorship. No prior wiki/skill for op→file or file→owner; derived from scratch this session.

### [19:09] [INTERVENTION] user directed: resolve the GitHub team handle @EightfoldAI/dp-integrations to its name/members
- **observed:** I had delivered the consolidated report with the CODEOWNERS handle `@EightfoldAI/dp-integrations` for the root op, and offered next steps.
- **human supplied:** "Lets just look for the Team name @EightfoldAI/dp-integrations"
- **type:** direction
- **source:** actual-user
- **what was missing:** no skill/wiki resolves a CODEOWNERS GitHub team handle (`@org/team`) to the team's display name / description / members; the CODEOWNERS lookup stops at the handle. `gh` CLI is authenticated here (account `abhijith-pradeep-eightfold`).

### [19:10] step: attempt to resolve @EightfoldAI/dp-integrations via gh api — blocked by token scope
- **observed:** `gh api orgs/EightfoldAI/teams/dp-integrations` and `.../members` → **HTTP 404 Not Found**. `gh api orgs/EightfoldAI/teams` (list) → **HTTP 403 "Resource not accessible by personal access token"**. The authenticated credential is a **fine-grained PAT** (`github_pat_11…`) lacking org-team read permission; the per-team 404 is the same restriction surfacing as not-found. Did not fabricate the member list.
- **observed:** the formal identifier is the CODEOWNERS handle itself: org `EightfoldAI`, team slug **`dp-integrations`** (`.github/CODEOWNERS:361`). The GitHub display name / description / membership could not be retrieved with this token.
- **observed:** live human signals available without the team API: the incident Slack thread cc'd a **Slack subteam `S0924LXN3FX`** (R5, alongside the `sync_ats is root` finding) and tagged **Vishal Shetty**, **Adarsh**, **Pranati Balodia** (R5/R7); git authors of `sync_ats_operation.py` (dp-integrations-owned) are S. Chandrasekar, A. Nilesh, V. Kacholia, kevincherian, hareeshp.
- **proof:** handle/slug — `.github/CODEOWNERS:361`; the gh errors are API responses, not repo code.
- **effort:** no exploration; one gh call + a permission diagnostic. The blocker is credential scope, recorded as a symptom rather than worked around with a guess.

### [19:15] [INTERVENTION] user directed: (a) write a reusable CODEOWNERS-resolver script; (b) draft the Slack reply, post only after approval
- **observed:** I had reported that I could give the team identifier but not the GitHub roster (token-scoped out), and offered routes.
- **human supplied:** "team identifier is enough, just for this if you ever whant to find it again, write a script that will go through the CODEOWNERS. Show me slack message, and post only after approval."
- **type:** direction
- **source:** actual-user
- **what was missing:** the file→owner resolution this session was done ad-hoc with several `rg` calls against CODEOWNERS (including manually proving the no-match/last-match-wins case); there is no reusable script/skill that, given a repo-relative path, applies CODEOWNERS last-match-wins semantics and returns the owner set. The user explicitly asked for one as durable tooling. Also: the consolidated report should be delivered to the incident Slack thread, gated on explicit approval before any post.

### [19:18] [INTERVENTION] user directed: include the tables in the Slack report too
- **observed:** My drafted Slack reply used prose/bullets only; the chat report had the tables but the Slack draft summarized them as text.
- **human supplied:** "can you put the tables details also in the report."
- **type:** direction
- **source:** actual-user
- **what was missing:** I did not carry the user's earlier standing instruction ("for an oncall report always provide all the tables ... in the final summary") into the Slack draft — the tables were dropped when moving the report from chat into the Slack message, and the user had to re-request them.

### [19:20] step: posted the consolidated oncall report to the Slack incident thread (approved)
- **observed:** User approved option "2" (post) only — did NOT approve option "1" (run the `codeowners_for.py` script), so the script was written but **not executed/validated** this session. Posted the report (with all four tables) as a threaded reply via `slack_send_message` to `C07NZL0PL9K` thread_ts `1782417548.955709`. Result message_ts `1782501637.804469`, link `https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782501637804469?thread_ts=1782417548.955709&cid=C07NZL0PL9K`.
- **script:** `codeowners_for.py` (scratch, written but unrun — approval was withheld for the validation run). Full source — pure stdlib CODEOWNERS resolver, last-match-wins + gitignore-glob semantics, path→owners:
  ```python
  #!/usr/bin/env python3
  """Resolve CODEOWNERS owner(s) of repo-relative path(s). Last matching pattern wins."""
  import argparse, os, re, sys

  def _pattern_to_regex(pat):
      raw = pat
      dir_only = raw.endswith("/")
      anchored = raw.startswith("/") or ("/" in raw.rstrip("/"))
      body = raw.strip("/")
      esc = re.escape(body)
      esc = (esc.replace(r"\*\*", "\x00").replace(r"\*", "[^/]*")
                .replace("\x00", ".*").replace(r"\?", "[^/]"))
      prefix = "^" if anchored else r"(^|.*/)"
      suffix = r"/.*$" if dir_only else r"(/.*)?$"
      return re.compile(prefix + esc + suffix)

  def _load_rules(path):
      rules = []
      with open(path) as fh:
          for lineno, line in enumerate(fh, 1):
              s = line.strip()
              if not s or s.startswith("#"):
                  continue
              parts = s.split()
              rules.append((lineno, parts[0], parts[1:], _pattern_to_regex(parts[0])))
      return rules

  def resolve(path, rules):
      norm = path.strip("/")
      winner = None
      for lineno, pat, owners, rx in rules:
          if rx.match(norm):
              winner = (lineno, pat, owners)
      return winner

  def main(argv=None):
      ap = argparse.ArgumentParser()
      ap.add_argument("paths", nargs="+")
      ap.add_argument("--codeowners")
      args = ap.parse_args(argv)
      co = args.codeowners
      if not co:
          base = os.environ.get("CODE_BASE", ".")
          for cand in (os.path.join(base, ".github/CODEOWNERS"), ".github/CODEOWNERS",
                       "CODEOWNERS", "docs/CODEOWNERS"):
              if os.path.isfile(cand):
                  co = cand; break
      if not co or not os.path.isfile(co):
          print("error: CODEOWNERS file not found", file=sys.stderr); return 2
      rules = _load_rules(co)
      print(f"CODEOWNERS: {co}  ({len(rules)} rules)\n")
      for p in args.paths:
          w = resolve(p, rules)
          if w:
              lineno, pat, owners = w
              print(f"{p}\n    owners : {' '.join(owners)}\n    rule   : {pat}  (line {lineno})\n")
          else:
              print(f"{p}\n    owners : (none — no matching CODEOWNERS rule)\n")
      return 0

  if __name__ == "__main__":
      sys.exit(main())
  ```
  Intended invocation: `python3 codeowners_for.py www/processor/sync_ats_operation.py www/processor/ai_interview_competency_generation_operation.py` (expected: sync_ats → `@EightfoldAI/dp-integrations` line 361; culprit → none).
- **effort:** message assembled from already-gathered findings (no new queries); the script encodes the CODEOWNERS last-match-wins semantics that were applied by hand earlier this session.

### [19:32] close-out
- **observed:** User asked to close out. Final state of the investigation:
  - **Incident:** PagerDuty `[us-west-2] Queue backed up-ai_interview_op_queue` (High, Core Infra); fired 2026-06-26 01:29 IST (2026-06-25 19:59 UTC), recovered ~21:45 UTC. Metric = `AWS/SQS ApproximateNumberOfMessagesVisible`, Max/900s, threshold ≥50k for 4 datapoints; peak ~57.8k.
  - **Root cause:** bulk `sync_ats` for `autodesk-sandbox.com` fanned out ~74,371 `ai_interview_competency_generation_operation` dispatches (95.6% of the 77,752 in-window) onto `ai_interview_op_queue` while consumers were already at max allocation. Lineage `sync_ats` (root, queue `ingest_sync_requests`) → `ai_interview_competency_generation_operation` (queue `ai_interview_op_queue`).
  - **Ownership:** root op `sync_ats_operation.py` → `@EightfoldAI/dp-integrations` (CODEOWNERS); culprit op `ai_interview_competency_generation_operation.py` → no CODEOWNERS owner, de-facto Adarsh Shah (sole git author, matches thread tag).
  - **Deliverable:** consolidated report (4 tables + follow-ups) posted to the Slack incident thread (ts 1782501637.804469).
- **observed:** Open items left unfinished at user's close-out: (1) `codeowners_for.py` written but not validated (run approval withheld); (2) whether the autodesk-sandbox ATS sync was an expected backfill vs. runaway was not investigated; (3) the GitHub team display name/roster for `@EightfoldAI/dp-integrations` could not be resolved (PAT lacks org-team read).
- **effort:** no new work at close-out; finalized the frontmatter index and recorded final state.
