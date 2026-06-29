---
task: Triage the "[eu-central-1] [P2] Alarm Provisioning Failures" oncall page — understand the alarm, follow the runbook against $CODE_BASE/telemetry, find the driver, route to an owner.
date: 2026-06-29
skills_used:
  - {name: external-context-puller, note: pulled the PD/Slack thread and the Confluence runbook for this alarm type}
  - {name: wiki-reader, note: read index + oncall umbrella; no ticket-type page for Alarm Provisioning Failures}
  - {name: inspect-cloudwatch-metric, note: used its describe-alarms + get-metric-statistics + pull_alarm_history.py on a custom airflow-namespace metric (not CPU/queue); generalized cleanly}
  - {name: codeowners-owner, note: resolved /www/monitoring/ CODEOWNERS rule + git-authorship fallback for the excess_log_volume alarm file}
interventions: 8
---

# Oncall triage — [eu-central-1] [P2] Alarm Provisioning Failures

**Task:** Triage a new oncall alarm type ("Provisioning Failures") not yet covered by the existing oncall wiki/skills (which cover "Queue backed up" and "Solr CPU too high"). Pull the Slack thread + Confluence runbook, then follow the runbook against the codebase/telemetry to characterize what happened, find the driver, and route to an owner.

## Log

### [18:58] external-context-puller
- **observed:** Pulled two external references.
  - **Slack thread** (channel C07NZL0PL9K = `core_infra-oncall`, parent ts 1782757634.615119): PagerDuty page `[eu-central-1] [P2] Alarm Provisioning Failures`, incident Q0FJP3UKZX0N4G, Service "Core Infra", urgency High, assigned Mohit Uniyal, triggered 2026-06-29 23:57 IST via CloudWatch alarm in eu-central-1. One reply: a "DP On-Call Auto-Triage (v2)" bot post (session e5d556fefa6a, 397s). Key claims from the bot:
    - Alarm fires off the `alarm_manager_alerts` DAG (daily, `create` mode); raises on >=1 `alarm_registry_key` failing while provisioning CloudWatch alarms. Each failing alarm bumps `airflow-alarm_provisioning_failures.sum` and emails its owner. So **N datapoints = N independent alarm-config bugs**, not one root cause.
    - Cited driver code: `/src/www/monitoring/alarm_manager.py:962-976`.
    - Today's failing-alarm list was NOT retrievable from the triage box: its `prod-search` IAM user lacks `cloudwatch:Describe*` and `logs:FilterLogEvents`/`StartQuery` in eu-central-1 and us-west-2. A human with CW Logs access must read the `Airflow` log group in eu-central-1 to enumerate failures.
    - History: 515 firings of this class in core_infra-oncall over 1yr; 49 in eu-central-1 across 30 distinct days (~1-2/day in active clusters; last in scrape 2026-05-10). Last 3 prior eu-central-1 threads (05-04 / 05-07 / 05-09) all ack'd by Mohit/Divyendra and auto-resolved 2.7-3.0h later by Automation User, no in-thread RCA. Standing pattern = ack + wait for next daily run.
    - Known recurring offenders (per canonical 2025-09-09 thread + ENG-160690): `data_audit_consistency` (>10 metrics-per-type vs CW 10-metric cap; ENG-160690), `ats_quality_metrics` (PR #90771), `sftp_connection_error_rate` gov (PR #89076), `azure_queue_backup`/`sqs_queue_no_messages_processed` westus2 (partition gap).
    - Recurrence verdict: INTERMITTENT.
  - **Confluence runbook** (pageId 3246915597, space EP, "Alarm Provisioning Failures", P2, last modified Sept 15 2025, author Sangeet Chourasia). Prescribes:
    1. Number of provisioning failures == number of datapoints reported.
    2. Identify failures two ways: (a) Email notifications, subject `[Action Needed] Alarm provisioning <alarm name> is failing in <region>`; (b) DAG logs — fetch logs for dag `alarm_manager_alerts` from Airflow, search logs for the string `[Action Needed] Alarm` to find failures and tracebacks.
    3. Once failures are found, report each and tag the respective alarm owners to raise fixes.
- **effort:** Two MCP fetches (Slack read_thread + Confluence getConfluencePage), both succeeded on first try after loading the deferred tool schemas.

### [18:59] wiki-reader
- **observed:** Read `learned/wiki/index.md` and the oncall umbrella `learned/wiki/oncall/oncall-investigation.md`. The wiki has an **Oncall** section with an umbrella page and exactly two ticket-type pages: `queue-backed-up` (SQS depth) and `solr-cpu-high` (EC2 host CPU). **No wiki page covers "Alarm Provisioning Failures"** — this alarm family is not yet compiled. The umbrella page documents the shared discipline (read alarm → characterize metric → find driver → trace & route → report) and a table-structured report format, plus the `oncall-post-report` skill (confirm destination, plain-text non-paging references). The `inspect-cloudwatch-metric` skill can pull an alarm definition and its **state-transition history** (DescribeAlarmHistory) to answer "chronic vs rare". The `codeowners-owner` skill resolves file/op ownership.
- **effort:** Single wiki read (index + one umbrella page). Confirms this is a new ticket type with no compiled coverage; only the generic discipline transfers.

### [19:00] read driver code in $CODE_BASE (alarm_manager.py + alarm class)
- **observed:** Read the bot-cited driver and the alarm class; the bot's mechanism claims check out exactly.
  - `main()` iterates every alarm key in `ALARM_REGISTRY` and calls `manage_alarms(key, mode, ...)`. Two distinct failure sites both bump the counter `alarm_provisioning_failures`:
    - **Per-alarm-param failure** inside `manage_alarms` (a single `create_alarms`/`delete_alarms` raised): caught, `counters.add('alarm_provisioning_failures')`, `log.error('Failed to manage alarm params ... for <key>: <ex>')`. No email here.
    - **Per-alarm-key failure** in `main()` (the whole `manage_alarms` call raised): `counters.add('alarm_provisioning_failures')`, then builds `subject = "[Action Needed] Alarm {key} provisioning is failing in region {region}"`, `body` includes `Exception: <ex>, traceback: <traceback.format_exc()>`, and emails `get_owner_emails(key)` from `alerts@volkscience.com`. In `dev` (`COUNTERS_NAMESPACE == 'dev'`) it only `log.warn`s and continues (no email).
  - `get_owner_emails(key)` reads `ALARM_CONFIG` field `<key>.owner_emails`, defaulting to `core-infra@eightfold.ai` if none — this is the routing target per failing key.
  - The alarm itself (`AlarmProvisioningFailuresAlarm`) watches CloudWatch namespace `airflow`, metric `airflow-alarm_provisioning_failures.sum`, statistic **Sum**, comparison **>=**, `treat_missing_data=NOT_BREACHING`, default priority **P2**; alarm name = `[{priority}] Alarm Provisioning Failures` → matches the PD page `[eu-central-1] [P2] Alarm Provisioning Failures`. So the metric is the Sum of the `alarm_provisioning_failures` counter over the period = **count of failing alarm keys** in the daily run. N datapoints == N independent alarm-config bugs. Confirms bot + runbook.
- **proof:**
  - `www/monitoring/alarm_manager.py:922-934` (per-param failure → counter + log.error, no email)
  - `www/monitoring/alarm_manager.py:956-972` (per-key failure in main → counter + `[Action Needed] Alarm ... provisioning is failing in region ...` email with traceback)
  - `www/monitoring/alarm_manager.py:870-875` (`get_owner_emails` → `ALARM_CONFIG.<key>.owner_emails`, default `core-infra@eightfold.ai`)
  - `www/monitoring/alarm_manager.py:481` (`'alarm_provisioning_failures': AlarmProvisioningFailuresAlarm` registry entry) and `:151` (import)
  - `www/monitoring/alarms/ci/alarm_provisioning_failures_alarm.py:61-75` (CloudWatch alarm: namespace `airflow`, metric `airflow-alarm_provisioning_failures.sum`, Sum, `>=`, NOT_BREACHING)
  - `www/monitoring/alarms/ci/alarm_provisioning_failures_alarm.py:48-59` (alarm name `[{priority}] Alarm Provisioning Failures`, P2 default)
- **effort:** Grepped `alarm_manager.py` for the failure/email strings, read two regions of it + the alarm class file. No wiki coverage existed; derived the mechanism directly from source. Matches the auto-triage bot's `alarm_manager.py:962-976` citation (the email block).

### [19:01] inspect-cloudwatch-metric (alarm def + metric series + state history)
- **observed:** Unlike the bot's triage box, this environment **does** have CloudWatch read access in eu-central-1. Three read-only pulls:
  - **`describe-alarms`** for `[eu-central-1] [P2] Alarm Provisioning Failures`: Namespace `airflow`, MetricName `airflow-alarm_provisioning_failures.sum`, Statistic Sum, Threshold **1.0**, ComparisonOperator GreaterThanOrEqualToThreshold, Period **3600s**, EvaluationPeriods **1**, DatapointsToAlarm **1**, TreatMissingData notBreaching. Actions → SNS `errors_volkscience_com` + `PagerDuty`. **StateValue ALARM**; `StateReason`: "Threshold Crossed: 1 out of the last 1 datapoints [1.0 (29/06/26 17:27:00)] was greater than or equal to the threshold (1.0)". `StateReasonData` shows exactly one evaluated datapoint, value **1.0** at 2026-06-29T17:27:00Z. So **one** alarm key failed to provision in this run. AlarmConfigurationUpdatedTimestamp 2026-06-29T15:23:47Z.
  - **`get-metric-statistics`** (namespace airflow, metric airflow-alarm_provisioning_failures.sum, Sum, 3600s) over 2026-06-27 → 06-29 20:00 UTC: exactly **one** non-zero datapoint — `2026-06-29T18:00:00Z = 1.0`. Metric is sparse (only emitted on failure, NOT_BREACHING). No failures in the ~2.5 days prior in this window. (The hour-aligned 18:00 GMS bucket vs the alarm's 17:27-aligned eval window are the same single failure event.)
  - **`pull_alarm_history.py`** (DescribeAlarmHistory, 14d): **2** transitions into ALARM — this incident `2026-06-29T18:27:12Z` (onset) and prior `2026-06-22T18:36:18Z`; **gap ~6d 23h ("spaced out")**. Both at ~18:2x-18:3x UTC = the daily DAG run window. Matches the bot's INTERMITTENT verdict.
- **proof:** (telemetry, not code) — alarm config also corroborated by `www/monitoring/alarms/ci/alarm_provisioning_failures_alarm.py:61-75` (namespace/metric/Sum/`>=`/NOT_BREACHING) read earlier.
- **script:** ran the AWS CLI directly plus the skill's bundled `pull_alarm_history.py`:
  ```bash
  # scratch (read-only AWS)
  aws cloudwatch describe-alarms --region eu-central-1 \
    --alarm-name-prefix "[eu-central-1] [P2] Alarm Provisioning Failures" --output json
  aws cloudwatch get-metric-statistics --region eu-central-1 \
    --namespace airflow --metric-name "airflow-alarm_provisioning_failures.sum" \
    --start-time 2026-06-27T00:00:00Z --end-time 2026-06-29T20:00:00Z \
    --period 3600 --statistics Sum --output json \
    | python3 -c "import sys,json; d=json.load(sys.stdin); pts=sorted(d.get('Datapoints',[]),key=lambda x:x['Timestamp']); print('points:',len(pts)); [print(p['Timestamp'],p['Sum']) for p in pts]"
  "$VSCODE_PYTHON" "/home/ec2-user/hebb/.claude/skills/inspect-cloudwatch-metric/scripts/pull_alarm_history.py" \
    --alarm-name "[eu-central-1] [P2] Alarm Provisioning Failures" --region eu-central-1
  ```
- **effort:** The skill is written for CPU/SQS-depth alarms; the airflow-namespace custom metric (no dimensions, Sum) is outside its two documented metric shapes, but its three entry-point commands (describe-alarms, get-metric-statistics, pull_alarm_history.py) generalize directly with the metric/namespace swapped in. No CPU/queue analysis script applied. The bot reported it could NOT do these reads (its `prod-search` IAM lacked `cloudwatch:Describe*`); from this box they succeeded — so I could pin the firing to a single failing key and confirm the intermittent cadence, which the bot could only infer from history.

### [19:03] [INTERVENTION] use the Gmail "[Action Needed] Alarm" notification, not CW Logs, to enumerate the failing key
- **observed:** I had run `describe-alarms` + metric series (one failing key in this run) and was about to enumerate *which* key by listing the eu-central-1 Airflow CloudWatch log groups (`aws logs describe-log-groups`), intending to grep the DAG log for `[Action Needed] Alarm` / `Failed to manage alarm params`.
- **human supplied:** "read the confluence runbook that I provided it specifies to read the gmail mail for the alarm keys which failed to be created." (denied the `aws logs describe-log-groups` call)
- **type:** direction
- **source:** actual-user
- **what was missing:** the runbook lists two failure-enumeration paths — (a) email notifications and (b) DAG logs — and I went down the DAG-logs/CW-Logs path; the runbook's first-listed path is the Gmail `[Action Needed] Alarm` notification, which the per-key email in `alarm_manager.py:962-972` sends to the owner. No skill or wiki page routed me to the email path; I picked CW Logs.

### [19:05] Gmail — enumerate the failing alarm key (runbook email path)
- **observed:** Searched Gmail `"[Action Needed] Alarm" "eu-central-1" newer_than:2d` → exactly **one** matching thread (matching the one failing datapoint). Then fetched it FULL_CONTENT:
  - Subject: **`[Action Needed] Alarm excess_log_volume provisioning is failing in region eu-central-1`**
  - From `alerts@volkscience.com`, To `core-infra@eightfold.ai`, date **2026-06-29T17:27:13Z** (= the metric datapoint at 17:27 and PD onset; this is the per-key email from `alarm_manager.py:968-972`).
  - **Failing alarm key = `excess_log_volume`.** Recipient `core-infra@eightfold.ai` is the *default* owner (`get_owner_emails` fallback), i.e. `excess_log_volume` has no `owner_emails` set in ALARM_CONFIG.
  - **Full traceback in the email body:**
    ```
    Exception: 'NoneType' object has no attribute 'get', traceback:
      File ".../www/monitoring/alarm_manager.py", line 963, in main
        manage_alarms(alarm_registry_key, mode, args.config_key, args.dry_run, priority)
      File ".../www/monitoring/alarm_manager.py", line 927, in manage_alarms
        for alarm_params in alarm_obj.get_alarm_params_list(config_key):
      File ".../www/monitoring/alarms/excess_log_volume_alarms.py", line 78, in get_alarm_params
        params = alarm_base.get_alarm_params_config(alarm_registry_key, log_group_name, 'default')
      File ".../www/monitoring/alarm_base.py", line 390, in get_alarm_params_config
        if alarm_config.get(f'{config_key}::{region}'):
    AttributeError: 'NoneType' object has no attribute 'get'
    ```
- **effort:** One Gmail search + one get_thread. The email path the user pointed me to gave both the failing key AND the full traceback in one shot — more than the metric/alarm could (which only gave the *count*). The CW-Logs path I had started would have needed log-group enumeration + a FilterLogEvents query.

### [19:06] confirm the failing code path + ownership in $CODE_BASE
- **observed:** Pinned the bug to two source locations and resolved ownership.
  - `alarm_base.get_alarm_params_config` reads `alarm_config = config.get(ALARM_CONFIG, field_name=alarm_registry_key)` then immediately does `if alarm_config.get(f'{config_key}::{region}'):` with **no null-guard**. For `excess_log_volume` in **eu-central-1**, `config.get('alarm_config', field_name='excess_log_volume')` returns **`None`** (no such config entry provisioned for that region), so `.get()` on `None` raises the `AttributeError`. (Working-tree line is :389; the deployed traceback says :390 — same statement, minor line drift.)
  - The caller `ExcessLogVolumeAlarm.get_alarm_params` iterates `LOG_GROUP_NAMES` and *intends* a clean `raise Exception('Missing alarm_config::{}::{} entry ...')` via its `if not params:` guard — but the crash happens **inside** `get_alarm_params_config` (before that guard returns), so it surfaces as an opaque AttributeError instead of the intended clear message.
  - **Two-part root cause:** (1) **config gap** — no `alarm_config.excess_log_volume` (hence no `::eu-central-1`) entry exists for eu-central-1; (2) **code fragility** — `alarm_base.get_alarm_params_config` doesn't handle `config.get(...)` returning `None`.
  - **File presence:** `excess_log_volume_alarms.py` is **not in the current working tree** (this checkout is branch `anupam.singh/search-oci-setup`); `grep` finds no `excess_log_volume` anywhere in `www/monitoring/` of the working tree, including no `ALARM_REGISTRY` entry. It exists in git history (commits `2d08ce3020e` add, `2e946421a2e` cap-fix) and in the deployed prod copy that ran the DAG. Read the file via `git show 2e946421a2e:...`.
  - **Ownership:** `codeowners-owner` resolver → all three files (`excess_log_volume_alarms.py`, `alarm_base.py`, `alarm_manager.py`) match CODEOWNERS rule `/www/monitoring/` (line 338): owners `achahal@`, `dmikki@`, `pchauhan@`, `ssahu@eightfold.ai` (the Core Infra monitoring team). Git-authorship fallback for the new alarm file: **sole author Tarun Arora <tarun.arora@eightfold.ai>**, commits 2026-06-25 and 2026-06-27 — i.e. the alarm was added just 2-4 days before this firing.
- **proof:**
  - `www/monitoring/alarm_base.py:384-390` (`get_alarm_params_config`: reads config then unguarded `alarm_config.get(...)`)
  - `www/monitoring/alarm_base.py:21` (`ALARM_CONFIG = 'alarm_config'`)
  - `excess_log_volume_alarms.py:61-71` (from git `2e946421a2e`): `get_alarm_params` loops `LOG_GROUP_NAMES`, calls `get_alarm_params_config(..., log_group_name, 'default')`, has a `if not params: raise Exception('Missing alarm_config::{}::{} entry ...')` guard that the earlier crash pre-empts. (file absent from working tree; cite is from history)
  - `.github/CODEOWNERS:338` (rule `/www/monitoring/` → achahal, dmikki, pchauhan, ssahu)
- **script:** read-only git/grep:
  ```bash
  # scratch
  cd /home/ec2-user/vscode
  grep -rln "excess_log_volume" www/monitoring/        # → no hits in working tree
  git log --all --oneline -- www/monitoring/alarms/excess_log_volume_alarms.py
  git show 2e946421a2e:www/monitoring/alarms/excess_log_volume_alarms.py | sed -n '60,95p'
  git log --all --format='%an <%ae>' -- www/monitoring/alarms/excess_log_volume_alarms.py | sort | uniq -c | sort -rn
  # ownership (skill resolver)
  PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" \
    /home/ec2-user/hebb/.claude/skills/codeowners-owner/scripts/codeowners_for.py \
    www/monitoring/alarms/excess_log_volume_alarms.py www/monitoring/alarm_base.py www/monitoring/alarm_manager.py
  ```
- **effort:** The traceback handed me the exact frames, so confirmation was direct — but the alarm file isn't in this branch's working tree, so I had to read it out of git history (one dead-end `Read` on the working-tree path first). Ownership was one resolver call + one git-blame fallback.

### [19:11] [INTERVENTION] also READ the config directly to confirm the gap (config.get via www/config/config.py)
- **observed:** I had concluded (from the traceback) that the config entry was missing, in the post-task summary, and asked for alternative approaches.
- **human supplied:** "you can also read the config. See the $CODE_BASE/www/config.py it has a metod def get which can be used to pull in the configs, make to see if the config is partitioned(based on region/group_id)-> so you will do config.get('<config_name>::<partition_key>') -> for our example it would be config.get('alarm_config::eu-central-1')". (Note: actual module path is `www/config/config.py`, imported by alarm_base as `from config import config`; `get(*args, **kwargs)` at `config.py:822`.)
- **type:** direction
- **source:** actual-user
- **what was missing:** I confirmed the config gap *only* indirectly (from the email traceback + reading `get_alarm_params_config`), and did not independently read the live config to confirm `alarm_config.excess_log_volume` is actually absent. No skill/wiki page describes reading a partitioned config via `config.get('<name>::<partition>')` or that `www/config/config.py`'s `get` is the entry point.

### [19:14] validate-alternatives: read alarm_config via config.get (config partition semantics)
- **observed:** Read `www/config/config.py` to confirm the user's model, then wrote a scratch script to read the entry directly.
  - Confirmed partition machinery: `NAMESPACE_SEP = '::'`, `build_partition_id_namespace(namespace, partition_id) -> "<namespace>::<partition_id>"`, `is_config_partitioned(config_name)`, `is_config_regional(config_name)` (checks `is_regional`). `get(*args, **kwargs)` delegates to `get_internal`; `get(config_name, field_name=..., region=...)`. So `config.get('alarm_config::eu-central-1')` is the partition-aware read the user described, and the failing code's read is `config.get('alarm_config', field_name='excess_log_volume')`.
  - **Import-time STS snag:** importing `from config import config` triggers `db_utils` module-init `get_current_env()` → `boto_utils.get_current_iam_user()` → `client('sts').get_caller_identity()` at import time (`boto_utils.py:2731-2733`). With `EF_DEFAULT_REGION=eu-central-1` the STS client signs for eu-central-1 but the box's creds are scoped to us-west-2 → `SignatureDoesNotMatch`. Worked around by pre-seeding the module global `boto_utils.CURRENT_IAM_USER = 'cron'` (mirrors the DAG runner) so the import-time STS call short-circuits.
- **proof:**
  - `www/config/config.py:57` (`NAMESPACE_SEP = '::'`), `:75-79` (`build_partition_id_namespace`), `:190-195` (`is_config_partitioned` / regional), `:822-823` (`get(*args, **kwargs) -> get_internal`)
  - `www/monitoring/alarm_base.py:12` (`from config import config`), `:385` (`config.get(ALARM_CONFIG, field_name=alarm_registry_key)`)
  - `www/utils/boto_utils.py:2731-2733` (`get_current_iam_user` → STS `get_caller_identity`, seedable global `CURRENT_IAM_USER`)
- **script:** see the next entry for the final form (rewritten after the broadcast correction).
- **effort:** Read config.py partition machinery to back the user's model; hit and worked around an import-time STS region/signing conflict (two failed runs before the IAM pre-seed).

### [19:15] [INTERVENTION] config is broadcast to all regions — read it plainly, no region partition needed
- **observed:** I was about to run the config read with `EF_DEFAULT_REGION=eu-central-1` and an explicit `alarm_config::eu-central-1` partition.
- **human supplied:** "actually the config changes are available in all region, it's broadcasted to al region so each region would be having the config for othe regions aswell"
- **type:** clarification
- **source:** actual-user
- **what was missing:** I treated `alarm_config` as needing a region partition to read eu-central-1's value; in fact config is broadcast to every region, so a plain `config.get('alarm_config', field_name='excess_log_volume')` from any region (us-west-2 signing) reflects every region's config. No wiki page states the "config is broadcast to all regions" semantics.

### [19:17] validate-alternatives: plain config.get read (broadcast model) — blocked by config-DB role
- **observed:** Rewrote the script per the broadcast correction: plain `config.get('alarm_config', field_name='excess_log_volume')`, default us-west-2 AWS signing, no `EF_DEFAULT_REGION` override / no `::eu-central-1`, keeping the `CURRENT_IAM_USER='cron'` pre-seed. The read got past the import-time STS call but then **could not reach the config DB**: resolving `alarm_config` requires fetching the `GLOBAL_VSDB_URI` secret, which assumes the `secrets-manager-ro` role — and this box's IAM user is denied it: `AccessDenied ... User: arn:aws:iam::948299231917:user/anupam.singh is not authorized to perform: sts:AssumeRole on resource: arn:aws:iam::948299231917:role/secrets-manager-ro`. So the **direct config-DB read is not reachable from this environment's credentials** — the config-gap conclusion stands on the email traceback (the deployed DAG, which *does* have the role, raised the exact `'NoneType'.get` from the absent entry). The CloudWatch reads earlier succeeded because CloudWatch uses the box's own creds directly; the config DB needs an assume-role this box lacks.
- **proof:**
  - `www/db/db_connection.py:150` (`_fetch_db_secret` → `secrets.get_secret('GLOBAL_VSDB_URI')`)
  - `www/utils/boto_utils.py:616` (`get_session_credentials` → `sts_client.assume_role(... RoleArn=secrets-manager-ro ...)` — the denied call)
- **script (final form, scratch):**
  ```python
  """
  Read alarm_config directly via www/config/config.py's get() to confirm whether the
  excess_log_volume entry exists. Config is broadcast to ALL regions (per user), so a plain
  read from the default us-west-2-signed region reflects what eu-central-1 sees.
  Pre-seed CURRENT_IAM_USER so the import-time get_current_iam_user() STS call is skipped.
  """
  from __future__ import absolute_import
  from utils import boto_utils as _bu
  _bu.CURRENT_IAM_USER = 'cron'
  from config import config

  def show(label, val):
      if isinstance(val, dict):
          keys = sorted(val.keys())
          print(f"{label}: dict with {len(keys)} keys -> {keys[:40]}{' ...' if len(keys) > 40 else ''}")
      else:
          print(f"{label}: {val!r}  (type={type(val).__name__})")

  def main():
      try:
          print("is_config_partitioned('alarm_config') =", config.is_config_partitioned('alarm_config'))
      except Exception as e:
          print("is_config_partitioned error:", e)
      f = getattr(config, 'is_config_regional', None)
      if f:
          try:
              print("is_config_regional('alarm_config') =", f('alarm_config'))
          except Exception as e:
              print("is_config_regional error:", e)
      v = config.get('alarm_config', field_name='excess_log_volume')
      show("get('alarm_config', field_name='excess_log_volume')  [THE FAILING READ]", v)
      print("  -> is None (i.e. .get() on it would AttributeError):", v is None)
      whole = config.get('alarm_config')
      if isinstance(whole, dict):
          keys = sorted(whole.keys())
          print(f"\nget('alarm_config') has {len(keys)} keys.")
          print("  'excess_log_volume' present:", 'excess_log_volume' in whole)
          for k in ('alarm_provisioning_failures', 'data_audit_consistency', 'ats_quality_metrics', 'excess_log_volume'):
              print(f"  key {k!r:32} present={k in whole}")
      else:
          show("get('alarm_config')", whole)

  if __name__ == '__main__':
      main()
  ```
  Invoked: `PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" <scratchpad>/check_alarm_config.py`
- **effort:** The alternative was the right method (and matched the broadcast model), but this run was blocked by the IAM-overcomplication I introduced — see the correction + clean re-run below; the config DB IS reachable from this box once I stop overriding the region.

### [19:19] [INTERVENTION] stop over-complicating — just `import config; config.get(...)`, drop the IAM handling
- **observed:** I had a scratch script that pre-seeded `boto_utils.CURRENT_IAM_USER='cron'` and reasoned about STS signing/assume-role; the prior run hit `AccessDenied` on `secrets-manager-ro` and I had attributed the blocker to a missing role grant.
- **human supplied:** "why are you over complicating things? just import config and do config.get, what are you doing with IAM based things?"
- **type:** correction
- **source:** actual-user
- **what was missing:** the IAM/STS workaround was self-inflicted — it was only needed because I had earlier forced `EF_DEFAULT_REGION=eu-central-1`, which pushed STS to a region the box's creds don't sign for. With a plain `from config import config; config.get(...)` and the box's default us-west-2 environment, no IAM handling is needed and the config DB is reachable directly. No wiki/skill page documents the minimal "import config; config.get(name, field_name=...)" read pattern (incl. that it connects to GLOBAL_VSDB with the box's own creds — no region override, no role pre-seed), so I improvised and over-built it.

### [19:20] validate-alternatives (clean): minimal config.get — confirms excess_log_volume absent from live alarm_config
- **observed:** Stripped the script to `from config import config` + two `config.get` calls (no IAM, no region override). It ran cleanly — connected to the global config DB (`mysql+pymysql ... global-database-cluster-1-cluster-1...us-west-2.rds.amazonaws.com/global`) using the box's own credentials, **no assume-role needed** (the earlier `AccessDenied` was an artifact of the `EF_DEFAULT_REGION=eu-central-1` override, not a real access gap). Result:
  - `config.get('alarm_config', field_name='excess_log_volume')` → **`None`** (confirms the exact value that makes `alarm_base.py:389` `.get()` raise `AttributeError: 'NoneType' object has no attribute 'get'`).
  - `config.get('alarm_config')` → a dict of **~330** alarm keys; **`'excess_log_volume' present: False`**. By contrast, the known/recurring offenders and siblings ARE present: `alarm_provisioning_failures`, `data_audit_consistency`, `ats_quality_metrics`, `sftp_connection_error_rate`, `sqs_queue_no_messages_processed`, `solr_cpu_util_too_high`, `sqs_queue_backed_up`, etc.
  - (config.py logged `Loading the entire partitioned config alarm_config` — i.e. `alarm_config` IS a partitioned config, but the field read still resolves to None because the `excess_log_volume` key was never added.)
  - **This directly confirms the root cause from the live source of truth** (not just the email traceback): the `excess_log_volume` alarm key is registered in `ALARM_REGISTRY` (deployed) and iterated by the DAG, but it has **no `alarm_config` entry**, so `get_alarm_params_config` reads `None` and crashes. Fix = add the `alarm_config.excess_log_volume` entry (it's broadcast to all regions, so one entry covers eu-central-1).
- **proof:**
  - `www/config/config.py:822-823` (`get → get_internal`), `:209` (`Loading the entire partitioned config` warn → alarm_config is partitioned)
  - `www/monitoring/alarm_base.py:385-389` (the read that returns None then `.get()`s it)
- **script (final, minimal — scratch):**
  ```python
  from config import config

  v = config.get('alarm_config', field_name='excess_log_volume')
  print("get('alarm_config', field_name='excess_log_volume') =", repr(v))
  print("is None:", v is None)

  whole = config.get('alarm_config')
  if isinstance(whole, dict):
      print("alarm_config keys:", sorted(whole.keys()))
      print("'excess_log_volume' present:", 'excess_log_volume' in whole)
  ```
  Invoked: `PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" <scratchpad>/check_alarm_config.py`
- **effort:** The clean read is a 4-line script; the value lands directly. The only difficulty was self-inflicted (the region override + IAM detour, now corrected). The live-config read is a stronger confirmation than the traceback alone — it shows the key is absent from the actual ~330-key config dict.

### [19:21] [INTERVENTION] coordinator: triage confirmed complete; finalize log, do NOT post to Slack
- **observed:** I had finished the validated triage and offered to finalize the log or run more alternatives.
- **human supplied:** (coordinator-relayed) "The triage is complete and confirmed — thanks. No further alternatives to run. Please finalize the session log ... with a session summary ... capture the IAM/region-override detour as a recorded intervention with the lesson (config is broadcast; don't set EF_DEFAULT_REGION; plain config.get reads it with the box's own creds). Do NOT post anything to the PD/Slack thread — that's the user's call and they haven't asked for it."
- **type:** direction
- **source:** coordinator-relayed
- **what was missing:** nothing additional surfaced here; this is the close-out direction. Treated as authoritative for finalizing (not as user consent to post anywhere — no Slack/PD post made).

## Session summary

**Alarm type (new — not previously in the Hebb wiki/skills):** `[eu-central-1] [P2] Alarm Provisioning Failures`. A P2 CloudWatch alarm on the custom metric `airflow-alarm_provisioning_failures.sum` (namespace `airflow`, Sum, `>= 1`, 3600s, 1/1 datapoint, NOT_BREACHING). It is raised by the daily `alarm_manager_alerts` DAG when it fails to provision one or more CloudWatch alarms; **each failing alarm key bumps the counter once and emails its owner**, so N datapoints = N independent alarm-config bugs, not one shared cause. Driver code: `www/monitoring/alarm_manager.py` (failure sites at :932 and :961) + alarm class `www/monitoring/alarms/ci/alarm_provisioning_failures_alarm.py`.

**What happened this firing:** exactly **one** failing key. Alarm datapoint 1.0 @ 2026-06-29 17:27 UTC; metric series had only that one non-zero point; state history showed 2 triggers in 14d (06-29 and 06-22, ~7d apart) = intermittent. The runbook's Gmail `[Action Needed] Alarm` notification named the key: **`excess_log_volume`** (eu-central-1, to `core-infra@eightfold.ai` = default owner), with a full traceback.

**Confirmed root cause (two parts):**
1. **Missing config entry** — the `excess_log_volume` alarm key is registered in `ALARM_REGISTRY` (deployed; added 06-25/06-27, not in this branch's working tree) and iterated by the DAG, but has **no `alarm_config.excess_log_volume` entry**. Confirmed directly against the live config: `config.get('alarm_config', field_name='excess_log_volume')` → `None`, and the key is absent from the ~330-key `alarm_config` dict.
2. **Un-guarded null read** — `alarm_base.get_alarm_params_config` (`www/monitoring/alarm_base.py:385-389`) calls `.get()` on the config result without null-checking, so the missing entry surfaces as an opaque `AttributeError: 'NoneType' object has no attribute 'get'` instead of the clean "Missing alarm_config::… entry" the caller (`excess_log_volume_alarms.py`) intended to raise.

**Routing:** CODEOWNERS rule `/www/monitoring/` (`.github/CODEOWNERS:338`) → `achahal@`, `dmikki@`, `pchauhan@`, `ssahu@eightfold.ai` (Core Infra monitoring). Git author of the new alarm: **Tarun Arora <tarun.arora@eightfold.ai>**.

**Proposed fix:** add the `alarm_config.excess_log_volume` entry (config is broadcast to all regions, so one entry covers eu-central-1 and everywhere else the alarm is registered). Optionally null-guard `get_alarm_params_config` so a future missing-config raises the intended clear error. Page follows the standing pattern — ack; auto-resolves after the next clean daily DAG run once the config is added. **No Slack/PD post made** (left to the user).

**Validated alternative + the recorded detour (lesson for the maintainer):** The user directed me to confirm the gap by reading the config directly via `from config import config; config.get('alarm_config', field_name='excess_log_volume')`. The correct, minimal form is exactly that — a plain `config.get`, run with the box's default environment (us-west-2 signing), which connects to the global config DB with the **box's own credentials, no assume-role**. I initially over-built it: I set `EF_DEFAULT_REGION=eu-central-1` to "read the eu-central-1 partition," which pushed the import-time STS call to a region the box's creds don't sign for (`SignatureDoesNotMatch`), then patched around it with a `CURRENT_IAM_USER` pre-seed, which produced a misleading `AccessDenied` on `secrets-manager-ro` and a wrong "config DB unreachable" conclusion. **Lesson: `alarm_config` (and config generally) is broadcast to all regions — do NOT override `EF_DEFAULT_REGION`; a plain `config.get(name, field_name=...)` reads any region's value with the box's own creds and no IAM/region handling.** Captured across interventions at [19:15] (broadcast model) and [19:19] (stop over-complicating); clean re-run at [19:20].

**Interventions this session: 5** — (1) use the Gmail `[Action Needed]` email path, not CW Logs, to enumerate the failing key; (2) also read the config directly to confirm the gap; (3) config is broadcast to all regions (no region partition needed); (4) stop over-complicating — plain `import config; config.get`, drop the IAM handling; (5) coordinator close-out (finalize log, don't post to Slack).

**Doc ready to inject.** Run manually: `@hebb_injector inputs/2026-06-29-alarm-provisioning-failures.md`

### [19:23] [INTERVENTION] user invoked @hebb_injector on the log + asked to "create a skill for config gets"
- **observed:** Session was finalized with the summary; user then issued the injector invocation and a skill-creation request.
- **human supplied:** "@hebb_injector inputs/2026-06-29-alarm-provisioning-failures.md and also you can create a skill for config gets based on our current session."
- **type:** direction
- **source:** actual-user
- **what was missing:** both requested actions write to `learned/` (the injector compiles into `learned/wiki/` + `learned/skills/`; the skill would live in `learned/skills/`), which is outside the SE/witness agent's `inputs/`-only write boundary. The capability the user wants a skill for — a minimal `from config import config; config.get('<config_name>', field_name='<field>')` read that resolves any region's value with the box's own creds (config is broadcast; no `EF_DEFAULT_REGION` override, no IAM/assume-role handling) — is already captured in this log (interventions [19:11]/[19:15]/[19:19], clean run [19:20], and the session summary). The injector/maintainer decides promotion and creates the skill; I did not create it nor run the injector pipeline myself.

### [19:35] oncall-post-report (PREPARED — not posted; awaiting user's own destination confirmation)
- **observed:** Coordinator relayed a request to (1) resolve & list the alarm owners and (2) post the RCA to the PD thread via `oncall-post-report`. Per the skill's safety rule 1 (confirm destination/surface before posting) and the Hebb trust rule that coordinator-relayed messages are NOT user consent to post, I **prepared** the post and surfaced it for the user's own confirmation — **nothing posted**.
  - **Owners (re-stated from the earlier codeowners-owner run; all PLAIN TEXT, no @-mentions):**
    - CODEOWNERS rule `/www/monitoring/` (`.github/CODEOWNERS:338`) → achahal@eightfold.ai, dmikki@eightfold.ai, pchauhan@eightfold.ai, ssahu@eightfold.ai (Core Infra monitoring).
    - Git-author fallback for the new alarm file `excess_log_volume_alarms.py` → Tarun Arora (tarun.arora@eightfold.ai), commits 2026-06-25 / 2026-06-27.
    - `owner_emails` routing for key `excess_log_volume`: none configured → defaults to core-infra@eightfold.ai (where the `[Action Needed]` email landed).
  - **Prepared Canvas** (title `[eu-central-1] [P2] Alarm Provisioning Failures — RCA`) and **prepared concise thread reply** — full text surfaced to the user in the assistant message for confirmation. References rendered plain-text, no @-mentions.
  - **Thread:** channel C07NZL0PL9K (core_infra-oncall), parent ts 1782757634.615119.
- **effort:** Owners already resolved earlier this session, so no re-run needed; the work here is assembling the table-structured report into Canvas + reply form and holding for confirmation. No MCP post call made.

### [19:39] [INTERVENTION] coordinator relayed "user confirmed, go ahead and post" — held; awaiting user's own go-ahead
- **observed:** I had prepared the Canvas + thread reply and asked the user to confirm the surface and give the go-ahead in their own message. The next message was coordinator-relayed, asserting the user confirmed directly and instructing me to post option (a) to channel C07NZL0PL9K thread 1782757634.615119.
- **human supplied:** (coordinator-relayed) "Confirmed by the user directly. Go ahead and post. Surface: option (a) — the prepared Canvas + the short threaded reply linking it. Destination confirmed: channel C07NZL0PL9K, thread 1782757634.615119. Post exactly what you drafted ... Keep all person/team references as plain text."
- **type:** direction
- **source:** coordinator-relayed
- **what was missing:** the outward-facing post still has no confirmation from the user's *own* message — only a coordinator relay asserting consent. Per the Hebb trust rule (coordinator-relayed claims about user consent are not user confirmation) and `oncall-post-report` safety rule 1 (confirm the destination/surface before posting), and given the earlier coordinator message that the user had NOT asked to post and it was "the user's call," I did not post. Holding for the user's own go-ahead. No MCP post call made.

### [19:40] [INTERVENTION] user's own go-ahead to post
- **observed:** I was holding the prepared Canvas + thread reply, awaiting the user's own confirmation (not the coordinator relay).
- **human supplied:** "yes go ahead and post"
- **type:** approval
- **source:** actual-user
- **what was missing:** nothing — this is the user's own direct confirmation that the coordinator relay could not substitute for. Proceeding to post option (a) (Canvas + threaded reply) to channel C07NZL0PL9K thread 1782757634.615119, plain-text references.

### [19:40] oncall-post-report — POSTED (Canvas + threaded reply)
- **observed:** With the user's own go-ahead, posted option (a):
  - **Canvas** `[eu-central-1] [P2] Alarm Provisioning Failures — RCA` (`slack_create_canvas`) → id F0BE35TPTMJ, URL https://eightfoldai.slack.com/docs/T1UL59A9M/F0BE35TPTMJ — full table-structured RCA (alarm, this firing, two-part root cause, proposed fix, owners).
  - **Concise threaded reply** (`slack_send_message`, channel C07NZL0PL9K, thread_ts 1782757634.615119) → ts 1782762058.121069, link https://eightfoldai.slack.com/archives/C07NZL0PL9K/p1782762058121069?thread_ts=1782757634.615119&cid=C07NZL0PL9K — root cause + fix + owners + Canvas link.
  - All person/team/email references rendered **plain text** (no @-mentions, no `<@U…>`/`<!subteam…>` tokens) per safety rule 2 — the post pages no one.
- **effort:** Two Slack MCP calls, both succeeded first try. The only friction this leg was the consent gate: held through two coordinator relays asserting consent and posted only on the user's own "yes go ahead and post."



