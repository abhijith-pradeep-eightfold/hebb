# Alarm Provisioning Failures (oncall ticket type)

**Summary:** A PagerDuty `[<region>] [P2] Alarm Provisioning Failures` page fires when the daily `alarm_manager_alerts` DAG fails to provision one or more CloudWatch alarms. The backing metric (`airflow-alarm_provisioning_failures.sum`) is the **count of failing alarm keys** in that run, so **N datapoints = N independent failing alarm keys = N independent alarm-config bugs**, not one shared root cause. Each failing key also **emails its owner** with a full traceback — so the fastest path to *which* key failed is that email, not CloudWatch Logs. This is a concrete instance of the [[oncall-investigation|oncall investigation discipline]].

## The alarm

A CloudWatch alarm on a custom **airflow-namespace** metric (no EC2/SQS dimensions):

| Field | Value |
|---|---|
| `Namespace` | `airflow` |
| `MetricName` | `airflow-alarm_provisioning_failures.sum` |
| `Statistic` | `Sum` |
| `Threshold` | `1.0` |
| `ComparisonOperator` | `GreaterThanOrEqualToThreshold` |
| `Period` | `3600` (s) |
| `EvaluationPeriods` | `1` |
| `DatapointsToAlarm` | `1` |
| `TreatMissingData` | `notBreaching` |
| `AlarmActions` | SNS `errors_volkscience_com` + PagerDuty |
| Alarm name | `[{priority}] Alarm Provisioning Failures`, priority default **P2** |

- *anchors:* `www/monitoring/alarms/ci/alarm_provisioning_failures_alarm.py:61-75` (namespace/metric/Sum/`>=`/NOT_BREACHING), `:48-59` (name template + P2 default). Registry entry `www/monitoring/alarm_manager.py:481` (`'alarm_provisioning_failures': AlarmProvisioningFailuresAlarm`), import `:151`.

The metric is **sparse** — `TreatMissingData = notBreaching` and the counter is only emitted on failure, so the series is mostly empty and shows one non-zero point per failing daily run. Because the alarm is `Sum >= 1` over a single 1h datapoint, **the datapoint value is the number of failing alarm keys** in that run.

## What the metric means — N datapoints = N independent bugs

The metric is the **Sum of the `alarm_provisioning_failures` counter** emitted by the daily `alarm_manager_alerts` DAG (runs in `create` mode). `main()` iterates every key in `ALARM_REGISTRY` and calls `manage_alarms(key, mode, ...)`. The counter is bumped **once per failing alarm key** at two distinct sites:

1. **Per-alarm-param failure** *inside* `manage_alarms` (a single `create_alarms`/`delete_alarms` raised): caught → `counters.add('alarm_provisioning_failures')` + `log.error('Failed to manage alarm params ... for <key>: <ex>')`. **No email.**
   - *anchor:* `www/monitoring/alarm_manager.py:922-934`.
2. **Per-alarm-key failure** in `main()` (the whole `manage_alarms` call raised): counter bumped, then it **emails the owner** — `subject = "[Action Needed] Alarm {key} provisioning is failing in region {region}"`, body includes `Exception: <ex>, traceback: <traceback.format_exc()>`, sent from `alerts@volkscience.com` to `get_owner_emails(key)`. In `dev` (`COUNTERS_NAMESPACE == 'dev'`) it only `log.warn`s — no email.
   - *anchor:* `www/monitoring/alarm_manager.py:956-972`.

So **each failing key is an independent config bug** — do not look for one shared cause behind a multi-datapoint firing.

## Owner routing

`get_owner_emails(key)` reads `ALARM_CONFIG.<key>.owner_emails`, defaulting to **`core-infra@eightfold.ai`** when none is set. A page whose email landed at that default address means the failing key has **no `owner_emails` configured** — route via the alarm file's [[../repo/codeowners-ownership|CODEOWNERS]] owner / git author instead.

- *anchor:* `www/monitoring/alarm_manager.py:870-875`.

## Investigation flow

The metric-first arc ([[oncall-investigation#shared-discipline|shared discipline]]), specialized:

1. **Read the alarm.** Note the region. Remember: N datapoints = N failing keys.
2. **Characterize the metric.** Pull the alarm definition, the (sparse) metric series, and the alarm state history. The `inspect-cloudwatch-metric` skill — written for EC2-CPU / SQS-depth alarms — **generalizes directly** to this airflow-namespace custom metric (no dimensions, Sum): its three entry commands (`describe-alarms`, `get-metric-statistics`, `pull_alarm_history.py`) work with just the namespace/metric swapped in. The metric value confirms the **count** of failing keys; the state history (`DescribeAlarmHistory`) tells you chronic vs rare (this family is typically intermittent — spaced days apart, firing in the daily-DAG run window).
3. **Enumerate the failing key(s) — prefer the email path.** The runbook gives two paths; **start with (a)**:
   - **(a) Gmail `[Action Needed] Alarm` notification** — search for subject `[Action Needed] Alarm <key> provisioning is failing in region <region>` (the per-key email from site 2 above). This gives the **failing key and the full traceback in one shot** — strictly more than the metric (which gives only the count). **Use this first.**
   - **(b) DAG logs** — fetch the Airflow `alarm_manager_alerts` DAG logs and grep for the string `[Action Needed] Alarm`. This is the **heavier fallback**: it needs CloudWatch-Logs access and log-group enumeration, and only some environments hold the `logs:FilterLogEvents` / `StartQuery` permissions. Reach for it only if the email is unavailable.
4. **Diagnose each failing key.** The email's traceback names the exact frames; read them to find the per-key cause. Where the cause is a suspected missing config entry, **confirm it against the live config directly** — see the root-cause shape below.
5. **Route.** To the key's `owner_emails`, or (when that defaulted to `core-infra@eightfold.ai`) to the alarm file's CODEOWNERS owner / git author.

## The witnessed root-cause shape — registered alarm with no config entry

The witnessed firing (`excess_log_volume`, eu-central-1) was a **registered alarm key with no `alarm_config` entry**. `alarm_base.get_alarm_params_config` reads `config.get('alarm_config', field_name='<key>')`, which returns **`None`** when the key was never added, then does an **un-guarded** `alarm_config.get(f'{config_key}::{region}')` → `AttributeError: 'NoneType' object has no attribute 'get'`. Two-part cause:

1. **Config gap** — the alarm key is registered in `ALARM_REGISTRY` (and iterated by the DAG) but has no `alarm_config.<key>` entry.
2. **Code fragility** — the unguarded null read surfaces the gap as an *opaque* `AttributeError` instead of the clean `Missing alarm_config::… entry` error the caller (`excess_log_volume_alarms.py`) intended to raise (the crash happens *before* that guard returns).

- *anchors:* `www/monitoring/alarm_base.py:384-390` (the unguarded read), `:21` (`ALARM_CONFIG = 'alarm_config'`).

**Confirm the config gap from the live source of truth** with a plain config read — `config.get('alarm_config', field_name='<key>')` returning `None` (and the key absent from the whole `alarm_config` dict) proves the entry is missing, a stronger confirmation than the traceback alone. Config is **broadcast to all regions**, so do **not** override the region — **use the `config-get` skill** and see [[../infra/config-get|Reading a config value]] for the full pattern and the region/IAM pitfalls. Fix = add the `alarm_config.<key>` entry (one entry covers every region, since config is broadcast).

> **Recurrence / standing pattern.** This family is **intermittent** — firings are spaced days apart, in the daily-DAG run window. The standing handling is ack and wait for the next clean daily run once the config is added; the page auto-resolves a few hours later. Known recurring offender shapes include exceeding CloudWatch's per-type metric cap and region/partition gaps — each still a *per-key* bug.

## Reporting the result

Report as a **detailed, table-structured report**, not prose — see the shared format on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For *Alarm Provisioning Failures* the tables are: alarm config; the failing-key list (one row per key = one datapoint, with its email/traceback); per-key root cause; and ownership/routing (key → `owner_emails` or alarm-file CODEOWNERS). To post it back to the PagerDuty thread, **use the `oncall-post-report` skill** (Canvas + concise threaded reply; confirm-before-post, plain-text non-paging references).

## Related skills

- `oncall-alarm-provisioning-failures` — the high-level runbook for this ticket type; start here to run the whole investigation (characterize → enumerate via email → confirm config gap → route). It pulls the page context via the external-context-puller skill at the start (see step 1 of the flow above).
- `inspect-cloudwatch-metric` — use it to pull the alarm definition, the sparse `airflow-alarm_provisioning_failures.sum` series (the failing-key count), and the alarm state history (chronic vs rare). It generalizes to this custom airflow-namespace Sum metric.
- `config-get` — use it to confirm the root-cause config gap (`config.get('alarm_config', field_name='<key>')` → `None`).
- `codeowners-owner` — use it to resolve the owning team/author of the failing alarm's source file when routing falls to the default owner.
- `oncall-post-report` — use it to post the finished table-structured report back to the PagerDuty Slack thread.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline and the ticket-type catalog.
- [[queue-backed-up|Queue backed up]] · [[solr-cpu-high|Solr CPU too high]] — the sibling ticket types.
- [[../infra/config-get|Reading a config value]] — the `config.get` read that confirms the missing-config root cause, with the broadcast/no-region/no-IAM rules.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the EC2-CPU alarm shape; this page is the airflow-custom-metric (no-dimension, Sum) variant of the same CloudWatch read pattern.
- [[../repo/codeowners-ownership|CODEOWNERS ownership resolution]] — op/file → owner routing for the failing alarm's source.

---
*Sources:* witness `inputs/2026-06-29-alarm-provisioning-failures.md` — `[18:58]` Slack thread + Confluence runbook (two enumeration paths); `[19:00]` driver code in `alarm_manager.py` + alarm class; `[19:01]` live `describe-alarms` / `get-metric-statistics` / `pull_alarm_history.py` (single failing key, intermittent cadence); `[19:03]` intervention — use the Gmail email path, not CW Logs; `[19:05]` Gmail enumerated `excess_log_volume` + traceback; `[19:06]` failing code path + CODEOWNERS routing; `[19:11]`–`[19:20]` confirming the config gap with a plain `config.get`. Source anchors confirmed against `www/monitoring/alarm_manager.py` and `www/monitoring/alarm_base.py` in the live tree.
