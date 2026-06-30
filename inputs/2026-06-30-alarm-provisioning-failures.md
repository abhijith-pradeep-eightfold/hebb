---
task: Triage a "[us-gov-west-1] [P2] Alarm Provisioning Failures" PagerDuty oncall page shared via Slack
date: 2026-06-30
skills_used:
  - {name: external-context-puller, note: pulled the PagerDuty Slack thread; identified alarm type}
  - {name: oncall-alarm-provisioning-failures, note: end-to-end runbook for this page type}
  - {name: inspect-cloudwatch-metric, note: alarm def + metric series + state history; GovCloud creds exported}
  - {name: config-get, note: confirmed alarm_config has no excess_log_volume entry (returns None)}
  - {name: codeowners-owner, note: /www/monitoring/ owners + git author Tarun Arora (ENG-200027)}
interventions: 0
---

# Oncall triage — Alarm Provisioning Failures (us-gov-west-1)

**Task:** Triage an oncall issue shared as a Slack link. Pull the thread context, identify the alarm type, run the appropriate end-to-end oncall runbook to confirm/characterize the metric, find the driver, trace to a root cause, and identify the owning team. Record a witness log. Do NOT post outward-facing without explicit consent.

## Log

### [07:17] external-context-puller
- **observed:** Parsed the Slack URL (channel `C07NZL0PL9K`, message ts `1782754335.621829`) and read the thread via `slack_read_thread`. Thread is a single PagerDuty bot message, no replies. Content:
  - Title: **[us-gov-west-1] [P2] Alarm Provisioning Failures**
  - Incident: `https://volkscience.pagerduty.com/incidents/Q0T83F7W780IHW`
  - Incident type: Base Incident; Urgency: High
  - Service: **Core Infra** (`https://volkscience.pagerduty.com/services/P7I5DOG`)
  - Triggered via AWS Console alarm `[us-gov-west-1] [P2] Alarm Provisioning Failures`
  - Status icon was green (large_green_circle) at message time.
- **effort:** Single MCP thread read. No code exploration. Alarm type maps directly to the `oncall-alarm-provisioning-failures` runbook.

### [07:20] inspect-cloudwatch-metric — alarm + metric + state history
- **observed:** This is a GovCloud (`us-gov-west-1`) alarm. Env had `GOV_AWS_ACCESS_KEY_ID`/`GOV_AWS_SECRET_ACCESS_KEY` set; exported them as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for the reads.
  - **Alarm definition** (`describe-alarms`): `AlarmName="[us-gov-west-1] [P2] Alarm Provisioning Failures"`, Namespace `airflow`, MetricName `airflow-alarm_provisioning_failures.sum`, Statistic `Sum`, Threshold `1.0`, ComparisonOperator `GreaterThanOrEqualToThreshold`, Period `3600`, EvaluationPeriods `1`, DatapointsToAlarm `1`, Dimensions `[]`, TreatMissingData `notBreaching`. Actions: SNS `PagerDuty` + `errors_volkscience_com`. Current `StateValue=OK` (StateUpdatedTimestamp `2026-06-29T20:32:13.399Z`, "no datapoints received... treated as NonBreaching").
  - **Metric series** (`get-metric-statistics`, full 2026-06-29 UTC day, period 3600, Sum): exactly **one datapoint** — `2026-06-29T17:00:00Z Sum=1.0`. Per the runbook, the Sum value = the count of failing alarm keys → **1 failing key = 1 independent config bug**.
  - **State history** (`pull_alarm_history.py`): **one** transition into ALARM in the 14-day window — `2026-06-29T17:32:13.398Z` OK→ALARM. Cleared back to OK at `2026-06-29T20:32:13.399Z` (~3h later, the standard ack-and-auto-resolve window). Rare/intermittent, not chronic (caveat: CloudWatch retains ~14d, an older prior trigger wouldn't show).
- **effort:** Three read-only AWS CLI calls (one bundled history script). The runbook + inspect-cloudwatch-metric skill carried the namespace/metric/threshold facts; no code exploration needed. GovCloud cred export was the only setup.

Commands (all with GOV creds exported):
```bash
aws cloudwatch describe-alarms --region us-gov-west-1 --alarm-name-prefix "[us-gov-west-1] [P2] Alarm Provisioning Failures" --output json
aws cloudwatch get-metric-statistics --region us-gov-west-1 --namespace airflow --metric-name "airflow-alarm_provisioning_failures.sum" --start-time 2026-06-29T00:00:00Z --end-time 2026-06-30T00:00:00Z --period 3600 --statistics Sum --output json
"$VSCODE_PYTHON" ".../inspect-cloudwatch-metric/scripts/pull_alarm_history.py" --alarm-name "[us-gov-west-1] [P2] Alarm Provisioning Failures" --region us-gov-west-1
```

### [07:24] Enumerate failing key — Gmail `[Action Needed]` email
- **observed:** Searched Gmail for `subject:"[Action Needed] Alarm" "provisioning is failing" after:2026/06/28 before:2026/06/30`. Got 11 threads from `alerts@volkscience.com` to `core-infra@eightfold.ai` across many regions/keys (excess_log_volume in us-west-2, eu-central-1, us-gov-west-1, ca-central-1, ap-southeast-2, westus2, mil; frontdoor_5xx_request_count in westus2; djsafe_validation_active_mode_alarm in mil). **Each region's alarm-provisioning DAG fires its own page**; only the `us-gov-west-1` one is this incident.
  - The matching email for our page: thread `19f143be9bd6befb`, subject **`[Action Needed] Alarm excess_log_volume provisioning is failing in region us-gov-west-1`**, date `2026-06-29T16:34:54Z`, recipient `core-infra@eightfold.ai`.
  - **Failing key = `excess_log_volume`**, region `us-gov-west-1`.
  - Full traceback from the email body:
    ```
    Exception: 'NoneType' object has no attribute 'get'
    Traceback (most recent call last):
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
  - This is the witnessed missing-`alarm_config`-entry shape: `get_alarm_params_config` reads the config, gets `None`, and calls `.get()` on it unguarded.
- **effort:** One Gmail MCP search + one thread fetch (the runbook's preferred email path). The email yielded both the failing key and the full traceback in one shot — no DAG-log fallback needed.

### [07:26] Confirm root cause — source frames + config-get
- **observed:** Read the live source for each traceback frame and confirmed the missing-config root cause:
  - `alarm_base.py:21` defines `ALARM_CONFIG = 'alarm_config'`.
  - `alarm_base.py:384-390` `get_alarm_params_config(alarm_registry_key, config_key, default_config_key)`: line 385 `alarm_config = config.get(ALARM_CONFIG, field_name=alarm_registry_key)`; line 386 `region = os.getenv('EF_DEFAULT_REGION')`; line 389 `if alarm_config.get(f'{config_key}::{region}'):` — the **unguarded `.get()` on a possibly-`None` `alarm_config`**. (The email traceback labeled this `:390`; live HEAD has the unguarded call at `:389` — minor line drift, same statement.)
  - `excess_log_volume_alarms.py` is **not present on the current branch** (`anupam`). `grep -rn excess_log_volume www/monitoring/` returns nothing. The file exists only on the unmerged feature branch `origin/feature/ENG-200027/excess_log_volume_alarms`. On that branch the class `ExcessLogVolumeAlarm.__init__` sets `alarm_registry_key='excess_log_volume'`, and `get_alarm_params` (line ~62-71 on that branch) calls `alarm_base.get_alarm_params_config(alarm_registry_key, log_group_name, 'default')` — so `alarm_registry_key` passed into the failing read = `'excess_log_volume'`.
  - **config-get (live source of truth):** `config.get('alarm_config', field_name='excess_log_volume') = None`, `is None: True`. The `--has` check reported the resolved value is not a dict (NoneType), so the key is absent. => the `alarm_config` global config has **no entry** for key `excess_log_volume`. That `None` is exactly what `alarm_base.py:389` then calls `.get()` on → `AttributeError: 'NoneType' object has no attribute 'get'`.
  - **Root cause:** the `excess_log_volume` alarm code (ENG-200027) was registered/deployed in the alarm_manager run, but the corresponding `alarm_config.excess_log_volume` config entry was never added, so the daily `alarm_manager_alerts` DAG failed to provision it in `us-gov-west-1`. (Same key is failing in 6 other regions per the email sweep — consistent with a broadcast config: one missing entry => fails in every region's DAG run. Only us-gov-west-1 is this specific page.)
- **proof:**
  - `www/monitoring/alarm_base.py:21` (`ALARM_CONFIG = 'alarm_config'`)
  - `www/monitoring/alarm_base.py:385` (`config.get(ALARM_CONFIG, field_name=alarm_registry_key)`)
  - `www/monitoring/alarm_base.py:389` (`if alarm_config.get(f'{config_key}::{region}'):` — unguarded `.get()` on `None`)
  - `www/monitoring/alarm_manager.py:927` (`for alarm_params in alarm_obj.get_alarm_params_list(config_key):`) and `:963` (`manage_alarms(...)` in `main`) — per the email traceback (not re-read on the current branch)
  - feature branch `origin/feature/ENG-200027/excess_log_volume_alarms:www/monitoring/alarms/excess_log_volume_alarms.py` — `alarm_registry_key='excess_log_volume'` and the `get_alarm_params` call into `get_alarm_params_config`
- **effort:** Read 3 live source frames + the feature-branch version of the missing file via `git show`. One scoped grep timed out at repo scope (a recursive `grep -rn` over the whole `vscode` repo runs >2min) — re-scoping to `www/monitoring/` was instant. config-get bundled script confirmed the missing entry against the live config DB in one call.

Commands:
```bash
# locate / confirm (scoped to www/monitoring — repo-wide grep times out)
grep -rn "ALARM_CONFIG\s*=" www/monitoring/alarm_base.py
grep -rn "excess_log_volume" www/monitoring/        # -> no output (not on this branch)
git show origin/feature/ENG-200027/excess_log_volume_alarms:www/monitoring/alarms/excess_log_volume_alarms.py
# root-cause confirmation
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" ".../config-get/scripts/read_config.py" alarm_config --field-name excess_log_volume --has excess_log_volume
```

### [07:28] codeowners-owner — routing
- **observed:** The `[Action Needed]` email recipient was `core-infra@eightfold.ai` — the **default** owner (no owner configured for the `excess_log_volume` key), so resolved the alarm-file owner.
  - CODEOWNERS: all three monitoring files (`excess_log_volume_alarms.py`, `alarm_base.py`, `alarm_manager.py`) match rule `/www/monitoring/` (CODEOWNERS line 338) → owners `achahal@eightfold.ai`, `dmikki@eightfold.ai`, `pchauhan@eightfold.ai`, `ssahu@eightfold.ai`.
  - Git authorship of the feature-branch alarm file: **Tarun Arora <tarun.arora@eightfold.ai>** (2 of 2 commits). The branch's two alarm commits are tagged `[Core Infra]`: `2d08ce3` "add Excess Log Volume alarms for Processor/WWW" (2026-06-25) and `2e94642` "excess_log_volume: comply with period*eval <= 86400 cap" (2026-06-27) — both by Tarun Arora.
  - **Routing:** the most direct owner is Tarun Arora (added the alarm code under ENG-200027 but not the `alarm_config.excess_log_volume` entry); the Core Infra `/www/monitoring/` CODEOWNERS team backs it. PagerDuty service is Core Infra, consistent.
  - **Proposed fix:** add an `alarm_config.excess_log_volume` config entry (one entry covers every region — config is broadcast), keyed by the `LOG_GROUP_NAMES` / region keys the alarm class expects. The page follows the standing ack-and-wait pattern; it already auto-resolved (OK at 2026-06-29T20:32:13Z) after the metric stopped reporting, and will stay clear once the next daily DAG run has the config entry.
- **proof:** `.github/CODEOWNERS:338` (`/www/monitoring/` rule); git authorship on `origin/feature/ENG-200027/excess_log_volume_alarms`.
- **effort:** One bundled CODEOWNERS resolver call + a `git log` author tally on the feature branch. Straightforward; default-owner → file-owner fallback as the runbook prescribes.

## Session summary
- **What was done:** Triaged the `[us-gov-west-1] [P2] Alarm Provisioning Failures` PagerDuty page (incident Q0T83F7W780IHW, Core Infra) shared via Slack. Ran the `oncall-alarm-provisioning-failures` runbook end to end: external-context-puller → inspect-cloudwatch-metric → Gmail `[Action Needed]` email → live source read + config-get → codeowners-owner.
- **Result (root cause):** Exactly **one** failing alarm key — `excess_log_volume` (metric Sum=1 = 1 key). The daily `alarm_manager_alerts` DAG crashes provisioning it because `config.get('alarm_config', field_name='excess_log_volume')` returns `None` (no `alarm_config` entry for that key), and `alarm_base.py:389` calls `.get()` on it unguarded → `AttributeError: 'NoneType' object has no attribute 'get'`. The alarm code was added on the unmerged feature branch `feature/ENG-200027/excess_log_volume_alarms` without its matching `alarm_config` entry. Same key fails in 6 other regions (broadcast config), but only us-gov-west-1 is this page.
- **Chronicity:** Rare — one trigger in the 14-day CloudWatch window (ALARM 2026-06-29T17:32:13Z, auto-resolved OK 2026-06-29T20:32:13Z).
- **Routing:** Core Infra. Default owner email was `core-infra@eightfold.ai`; file owner = `/www/monitoring/` CODEOWNERS team (achahal, dmikki, pchauhan, ssahu); most direct = git author **Tarun Arora** (ENG-200027).
- **Fix:** Add the `alarm_config.excess_log_volume` entry (one entry, broadcast to all regions). Page auto-resolves after the next clean daily DAG run.
- **Outward-facing posts:** None. Per the task, no Slack/PagerDuty post was made; oncall-post-report was NOT invoked (awaiting explicit user consent).
- **Alternatives validated:** none proposed yet (pending user feedback).
