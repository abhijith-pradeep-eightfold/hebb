---
task: Debug a "Queue backed up-batch_requests (us-west-2)" PagerDuty page from a Slack link
date: 2026-06-26
skills_used:
  - {name: external-context-puller, note: pulled the PagerDuty Slack alert via Slack MCP}
  - {name: oncall-queue-backed-up, note: loaded the high-level queue-backed-up runbook}
  - {name: query-processor-event-log, note: count-by operation0×group_id for batch_requests; narrowed UTC vs IST windows to pin t_create tz}
  - {name: query-starrocks, note: live MAX(t_create) vs warehouse NOW() to verify t_create is UTC on both processor_event_log and search_query_log}
interventions: 6
---

# Queue backed up — batch_requests (us-west-2)

**Task:** Debug the PagerDuty page linked from Slack: `[us-west-2] Queue backed up-batch_requests (us-west-2)` (Core Infra service).

## Log

### [20:19] external-context-puller — pull the PagerDuty Slack alert
- **observed:** Read the Slack thread `C07NZL0PL9K/p1782460626004789` via Slack MCP. Thread parent is a PagerDuty bot message, no replies. Content:
  - Title: `:large_green_circle: [us-west-2] Queue backed up-batch_requests (us-west-2)` linking incident `Q1POBEGIUPIWKJ`.
  - Incident type: Base Incident; Urgency: High; Service: Core Infra (`P7I5DOG`).
  - Triggered via CloudWatch alarm `[us-west-2] Queue backed up-batch_requests (us-west-2)` in region us-west-2.
  - Alert fired 2026-06-26 13:27:06 IST (= 2026-06-26 07:57:06 UTC).
  - Green circle indicates the incident is already resolved at read time.
- Extracted parameters for the investigation: queue = `batch_requests`, region = `us-west-2`.

### [20:19] oncall-queue-backed-up — load runbook
- **observed:** Loaded the queue-backed-up runbook. Sequence: inspect-cloudwatch-metric (confirm/characterize spike) → query-processor-event-log (op×group `message_dispatched` breakdown over the window) → trace-processor-op (culprit SMID → root op) → codeowners-owner (route root/culprit op to owner).

### [20:20] inspect-cloudwatch-metric — confirm & characterize the queue-depth spike
- **observed:** Ran the bundled `pull_queue_depth.py` for queue `batch_requests`, region `us-west-2`, window 2026-06-26 04:00–12:00 UTC. Alarm threshold = 50000 (`AWS/SQS ApproximateNumberOfMessagesVisible`, metric-math alarm). Curve (Maximum per 900s bucket):
  - 04:00–06:00 UTC: baseline ~1.8k–7.4k.
  - 06:00 ~5k → 06:30–06:45 ~15k (ramp begins).
  - **07:00 crosses threshold: max ~65.9k.**
  - **07:15 peak: max 77,365 (155% of threshold).**
  - Breach sustained 07:00–07:45 (four consecutive 900s buckets ≥ 50k — matches the alarm's `datapoints_to_alarm=4`; consistent with the page firing at 07:57 UTC / 13:27 IST).
  - 08:00 ~6.6k, drains to ~467 by 08:45; back to baseline after.
  - Shape: sudden ramp + sharp drain (a fan-out burst), not a gradual creep. True spike window ≈ 06:15–08:30 UTC.
- **effort:** Single bundled skill call; no manual AWS calls or $CODE_BASE exploration needed. The alarm config + metric-math shape are already compiled in the wiki/skill.
- **script:** bundled skill script (not scratch):
  ```bash
  PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/inspect-cloudwatch-metric/scripts/pull_queue_depth.py" \
    --queue batch_requests --region us-west-2 \
    --start 2026-06-26T04:00:00Z --end 2026-06-26T12:00:00Z
  ```

### [20:20] [INTERVENTION] region is eu-central-1, not us-west-2
- **observed:** I had taken the region from the PagerDuty alert text/console link, which read `[us-west-2] Queue backed up-batch_requests (us-west-2)` and `region=us-west-2`, and ran step 1 against us-west-2 (which returned a real spike). The user corrected the region.
- **human supplied:** "here the region is eu-cental-1 not us-west-2"
- **type:** correction
- **source:** actual-user
- **what was missing:** the investigation took the region verbatim from the alarm name/console link; the actual region to investigate (eu-central-1) differs from the one the page names (us-west-2).

### [20:20] inspect-cloudwatch-metric — re-confirm spike in eu-central-1 (corrected region)
- **observed:** Re-ran `pull_queue_depth.py` for queue `batch_requests`, region `eu-central-1`, same 04:00–12:00 UTC window. Threshold = 50000. Curve (Maximum per 900s bucket):
  - 04:00–06:30: baseline rising ~3k → ~16k.
  - **06:45 crosses threshold: max 74,460 (149% of threshold), avg 52,937 — the peak.**
  - Breach sustained 06:45–07:30 UTC (four consecutive buckets ≥ 50k — matches `datapoints_to_alarm=4`).
  - Drains: 07:45 ~34k, 08:00 ~24k, plateau ~17–21k through ~09:15, declining to ~1–3k by 11:00.
  - Shape: sharp ramp + burst, then a slow drain (longer tail than the us-west-2 curve). Page fired 07:57 UTC (13:27 IST), just after the breach window.
  - Both regions (us-west-2 from the alert text, eu-central-1 per the user) showed a spike around the same time; investigation now proceeds against eu-central-1.
- **effort:** Single bundled skill call against the corrected region.
- **script:** bundled skill script (not scratch):
  ```bash
  PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/inspect-cloudwatch-metric/scripts/pull_queue_depth.py" \
    --queue batch_requests --region eu-central-1 \
    --start 2026-06-26T04:00:00Z --end 2026-06-26T12:00:00Z
  ```

### [20:31] [INTERVENTION] region is us-west-2 after all — prior eu-central-1 correction retracted
- **observed:** I had just loaded the `query-processor-event-log` skill to run the op×group `message_dispatched` breakdown against eu-central-1 (the region from the previous [20:20] correction). The user retracted that correction: the region is us-west-2, the value the PagerDuty alert named originally.
- **human supplied:** "the region is us-west-2 only. Told you I made a mistake"
- **type:** correction
- **source:** actual-user
- **what was missing:** the region under investigation flipped twice (alert text us-west-2 → user correction eu-central-1 → user retraction back to us-west-2); the authoritative region was not settled before the metric step ran, so step 1 was executed against both regions before the correct one (us-west-2) was confirmed.

### [20:34] query-processor-event-log — message_dispatched breakdown for batch_requests (op×group), and timezone pinning
- **observed:** Ran the bundled count-by reader (region resolves to us-west-2 via env `AWS_DEFAULT_REGION`/`EF_DEFAULT_REGION=us-west-2`; the script takes no `--region` flag — `dwh.get_db_type_override(REDSHIFT_LOG)` routes to the region warehouse, here StarRocks `log.processor_event_log`).
  - **Wide window 2026-06-26 06:00–14:30** (deliberately spanning both UTC and IST interpretations of the 07:00–07:45 UTC CloudWatch breach): top groups were `index` spread across many tenants — mercadolibre.com 46,891 / appliedmaterials.com 43,358 / lockheedmartin.com 30,005 / tailoredbrands.com 21,786 / jhu.edu 20,895 / … . Broad `index` traffic; no single-tenant 95% fan-out like the prior witnessed incident.
  - **Narrowed to pin the timezone.** Ran the same count-by over the two candidate windows for the *same real-world* breach:
    - **UTC hypothesis (t_create 06:30–08:00):** `index`/lockheedmartin.com **30,000**, `index`/tailoredbrands.com **21,609**, index/libertymutual.com 8,636, index/ngc.com 8,131, index/mercadolibre.com 7,499, …
    - **IST hypothesis (t_create 12:00–13:30):** `index`/mercadolibre.com 11,589, index/appliedmaterials.com 6,527, index/netapp.com 6,264, … (lower, spread-out).
  - **Finding:** lockheedmartin.com (30,005 over the whole day) and tailoredbrands.com (21,786) dispatched essentially their *entire* daily `index` volume inside **06:30–08:00 t_create** — i.e. concentrated exactly on the CloudWatch breach window **07:00–07:45 UTC**. The burst aligns with t_create read as **UTC**, not IST. ⇒ `processor_event_log.t_create` is **UTC**.
  - Culprit shape (under UTC): the spike is an `index` burst dominated by **lockheedmartin.com (~30k)** and **tailoredbrands.com (~21.6k)**, both on `operation0=index`.
- **effort:** Three bundled count-by calls (one wide + two narrow). The wide window initially obscured the burst under high-volume baseline `index` traffic (mercadolibre etc.); narrowing to the two timezone-candidate windows separated burst from baseline and pinned the clock. The wiki's IST framing for these log tables sent me to test an IST window that did not contain the burst.
- **script:** bundled skill runner (not scratch), e.g.:
  ```bash
  PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/query-processor-event-log/scripts/query_processor_event_log.py" \
    --queue batch_requests --event-type message_dispatched \
    --since "2026-06-26 06:30:00" --until "2026-06-26 08:00:00" \
    --count-by operation0,group_id --limit 15
  ```

### [20:37] [INTERVENTION] the IST assumption is wrong — t_create is UTC; correct it for search_query_log too
- **observed:** I had just used the UTC-vs-IST window comparison to pin `processor_event_log.t_create`, leaning on the compiled wiki ([[process/incident-metric-correlation]]) which frames these warehouse log tables' `t_create` as **IST** and cautions to convert UTC↔IST. The burst-alignment evidence showed the column is actually UTC. The user stated the IST idea is wrong and that the same fix must extend to `search_query_log`.
- **human supplied:** "IST idea is wrong, we should fix it for search_query_log also. UPDATE SEARCH_QUERY_LOG also."
- **type:** correction
- **source:** actual-user
- **what was missing:** the compiled wiki asserts these StarRocks `log.*` tables store `t_create` in IST — the [[process/incident-metric-correlation]] page (UTC/IST caution) and the [[data-warehouse/search-query-log]] page. The observed `processor_event_log` burst alignment contradicts that (it is UTC), and the user states the `search_query_log` page carries the same incorrect IST assertion. (The IST claim is wiki content under `learned/wiki/`, outside the SE agent's `inputs/`-only write boundary; recorded here for the maintainer.)

### [20:40] query-starrocks — verify t_create timezone on both log tables (validate the correction)
- **observed:** Ran a sanity query capturing the warehouse clock `NOW()` and `MAX(t_create)` (plus a 1-day `MIN`/`COUNT` to confirm live data) for both `log.search_query_log` and `log.processor_event_log`. Results:
  - `db_now` = **2026-06-26 20:40:15** for both rows → matches the real UTC wall clock at run time (20:40 UTC) → the warehouse server clock itself is UTC.
  - `processor_event_log` `MAX(t_create)` = **2026-06-26 20:39:40** (~35s behind `db_now`).
  - `search_query_log` `MAX(t_create)` = **2026-06-26 20:38:10** (~2m behind `db_now`).
  - Both tables' latest `t_create` sit within seconds/minutes of real UTC now (20:40), **not** ~5.5h ahead. An IST-stored column would read ~`2026-06-27 02:1x` (5.5h ahead + date rollover); neither does. ⇒ **`t_create` is stored in UTC on both `processor_event_log` and `search_query_log`.**
  - This independently confirms the [20:34] `processor_event_log` burst-alignment finding (UTC) and extends the same conclusion to `search_query_log` — contradicting the wiki's IST assertion for both. The check is lag-tolerant: ingest lag here is only seconds/minutes, far smaller than the 5.5h UTC↔IST gap.
- **effort:** Single bundled `query-starrocks` call. The live-`MAX(t_create)`-vs-`NOW()` comparison is decisive on its own (no dependence on the incident burst), and applies uniformly to any `log.*` table — it's how the timezone of such a column can be settled directly rather than inferred from an incident.
- **script:** scratch SQL (full inline) + invocation:
  ```sql
  -- Pin the timezone of t_create on the StarRocks log.* tables.
  -- Ground truth: real-world "now" at run time is 2026-06-26 20:37 UTC (= 2026-06-27 02:07 IST).
  -- For a continuously-written log table, MAX(t_create) ~ a few min/hours (ingest lag) behind now.
  --   If t_create is UTC -> max_t_create reads ~2026-06-26 ~18:0x-20:3x.
  --   If t_create is IST -> max_t_create reads ~2026-06-27 ~00:0x-02:0x (5.5h ahead + date rollover).
  -- db_now (warehouse NOW()) reveals the server clock's own tz for reference.
  SELECT 'search_query_log'    AS tbl, NOW() AS db_now,
         MAX(t_create)         AS max_t_create,
         MIN(t_create)         AS min_t_create_1d,
         COUNT(*)              AS rows_1d
  FROM log.search_query_log
  WHERE t_create >= DATE_SUB(NOW(), INTERVAL 1 DAY)
  UNION ALL
  SELECT 'processor_event_log' AS tbl, NOW() AS db_now,
         MAX(t_create)         AS max_t_create,
         MIN(t_create)         AS min_t_create_1d,
         COUNT(*)              AS rows_1d
  FROM log.processor_event_log
  WHERE t_create >= DATE_SUB(NOW(), INTERVAL 1 DAY)
  ```
  ```bash
  PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/query-starrocks/scripts/query_starrocks.py" \
    /path/to/pin_tz.sql
  ```
  Output (verbatim):
  ```
  tbl                  db_now               max_t_create                min_t_create_1d             rows_1d
  processor_event_log  2026-06-26 20:40:15  2026-06-26 20:39:40.638330  2026-06-25 20:40:15.000621  105607440
  search_query_log     2026-06-26 20:40:15  2026-06-26 20:38:10.590278  2026-06-25 20:40:15.000060  35086222
  ```

### [20:41] locate the IST claim across the wiki + note the prior-session origin
- **observed:** Grepped `learned/wiki/` for the IST/timezone claim. It appears on (read-only; for the maintainer):
  - `data-warehouse/search-query-log.md:58` — the core assertion: "`t_create` is stored in IST (local time), not UTC. Confirmed by a warehouse-`NOW()` sanity row that read `2026-06-24 17:17`, matching IST wall-clock … not UTC (~11:48)."
  - `process/incident-metric-correlation.md:24,26` — restates IST for `search_query_log` and the "+5:30 to IST / −5:30 to UTC" shift; sourced (line 36) from `inputs/2026-06-24-solr-cpu-spike-debug.md`.
  - `infra/cloudwatch-cpu-alarm.md:75,82` — "`log.search_query_log` … whose `t_create` is stored in IST, not UTC."
  - `index.md:39` — index blurb repeats "`t_create` IST."
  - `processor/processor-event-log.md:33` — already **hedged**: "Timezone is unconfirmed for this table … One incident *inferred* UTC … the IST claim on the incident metric-correlation page is for `search_query_log`, a different table." (No outright IST assertion for `processor_event_log`.)
  - All IST assertions trace to one origin: `inputs/2026-06-24-solr-cpu-spike-debug.md`'s `[17:18]` sanity row, which read `NOW()` = 17:17 and concluded the column is IST.
- **observed (mechanism):** Today's run had warehouse `NOW()` = `2026-06-26 20:40:15`, equal to real-world UTC at run time — i.e. *this* session's `NOW()` returned UTC, whereas the 06-24 doc reports `NOW()` = 17:17 (IST). `NOW()` returns time in the **read session's `time_zone`**, which differs between the two sessions; it does not, by itself, establish how the `t_create` literals were written. The storage tz is settled instead by comparing `MAX(t_create)` to an *independently-known* real-world UTC instant — done at [20:40]: `MAX(t_create)` ≈ real UTC now on both tables (within seconds/minutes), so both store `t_create` in **UTC**. A continuously-written table (rows_1d = 105.6M / 35.1M) cannot have its latest `t_create` be 5.5h stale, which rules out the IST reading.
- **effort:** One read-only grep over the compiled wiki plus the [20:40] data. No `$CODE_BASE` exploration. The conflicting prior conclusion came from a `NOW()`-only sanity row; reconciling it required the `MAX(t_create)`-vs-real-UTC comparison, which is the tz check that does not depend on the read session's clock.

### [20:43] query-processor-event-log — pull representative culprit SMIDs (index / lockheedmartin.com)
- **observed:** A tight 07:00–07:45 UTC window with `--operation index --group-id lockheedmartin.com` returned **0 rows** — so the lockheedmartin `index` burst is in the earlier part of the breakdown window (~06:30–07:00), dispatched *before* the depth peak (07:00–07:45). Pulled raw rows over 06:00–08:00: the newest `message_dispatched` rows are at `t_create` **2026-06-26 06:58:55** (dispatch precedes peak depth — consistent with producer-outpaces-consumer). Representative culprit SMID `beb5cbad-e5ac-45b4-b3ea-c6a52d3d1f5c`. Many `index` messages share a `processor_parent_msg_id` (`d80542bf-740c-47dd-a6d3-69a615b39a96`, `7c73100a-5077-46e7-ac13-42a499084cae`) and a `request_trace_id` per parent → a fan-out from a parent op.
- **effort:** Minor — one tight window (empty) then one raw pull over the wider window.
- **script:** bundled runner:
  ```bash
  PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/query-processor-event-log/scripts/query_processor_event_log.py" \
    --queue batch_requests --event-type message_dispatched \
    --operation index --group-id lockheedmartin.com \
    --since "2026-06-26 06:00:00" --until "2026-06-26 08:00:00" --limit 5 --format json
  ```

### [20:45] trace-processor-op — walk culprit SMID to root op
- **observed:** Traced SMID `beb5cbad` (`index`, `batch_requests`, 06:58:55) up the `processor_parent_msg_id` chain (all hops `group_id=lockheedmartin.com`, `status=PASS`):
  - depth 1: `employee_role_association_batch` [`d80542bf`, queue `role_association_update_requests`, `t_create` 06:39:44]
  - depth 2 (**root**, `parent=None`): `employee_role_association_manager` [`ed7c87f4`, queue `role_association_update_requests`, `t_create` 06:31:34]
  - Op trace root→target: `employee_role_association_manager` → `employee_role_association_batch` → `index`.
  - The root op (`manager`) fanned out to `batch`, which dispatched the `index` flood onto `batch_requests`. Timeline (UTC): manager 06:31 → batch 06:39 → index 06:58, consistent with the 07:00–07:45 depth peak. Not a high-mem-reroute shape (distinct ops, no `REROUTE_TO_HIGH_MEM`).
- **effort:** Single bundled tracer call; 3 shallow hops.
- **script:** bundled runner:
  ```bash
  PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/trace-processor-op/scripts/trace_processor_op.py" \
    beb5cbad-e5ac-45b4-b3ea-c6a52d3d1f5c
  ```

### [20:46] codeowners-owner — route root + culprit ops to owners
- **observed:** op_registry maps the three ops to source files, and the CODEOWNERS resolver (last-match-wins) gives owners:
  - `index` → `processor.index_operation` → `www/processor/index_operation.py` → **@EightfoldAI/core-search @EightfoldAI/dp-data-flow** (CODEOWNERS L255)
  - `employee_role_association_batch` → `processor.employee_role_association_batch_operation` → `www/processor/employee_role_association_batch_operation.py` → **hpatel@eightfold.ai** (L182)
  - `employee_role_association_manager` → `processor.employee_role_association_manager_operation` → `www/processor/employee_role_association_manager_operation.py` → **hpatel@eightfold.ai** (L181)
  - All three files matched an explicit CODEOWNERS rule; no git-author fallback needed.
- **proof:** `www/processor/op_registry.py:17` (index), `:142` (batch), `:143` (manager); CODEOWNERS `:181`, `:182`, `:255` (resolver output).
- **effort:** One `op_registry` grep + one resolver call.
- **script:**
  ```bash
  rg -n --no-heading -S "'employee_role_association_manager'|'employee_role_association_batch'|'index'" "$CODE_BASE/www/processor/op_registry.py"
  PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" \
    "/home/ec2-user/hebb/.claude/skills/codeowners-owner/scripts/codeowners_for.py" \
    www/processor/employee_role_association_manager_operation.py \
    www/processor/employee_role_association_batch_operation.py \
    www/processor/index_operation.py
  ```

### [20:51] [INTERVENTION] present oncall ticket findings as a detailed report with tables
- **observed:** I had delivered the queue investigation result as a prose summary with a couple of inline tables. The user directed that oncall tickets be reported as a detailed report with tables.
- **human supplied:** "for oncall tickets give me detailed report with tables"
- **type:** direction
- **source:** actual-user
- **what was missing:** the oncall investigation output format was a prose summary; the user wants a standardized detailed, table-structured report for oncall tickets (alarm, spike characterization, driver breakdown, lineage, ownership/routing, timeline).

### [20:55] [INTERVENTION] post the report in Slack — destination confirmed as Canvas in the PD thread
- **observed:** User asked to "post the report in slack". Posting is outward-facing and the report names an individual (hpatel@eightfold.ai) and customers (lockheedmartin.com, tailoredbrands.com), and the destination channel/surface was unspecified, so I asked for confirmation (AskUserQuestion) offering: Canvas in the PD thread / Markdown message in the PD thread / new channel message. User selected **Canvas in the PD thread**.
- **human supplied:** "post the report in slack" → (on confirmation) chose "Canvas in PD thread".
- **type:** direction
- **source:** actual-user
- **what was missing:** the post destination + surface were unspecified by the initial instruction; required a confirmation round before an outward-facing post.

### [20:55] post report to Slack — create Canvas + reply in PD alert thread
- **observed:** Created a Slack Canvas with the full table-structured report (`slack_create_canvas`) → `canvas_id=F0BD2KB2FLP`, url `https://eightfoldai.slack.com/docs/T1UL59A9M/F0BD2KB2FLP`. Posted a concise summary (root cause, route-to, canvas link) as a threaded reply in the PagerDuty alert thread (`slack_send_message`, channel `C07NZL0PL9K`, `thread_ts=1782460626.004789`) → message `https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782507515834299?thread_ts=1782460626.004789`. Owner references (hpatel@eightfold.ai, the two team handles) were left as plain text — not Slack @-mentions — so no one was paged by the post.
- **effort:** Two Slack MCP calls after one confirmation round; the report content was already assembled.

### [20:51] produce detailed table-structured oncall report
- **observed:** Assembled and delivered the investigation as a detailed report with tables (per the `[20:51]` direction): incident header, alarm definition, spike characterization (CloudWatch UTC buckets), driver breakdown (`operation0 × group_id` with a burst-vs-baseline concentration column comparing the 06:30–08:00 window to full-day volume), root-cause lineage (op/SMID/queue/t_create/status per hop), ownership/routing (op → file → owner), UTC timeline, findings/recommendation, and an investigation-caveats table. Concentration insight surfaced: lockheedmartin.com (~100%) and tailoredbrands.com (~99%) dumped ~their entire daily `index` volume into the burst window, whereas mercadolibre/appliedmaterials are high-baseline tenants (16–17% in-window) — not the cause.
- **effort:** Composition only from data already gathered; no new queries.

### [21:14] [INTERVENTION] exclude the eu-central-1 region detour from injection/wiki
- **observed:** The user manually invoked the maintainer (`@hebb_injector inputs/2026-06-26-queue-backed-up-batch-requests.md`) and directed that the eu-central-1 region detour — the user's own mistake at `[20:20]`, retracted at `[20:31]` — be excluded from the compilation. I launched the `hebb_injector` agent and relayed the constraint: treat region as us-west-2 throughout, derive no "settle the region first" lesson from the `[20:20]`/`[20:31]` flip, and do not surface the eu-central-1 spike in any compiled artifact; compile the timezone (IST→UTC) correction normally.
- **human supplied:** "In this session I made some mistake of saying the region was eu-cental-1, I later corrected it, dont include it in the injection or wiki."
- **type:** direction
- **source:** actual-user
- **what was missing:** the witnessed history in this doc contains a user self-correction (the eu-central-1 region flip) that the user does not want compiled; the injector needed an explicit instruction to exclude session-specific self-correction noise from the durable artifacts.

## Session summary

**What was done (region = us-west-2 throughout; the eu-central-1 steps at `[20:20]`/`[20:31]` were a user self-correction the user later asked to exclude from compilation):**
1. Pulled the PagerDuty "Queue backed up-batch_requests (us-west-2)" alert from Slack (`external-context-puller`); loaded the `oncall-queue-backed-up` runbook.
2. Confirmed & characterized the SQS queue-depth spike via `inspect-cloudwatch-metric`: breach sustained **07:00–07:45 UTC**, peak **77,365** (155% of the 50k threshold), fan-out shape, self-recovered by ~08:45.
3. Broke `message_dispatched` down by `operation0 × group_id` (`query-processor-event-log` count-by). While narrowing the window, hit the timezone question and **pinned `t_create` to UTC**: (a) the burst (lockheedmartin.com ~30k, tailoredbrands.com ~21.6k on `index`) aligned with `t_create` 06:30–08:00 = the UTC breach window; (b) `query-starrocks` live `MAX(t_create)` ≈ warehouse `NOW()` ≈ real UTC now on both `processor_event_log` and `search_query_log`. This contradicts the wiki's IST claim — confirmed by the user's correction and extended to `search_query_log`.
4. Traced a representative culprit SMID to its root op (`trace-processor-op`): root **`employee_role_association_manager`** → `employee_role_association_batch` → **`index`** flood onto `batch_requests`, all `group_id=lockheedmartin.com`.
5. Routed via `codeowners-owner`: root/fan-out ops → **hpatel@eightfold.ai**; `index` op → **@EightfoldAI/core-search @EightfoldAI/dp-data-flow**.
6. Delivered a detailed table-structured oncall report (per user direction), then posted it to Slack as a **Canvas** (`F0BD2KB2FLP`) with a concise threaded reply in the PD alert thread (owner refs left as plain text, not @-mentions).

**Final result:** Root cause = a role-association run (manager → batch) for lockheedmartin.com (+ a parallel tailoredbrands.com run) fanned out ~51.6k `index` ops onto `batch_requests` faster than consumers could drain; routed to hpatel@eightfold.ai (cc core-search / dp-data-flow). Report posted to Slack.

**Validated alternatives / corrections within the task:**
- Region: alert text us-west-2 → user said eu-central-1 (`[20:20]`) → user retracted to us-west-2 (`[20:31]`); final = us-west-2 (user asked to exclude the detour from compilation).
- Timezone: the wiki's IST framing for `t_create` was tested (an IST-hypothesis window did not contain the burst) and overturned — verified UTC two independent ways for `processor_event_log`, and via live `MAX(t_create)` for `search_query_log`.

**Interventions:** 6 (2 region flip-flops [excluded from compilation per user], 1 timezone correction, 1 report-format direction, 1 Slack-post direction, 1 injection-exclusion direction).

**Doc status:** the user has manually invoked `@hebb_injector` on this doc; the maintainer is compiling the timezone fix and the oncall report/Slack-post capabilities while excluding the eu-central-1 detour.
