---
task: Triage the "Redis Error Detected - namespace" P2 PagerDuty page for ranking_service (eu-central-1), prioritizing the Confluence runbook's steps.
date: 2026-06-29
skills_used:
  - {name: wiki-reader, note: oncall-investigation umbrella page + ticket-type catalog exist; NO page for "Redis Error Detected" ticket type}
  - {name: external-context-puller, note: pulled the PD Slack thread and the Confluence runbook via Slack + Atlassian MCP}
interventions: 4
---

# Redis Error Detected — ranking_service (eu-central-1) triage

**Task:** Triage the P2 "Redis Errors Detected - ranking_service (eu-central-1)" PagerDuty page. User asked to PRIORITIZE the Confluence runbook's prescribed steps: read the runbook, read the Slack alert thread, then execute the runbook steps in order against the specific alert. Diagnosis only — no Slack/Confluence/PD posts, no live-infra remediation without checking back.

## Log

### [21:55] wiki-reader — check coverage for "Redis Error Detected" ticket type
- **observed:** Read `learned/wiki/index.md` and `learned/wiki/oncall/oncall-investigation.md`. The Oncall section catalogs four ticket types — Queue backed up, Solr CPU too high, Alarm Provisioning Failures, RDS CPU too high — each with its own page + high-level runbook skill (`oncall-queue-backed-up`, `oncall-solr-cpu-high`, etc.). There is NO wiki page and NO `oncall-*` runbook skill for a "Redis Error Detected" / ElastiCache / Redis-errors ticket type. The umbrella page gives the shared discipline (read alarm → characterize metric → find driver → trace & route → report) and notes some non-AWS types page from other clouds; nothing Redis/ElastiCache-specific.
- **effort:** Two wiki reads (index + umbrella). No Redis ticket-type coverage to build on; the per-type runbook for this alert is the Confluence page, not a Hebb artifact.

### [21:57] external-context-puller — pull PD Slack thread + Confluence runbook
- **observed:** Loaded Slack `slack_read_thread` and Atlassian `getConfluencePage` via ToolSearch, then fetched both.
  - **Slack thread** (channel C07NZL0PL9K = core_infra-oncall, parent ts 1782764010.985239):
    - Parent = PagerDuty bot: ":large_yellow_circle: P2: Redis Errors Detected - ranking_service (eu-central-1)", incident `Q337QAU16KW1XZ`, type Base Incident, **Assigned: Mohit Uniyal**, Urgency High, **Service: Core Infra**, triggered via CloudWatch alarm named exactly `P2: Redis Errors Detected - ranking_service (eu-central-1)` in region **eu-central-1**. Parent time 2026-06-30 01:43:30 IST = **2026-06-29 20:13:30 UTC**.
    - One reply = a bot, "DP On-Call Auto-Triage (v2)" (`dp_oncall_auto_triage_v2`, session fb29ac28c6bb, 570s). Its auto-RCA claims: matches recurring transient-blip pattern (15× prior in eu-central-1, every archived precedent ack→auto-resolved); no Redis-path code change in 14d (`git log` on `www/utils/redis_utils.py`, `monitoring/alarms/redis_alarms.py`, `www/services/ranking_service/`); the one ranking_service-touching PR `20231251f98` (gunicorn worker halving removal) is gated on `CLOUD_TYPE=='azure'`, a no-op on AWS eu-central-1. History: most recent precedent `Q1FE55VHUVSYOM` (2026-05-07) ack→recovered→resolved, no node event cited, no follow-up fix; class INTERMITTENT. Scope: single-region single-namespace; class also fires in westus2 (16×) → not single-cluster-hardware. **Caveat: the bot produced its RCA WITHOUT a direct metric/log read** — its Step1 `aws cloudwatch get-metric-statistics`, Step2 Logs Insights, Step3 ElastiCache FreeableMemory/EngineCPUUtilization all returned AccessDenied for IAM user `arn:aws:iam::948299231917:user/prod-search` (cloudwatch:*, logs:FilterLogEvents, elasticache:* denied in eu-central-1). It references a runbook `skills/redis_errors_detected.md` (the bot's own, not a Hebb skill).
  - **Confluence runbook** ("Redis Error Detected - {namespace}", page 1972142240, space EP, last modified Feb 19 2024, author Strayn Wang): a single-step log-inspection runbook. The alarm's own `AlarmDescription` links a different page — the "Core Infra Playbook - PagerDuty" (page 1600126992). The namespace runbook verbatim: "To get error logs, go to cloudwatch and select log group according to namespace. For example if namespace is ranking_service then log group will be RankingService. After this you can run following query." The query (a CloudWatch **Logs Insights** query) is:
    ```
    fields @timestamp, @message, @logStream, @log
    | filter @message like "Got error executing" and @message like "redis"
    | sort @timestamp desc
    | limit 1000
    ```
    The page's example link points at us-west-2; the namespace→log-group mapping is ranking_service → `RankingService`.
- **effort:** Slack ts parsed from the p-URL (p1782764010985239 → 1782764010.985239). Atlassian page fetched by hostname cloudId + pageId straight from the URL; both succeeded first try. The runbook is short and prescribes exactly one investigative action: a Logs Insights query on the namespace's log group for the verbatim Redis error line.

### [22:00] Check this box's AWS identity + read the alarm definition (eu-central-1)
- **observed:** `aws sts get-caller-identity` → this agent box is IAM user `arn:aws:iam::948299231917:user/anupam.singh` (same account 948299231917 as the bot, but a DIFFERENT user than the bot's `prod-search`). `aws cloudwatch describe-alarms --region eu-central-1 --alarm-names "P2: Redis Errors Detected - ranking_service (eu-central-1)"` SUCCEEDED (full CloudWatch read — this box is NOT denied the way the bot's prod-search user was). Alarm facts:
  - Metric: Namespace `ranking_service`, MetricName `prod-ranking-service-redis-errors.sum`, Stat=**Sum**, Period=300s, no dimensions.
  - Threshold: **> 100**, EvaluationPeriods=2, DatapointsToAlarm=2 (2-of-2), ComparisonOperator GreaterThanThreshold, **TreatMissingData=notBreaching**.
  - Actions: SNS PagerDuty + errors_volkscience_com. AlarmDescription links the "Core Infra Playbook - PagerDuty" page (1600126992).
  - **Current StateValue = OK** (already self-resolved). StateReason: "Threshold Crossed: 1 out of the last 2 datapoints [100.0 (29/06/26 20:09:00)] was not greater than the threshold (100.0)" — ALARM→OK at StateUpdatedTimestamp **2026-06-29T20:14:28Z**. StateReasonData recentDatapoints `[316.0, 100.0]`.
- **effort:** Single describe-alarms call. The identity check was the deciding move: the bot's RCA was produced WITHOUT metric/log reads because its prod-search user was AccessDenied; this box's anupam.singh user CAN read CloudWatch/Logs in eu-central-1, so the runbook is actually executable here.

### [22:01] inspect the metric timeseries — characterize the spike
- **observed:** `aws cloudwatch get-metric-statistics` for `ranking_service`/`prod-ranking-service-redis-errors.sum` Sum, 300s, 18:00–21:00Z. No datapoints before 20:05 (consistent with notBreaching-on-missing — errors near-zero at baseline). Spike shape (Sum per 5-min bucket, UTC):
    - 20:05:00 = **360** (breach; alarm's StateReasonData phrased it as 316/100 across its own eval windows)
    - 20:10:00 = 63
    - 20:15:00 = 18, 20:20 = 6, 20:25 = 3, 20:30 = 3, 20:35 = 1, 20:40 = 2, 20:45 = 1, 20:50 = 1, 20:55 = 2
  - A single sharp burst (~360 errors in one 5-min bucket) decaying to a 1–3/5min trickle within ~20 min. Sudden onset, fast decay, self-resolved — matches the "transient blip" class the auto-triage bot described.
- **effort:** One get-metric-statistics call piped through a small python sorter. The curve cleanly confirms a transient spike, not a sustained outage.

### [22:02] runbook STEP — run the runbook's exact Logs Insights query on RankingService
- **observed:** Confirmed log group `RankingService` exists in eu-central-1 (`aws logs describe-log-groups --log-group-name-prefix Ranking` → `Ranking`, `RankingService`). Ran the runbook's VERBATIM query (`filter @message like "Got error executing" and @message like "redis"`) over 20:00–20:30Z. Result: **status Complete, recordsMatched = 0** (recordsScanned 1,301,652; bytesScanned 614 MB). The runbook's literal filter string matched ZERO of the 1.3M records in the spike window.
- Broadening attempts to find the actual error-line shape:
    - `filter @message like /(?i)redis/` → 127,258 matches, but all are normal `[Redis HIT]` cache-hit lines (`redis_utils.py:823`) and `request_tracelog.py:513` latency tracelogs (which embed `redis.get`/`redis.set` event entries). No error lines.
    - `filter /(?i)redis/ and /(?i)(error|exception|timeout|connection|refused|...)/` → only 30 matches in 30 min; still dominated by tracelog lines (matched on substrings inside the latency JSON) and benign `redis_utils.py:190/198` "Creating redis connection for ... persist-prod.kffviu.clustercfg.euc1.cache.amazonaws.com:6379" info lines.
    - `filter @message like /^[EWF]\d{4}/` (glog error/warn/fatal level) → 17,609 matches in 30 min, but the 300-row sample was saturated entirely by 20:00 and was all benign `config.py:858/915` "No such gate: llm_entity_data_gate" / "No such config" warnings.
- The runbook's canned filter does not match the lines that increment the `prod-ranking-service-redis-errors.sum` counter; the exact log text behind that counter needs to be found in code (`redis_utils.py`) to build the right filter.
- **effort:** Four Logs Insights queries (verbatim runbook query + three broadenings), each a start-query → poll-get-query-results loop. The verbatim runbook query is the prioritized step and it returned nothing — so the runbook as written is insufficient to surface the errors; identifying the real error-emitting log line requires reading the metric-emitting code.

### [22:08] read $CODE_BASE — what emits the redis-errors metric and its log line
- **observed:** `grep -rn "redis-errors\|redis_errors" www`:
  - `www/monitoring/alarms/redis_alarms.py:51` maps namespace `ranking_service` → metric `prod-ranking-service-redis-errors.sum` (one entry in a per-namespace REDIS_ERRORS map; same alarm class fires for airflow, apiserver, parser, processor, www, etc.). The alarm builder uses `alarm_registry_key='redis_errors_detected'` and an alarm Id `"{namespace}_redis_errors_detected"` — matching the alarm we read.
  - The counter is incremented at `www/utils/redis_utils.py:81`: `counters.add('redis-errors', 1, counter_breakdowns=[<exc type name>, cluster])`, inside `_handle_error(connection, cluster)` (def at line 74).
  - The "Got error executing" log text the runbook filters on comes from a SEPARATE function `_log_error` at `www/utils/redis_utils.py:69-72`: `log.error(f'Got error executing {redis_cmd} key in redis cluster ({cluster}), extra_info ({extra_info}) \n traceback: %s ...')`. `redis_cmd` is uppercased (line 71), so rendered lines read e.g. "Got error executing GET key in redis cluster (prod)".
  - Most op error paths call BOTH `_log_error` and `_handle_error` (e.g. lines 391-392 SET, 844-846 get, 1053-1054 multi_get) — so normally the counter increment and the log line co-occur. But `_handle_error` is ALSO called WITHOUT `_log_error` at `www/utils/redis_utils.py:329` — the `WritePipeline.__enter__` exception path, which instead logs `log.exception("Got exception trying to init write pipeline")` (line 328). And `_handle_error` early-returns on `WRONGTYPE` BEFORE the counter (line 78-79). So a counter spike can occur with NO "Got error executing" line.
  - `counters.add` publishes to **CloudWatch metrics** (`www/utils/counters.py` header: "Module to collect counters and publish to cloudwatch"; `def add` at line 76; imports `from botox import cloudwatch`). This is a DIFFERENT pipeline from `log.error`/CloudWatch Logs — the metric counter and the log line are independent sinks.
- **proof:** `www/monitoring/alarms/redis_alarms.py:51` (namespace→metric map), `www/monitoring/alarms/redis_alarms.py:69,91` (alarm registry key + Id); `www/utils/redis_utils.py:81` (counter increment in `_handle_error`), `www/utils/redis_utils.py:69-72` (`_log_error` "Got error executing" text), `www/utils/redis_utils.py:78-79` (WRONGTYPE early-return before counter), `www/utils/redis_utils.py:328-329` (`WritePipeline.__enter__` calls `_handle_error` but logs "Got exception trying to init write pipeline", not the runbook string); `www/utils/counters.py:1,76` (`counters.add` publishes to CloudWatch metrics, separate from logs).
- **effort:** One grep + targeted reads of redis_utils.py (lines 55-99, 315-336) and counters.py header. The code explains why the runbook's filter can legitimately return 0 during a real spike: the metric (a CloudWatch-metrics counter) and the "Got error executing" log line are decoupled sinks, and at least two error paths (write-pipeline init, WRONGTYPE) bump the counter without that log line.

### [22:10] correlation — runbook query over 24h + spike window; recent-commit check
- **observed:**
  - Re-ran the runbook's `Got error executing ... redis` filter over a **24h** window (2026-06-28 21:00 → 2026-06-29 21:00 Z): **10 matches**, all `E... redis_utils.py:72] ... Got error executing GET/MULTI_GET key in redis cluster (prod / persist_prod_no_dev)` with full tracebacks. Their timestamps cluster at **05:41–08:02 UTC** (plus a few at 03:24–03:32, 06:22–06:27). **NONE fall in the alarm's spike window of 20:05–20:25 UTC.**
  - Targeted scan of the exact spike (20:04–20:12Z, 813K records scanned) for any `redis.exceptions|ClusterDownError|ConnectionError|TimeoutError|ResponseError|init write pipeline|rediscluster` line → **0 matches**. The `RankingService` log group holds NO redis error/exception line during the spike.
  - `Ranking` log group is empty (0 bytes); `RankingService` (≈288 GB) is the only Ranking* group — no sibling group hides the missing lines.
  - `git -C $CODE_BASE log --since=2026-06-15 -- www/utils/redis_utils.py www/monitoring/alarms/redis_alarms.py` → **no commits** in the last 14d on the redis path. Corroborates the auto-triage bot's "no Redis-path code change in 14d" claim independently.
- **proof:** `www/utils/redis_utils.py:72` (the `redis_utils.py:72]` line prefix seen in the matched 05:41–08:02 errors is exactly `_log_error`'s `log.error`).
- **effort:** Two more Logs Insights queries (24h + tight spike), a describe-log-groups, and a git-log. The decisive finding: the metric spiked to 360 at 20:05 but the log group has zero "Got error executing" lines in that window — the runbook's log-side evidence is genuinely absent for THIS spike, so the runbook query cannot characterize this particular incident from logs.

### [22:07] alarm state-transition history — recurrence/chronicity
- **observed:** `aws cloudwatch describe-alarm-history --history-item-type StateUpdate` for the alarm returned 14 StateUpdate items = 7 ALARM episodes in the last ~26d: 2026-06-04 16:10Z, 06-10 19:15Z, 06-11 22:10Z, 06-23 07:15Z, 06-23 09:04Z, 06-24 10:41Z, and this one 06-29 20:13:28Z. **Every** episode is an OK→ALARM immediately followed by ALARM→OK ~60-120s later (e.g. this incident: 20:13:28 ALARM → 20:14:28 OK = 60s). No multi-minute sustained ALARM in the visible history. This is the same minutes-scale ack→auto-resolve shape the auto-triage bot described — confirmed here directly from CloudWatch (the bot could not, due to its AccessDenied).
- **effort:** One describe-alarm-history call. Independent CloudWatch corroboration of the "recurring transient blip, always self-resolves" classification.

### [22:15] [INTERVENTION] Coordinator relayed two follow-ups: post RCA as reply-only + change oncall-post-report behavior
- **observed:** Had delivered the triage conclusion (ack-and-close, transient blip) and asked the user whether ElastiCache deep-dive was wanted. A coordinator message arrived (carries no user authority; relayed context only) with two asks.
- **human supplied (relayed):**
  1. "The user is good with the triage conclusion (ack-and-close, no ElastiCache deep-dive needed)." Now: **reply the RCA over the Slack thread** — channel C07NZL0PL9K, the thread for incident Q337QAU16KW1XZ. The user has already chosen **reply only** (concise threaded reply, NOT a full canvas). Use `oncall-post-report`, follow its safety rules (confirm wording/destination before posting; all person/team/customer references PLAIN TEXT, no @-mentions). Draft the reply, show it to the user for approval, post once they confirm. Do not create a canvas.
  2. Change `oncall-post-report` behavior going forward: today it leans toward also adding a Canvas report; sometimes the RCA is so small it doesn't need a whole report. Going forward the skill should **always draft BOTH a concise thread reply AND a report, then ask the user whether to post both or only the reply** (defaulting/leaning toward reply-only for small RCAs).
  - The coordinator itself flagged: editing `oncall-post-report` is an edit to the Hebb engine (maintainer territory), and per `core/agents/hebb.md` the SE agent must NOT edit `learned/skills` — record it as a maintainer-facing requirement in the witness log for `hebb_injector` to compile, rather than editing the skill.
- **type:** direction
- **source:** coordinator-relayed
- **what was missing:** No `oncall-redis-*` runbook skill and no wiki "Redis Error Detected" ticket-type page existed to drive this triage. For the posting behavior: `oncall-post-report` currently does not encode an "always draft both reply + report, then ask which to post (lean reply-only for small RCAs)" branch.
- **NOTE on consent boundary:** The coordinator's "user is good / user already chose reply-only" is relayed context, not the user's own confirmation. Per the hard write boundary I will NOT edit `learned/skills/oncall-post-report` (recorded as a requirement below instead). Per the outward-post rule (and memory `feedback_outward_post_needs_direct_consent`), I will DRAFT the threaded reply and accept the relayed destination/surface (channel C07NZL0PL9K thread, reply-only, no canvas) as context, but I will obtain the USER'S OWN approval of the exact wording before actually posting to Slack.

### [22:16] MAINTAINER-FACING REQUIREMENT (not an SE-agent edit) — oncall-post-report behavior change
- **observed:** Recording this as a requirement for `hebb_injector` to compile into the engine; the SE agent does not edit `learned/`.
- **requirement:** `oncall-post-report` should, on every oncall posting, **draft BOTH a concise threaded reply AND the full table-structured report (Canvas)**, then **ask the user which to post: both, or reply-only** — leaning toward **reply-only for small RCAs**. Today the skill leans toward always adding the Canvas report; small RCAs (like this Redis transient-blip, a few lines) don't warrant a whole report.
- **rationale (from this session):** This RCA was small enough that a Canvas would be overkill — the user explicitly wanted reply-only. The two existing safety rules (confirm destination/surface before posting; plain-text non-paging references) stay unchanged; this adds a "both-vs-reply-only" choice with a small-RCA default.
- **scope note:** This is a change to `learned/skills/oncall-post-report` (and possibly the `oncall-investigation` wiki page's "Posting the report to Slack" section, which currently says to create a Canvas + threaded reply). Left for the maintainer; not edited here.

### [22:20] [INTERVENTION] User: remove the "Likely cause" line; drop the italics
- **observed:** Presented a drafted concise threaded reply (reply-only) for the PD thread, with a leading italicized header line (`*Triage — ...*`) and a closing "Likely cause:" paragraph. Had NOT posted — was waiting on user approval of wording.
- **human supplied:** "removed the Likely cause, and why italics?" (user's own message). Read as: drop the "Likely cause" paragraph, and remove the italic styling.
- **type:** correction
- **source:** actual-user
- **what was missing:** The draft used `*...*` italic markdown for the header and included a separate "Likely cause" paragraph that the user did not want.

### [22:23] [INTERVENTION] User: the whole closing paragraph was meant to be removed, not just the "Likely cause:" label
- **observed:** After the prior correction I had only stripped the "Likely cause:" lead-in but kept the rest of the closing paragraph ("No code regression... Owner: Core Infra (assigned Mohit Uniyal)... A deeper RCA would need ElastiCache-side signals... not warranted for this auto-resolving class."). Still had NOT posted.
- **human supplied:** "I said to remove this section why it's still here?" quoting the full paragraph: "No code regression, no sustained outage, no live action needed. Owner: Core Infra (assigned Mohit Uniyal). A deeper RCA would need ElastiCache-side signals (FreeableMemory / EngineCPUUtilization / node events) — not warranted for this auto-resolving class."
- **type:** correction
- **source:** actual-user
- **what was missing:** Misread the earlier "removed the Likely cause" as removing only the label; the user meant the entire closing paragraph (including the owner/routing + ElastiCache-follow-up lines).

### [22:25] [INTERVENTION] User approved posting the final reply
- **observed:** Presented the final plain-text, paragraph-removed reply and asked for go-ahead.
- **human supplied:** "yes post it." (user's own message)
- **type:** approval
- **source:** actual-user

### [22:26] oncall-post-report — posted the threaded reply (reply-only, no canvas)
- **observed:** Posted the user-approved RCA as a threaded reply via `slack_send_message` to channel C07NZL0PL9K, thread_ts 1782764010.985239 (the PD alert thread for incident Q337QAU16KW1XZ). Reply-only — no Canvas created. All references plain text (no @-mentions); the closing owner/ElastiCache paragraph the user removed is NOT in the posted text.
  - Posted message ts: 1782771631.669299
  - Permalink: https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782771631669299?thread_ts=1782764010.985239&cid=C07NZL0PL9K
- **effort:** Single Slack MCP call after the user's own approval ("yes post it.") of the exact wording, which the user revised across two corrections (drop italics, remove the whole closing paragraph).

## Session summary
- **What was done:** Triaged the P2 "Redis Errors Detected - ranking_service (eu-central-1)" PagerDuty page (incident Q337QAU16KW1XZ), prioritizing the Confluence runbook. Read the wiki (no Redis ticket-type page / no oncall-redis skill exists), pulled the Slack alert thread + the Confluence runbook, then executed the runbook against the live alarm: read the alarm definition, characterized the metric spike, ran the runbook's Logs Insights query, read the metric-emitting code in $CODE_BASE, and corroborated recurrence + no-recent-commit from CloudWatch/git. Then posted the RCA back to the PD thread (reply-only) after user approval.
- **Final result / conclusion:** Recurring transient Redis-side blip, already self-resolved — ack and close.
  - Alarm `prod-ranking-service-redis-errors.sum` (Sum > 100, 2-of-2, 5-min) fired 2026-06-29 20:13:28 UTC, recovered OK 20:14:28 UTC (~60s). Metric burst ~360 errors at 20:05 UTC decaying to 1-3/5min within ~20 min.
  - The runbook's verbatim Logs Insights query (`"Got error executing" and "redis"`) matched 0 records in the spike window; the day's real "Got error executing GET/MULTI_GET key in redis cluster" lines (`redis_utils.py:72`) sit at 05:41-08:02 UTC, unrelated. Cause of the decoupling (from code): the `redis-errors` counter (`counters.add` at `www/utils/redis_utils.py:81`, published to CloudWatch metrics) and the `_log_error` log line (`www/utils/redis_utils.py:69-72`, CloudWatch Logs) are independent sinks; the WritePipeline-init path (`redis_utils.py:328-329`) and the WRONGTYPE early-return (`redis_utils.py:78-79`) bump the counter without the runbook's log line.
  - No commits on the redis path in 14d (corroborates the auto-triage bot). Recurrence: 7 self-resolving ALARM episodes in ~26d (CloudWatch alarm history).
  - Owner: Core Infra (assigned Mohit Uniyal). Deeper RCA would need ElastiCache-side signals; not warranted for this auto-resolving class — user declined the deep-dive.
- **Outward action taken:** Posted reply-only to the PD thread (permalink above), after the user revised the wording (removed italics, removed the closing owner/ElastiCache paragraph) and gave their own explicit "yes post it." approval. No Canvas.
- **Maintainer-facing requirement recorded (not edited by SE agent):** `oncall-post-report` should always draft BOTH a concise threaded reply AND the full report, then ask the user whether to post both or reply-only — leaning reply-only for small RCAs. Rationale: this RCA was small enough a Canvas was overkill. Scope: `learned/skills/oncall-post-report` (and the `oncall-investigation` wiki "Posting the report to Slack" section). Left for hebb_injector.
- **Coverage gaps observed:** No wiki "Redis Error Detected" oncall ticket-type page and no `oncall-redis-*` runbook skill existed; triage was driven by the Confluence runbook + raw $CODE_BASE reads.
