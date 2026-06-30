# Airflow DAG Failure (oncall ticket type)

**Summary:** A PagerDuty `[<region>] Airflow DAG Failure-<dag>` page fires when a specific Airflow DAG's per-DAG **failure counter** crosses `Sum >= 1` in any 15-minute window. The backing metric is `airflow-airflow.<dag>.failed.sum` on the custom **`airflow`** namespace — emitted by the DAG's shell wrapper **keyed on the wrapped script's process exit code** (non-zero → `...failed.sum`, zero → `...success.sum`). So the page fires on the **very first non-zero-exit run** of that DAG in a window — but only for runs that actually exit non-zero, which is why many "failures" never page. This is a concrete instance of the [[oncall-investigation|oncall investigation discipline]], and is **distinct** from [[alarm-provisioning-failures|Alarm Provisioning Failures]] (the daily `alarm_manager_alerts` DAG's provisioning counter — a different metric and a different failure semantics; see *Not to be confused with* below).

## The alarm

A CloudWatch alarm on a custom **airflow-namespace** metric (no EC2/SQS dimensions), one alarm per DAG:

| Field | Value |
|---|---|
| Alarm name | `[<region>] Airflow DAG Failure-<dag>` (e.g. `[us-west-2] Airflow DAG Failure-deploy_to_azure`) |
| `Namespace` | `airflow` |
| `MetricName` | `airflow-airflow.<dag>.failed.sum` (e.g. `airflow-airflow.deploy_to_azure.failed.sum`) |
| `Statistic` | `Sum` |
| `Dimensions` | `[]` (none) |
| `Threshold` | `1.0` |
| `ComparisonOperator` | `GreaterThanOrEqualToThreshold` |
| `Period` | `900` (s) |
| `EvaluationPeriods` | `1` |
| `DatapointsToAlarm` | `1` |
| `TreatMissingData` | `notBreaching` |
| `AlarmActions` | SNS `errors_volkscience_com` + PagerDuty |

- The `AlarmDescription` typically points at the **Core Infra PagerDuty Playbook** (Confluence `Core Infra Playbook - PagerDuty`), and the Confluence **"Airflow DAG Failure"** runbook gives the prescribed debug steps (below).
- **Exact trigger — pages on the first failure.** `Sum >= 1` over a single 900s (15-min) bucket, `1`-of-`1`, `notBreaching` → a single 15-min window whose summed `...failed.sum` counter is `>= 1` flips OK→ALARM. **No N-of-M smoothing**: one failed run in any 15-min window pages immediately. The Confluence runbook notes you *could* set `evaluation_periods = datapoints_to_alarm = R+1` to suppress retried-then-succeeded runs, but a DAG with `retries=0` has no retries to absorb, so the alarm is left at 1-of-1.
- *anchors:* alarm config read live from CloudWatch via `describe-alarms`; the metric is emitted from `scripts/airflow_v2/scripts/run_cron_script.sh` (see the exit-code mechanic below).

This is the **same Sum-counter family** as [[alarm-provisioning-failures|Alarm Provisioning Failures]], but a **different metric** (`airflow-airflow.<dag>.failed.sum`, per-DAG-task-failure) with **no email-per-key path** — it routes straight to the `errors_volkscience_com` SNS + PagerDuty.

## The exit-code → counter mechanic (the crux)

The `...failed.sum` counter is **not** driven by Airflow's notion of task failure — it is keyed on the wrapped **deploy/cron script's process exit code**:

1. The DAG's worker task is a **BashOperator** running `run_cron_script.sh <script_name>_{{run_id}}`.
2. `run_cron_script.sh` runs the script under `timeout`, captures `RETURN_CODE=$?`, then sets `status="success"` if `RETURN_CODE == 0` else `"failed"`, and emits:
   `aws cloudwatch put-metric-data --namespace $namespace --metric-name airflow-airflow.$AIRFLOW_SCRIPT_NAME.$status.sum --value 1`.
   So **`...failed.sum` is emitted iff the script exited non-zero**; an exit-0 run emits `...success.sum` instead. (`RETURN_CODE == 125` → `...import_error.sum`; `137` → SIGKILL/timeout email.)
3. The BashOperator's `on_failure_callback = failure_on_timeout_alert` emits the counter **only when the exception is an `AirflowTaskTimeout`** — for a *normal* failure it does nothing, because the shell wrapper already emitted the counter (avoiding a duplicate). (A PythonOperator task instead uses `failure_alert`, which emits on any failure — there is no shell wrapper underneath it.)
4. **Region/namespace branch (latent gap):** the shell scripts do `if EF_DEFAULT_REGION == AZURE_DEFAULT_REGION → namespace="azure-airflow"` (else `"airflow"`). The alarm watches namespace **`airflow`**. On a host whose `EF_DEFAULT_REGION` is the Azure region, the counter would land in `azure-airflow` and **never reach the alarm** — a latent blind spot. (Empirically, for `deploy_to_azure` in us-west-2 the `azure-airflow` namespace had **0** datapoints, so the counter lands in `airflow` as the alarm expects.)

- *anchors:* `scripts/airflow_v2/scripts/run_cron_script.sh:~145-163` (RETURN_CODE → status → `put-metric-data`; 125→import_error, 137→timeout email; namespace branch); `scripts/airflow_v2/scripts/add_counter_metrics.sh:~22-32` (the callback path's `put-metric-data` + same namespace branch); `scripts/airflow_v2/utils/callbacks.py:3-5` (docstring: which callback emits when), `:31-38` (timeout-only emit), `:40-45` (any-failure emit).

### Why a "failure" doesn't always page

Because the counter is keyed on the **process exit code**, a script can fail to do what was intended yet exit `0` and never page. In the `deploy_to_azure` deploy script (`production/release/deploy_azure_server.py`):

- **`sys.exit(0)` (counted as SUCCESS → NO page):** commit older than 7 days (`:112`), `skip_deployment` set (`:139`), antidote not contained (`:158`), and **even when a cluster actually failed to deploy** (`:199-200`).
- **`sys.exit(1)` (→ `...failed.sum` → page):** empty git email (`:99`), unauthorized user (`:104`), invalid commit (`:39`), antidote unexpected failure (`:54`).
- **An uncaught exception** (e.g. `ResourceNotFound` from the Azure SDK) is a non-zero exit → page.

Empirically, over 06-15→06-30 the `deploy_to_azure` DAG showed **8 `...failed.sum` buckets** (each `Sum=1`, matching the 8 alarm firings 1:1) against **162 `...success.sum` buckets** — the DAG runs dozens of times daily and overwhelmingly succeeds; failures are the rare exit-non-zero exception that pages, while the many exit-0 aborts are invisible to the alarm.

- *anchors:* `production/release/deploy_azure_server.py:39,54,99,104` (exit-1 paths), `:112,139,158,199-200` (exit-0 paths counted as success); failed-vs-success series from live `get-metric-statistics` over namespace `airflow`.

## Why it's intermittent — on-demand DAGs

Many of these DAGs are **on-demand / human-triggered**, not scheduled:

- `dag_deploy_to_azure` has `schedule_interval=None` → it does **not** run on a schedule; it runs only when a human triggers a deploy (run_id `manual__...`, with `dag_run.conf['args']` carrying environment / application / revision / regions / cluster_id). It cannot "fail every day" — it only fires when someone deploys.
- `retries=0` → a single task failure pages immediately, no retry masking.
- Each run supplies its **own target**; the deploy fails only when *that run's* inputs are bad (missing/stale target, stale commit, bad cluster_id). So the same operator+script trips different human-deploy mistakes on different runs → intermittent, days apart, and a clean re-run passes.

- *anchors:* `scripts/airflow_v2/dags-us/dag_deploy_to_azure.py:20` (retries=0), `:23` (schedule_interval=None), `:25-46` (`get_args` reads `dag_run.conf['args']`), `:59-65` (BashOperator → `run_cron_script.sh`).

## Chronic vs rare

CloudWatch retains alarm history for ~14 days. For `deploy_to_azure` the 14-day window showed **7 OK→ALARM→OK transitions** (each auto-resolves between firings) — corroborating "intermittent, recurring, auto-resolving". The standing handling is **ack and re-run against a valid target**; the next clean run auto-resolves the page, with no infra mutation. Confirm a longer history via PagerDuty (the in-window cadence under-counts because of the ~14-day retention).

## Investigation flow

The metric-first arc ([[oncall-investigation#shared-discipline|shared discipline]]), specialized for a per-DAG failure alarm:

1. **Pull the page context.** Read the PagerDuty/Slack thread and the Confluence "Airflow DAG Failure" runbook — use the external-context-puller skill. Note the **region** and the **DAG name** from the alarm (the DAG file is `dag_<name>.py`).
2. **Characterize the metric.** Pull the alarm definition, the `airflow-airflow.<dag>.failed.sum` series, and the alarm state history — **use the `inspect-cloudwatch-metric` skill**; its airflow-namespace Sum pattern generalizes directly (Namespace `airflow`, Sum, no dimensions, `>= 1` / 900s / 1-of-1 / `notBreaching`). The series tells you which 15-min bucket(s) failed; the state history tells you chronic vs rare. Pulling the **`...success.sum`** series alongside `...failed.sum` shows the success-vs-failure background (the DAG runs constantly and mostly succeeds).
3. **Map the failure to the failing code path.** Trace DAG → BashOperator → `run_cron_script.sh` → the wrapped script. Confirm whether the failing run took a **hard `sys.exit(1)` / uncaught-exception** path (which pages) vs an exit-0 abort (which would not). The Confluence runbook's **error-email** path (subject `[Airflow <region>] Failure for script <name>, status: 1`) and the region-specific Airflow V2 web UI (Grid View → task → Logs) hold the actual traceback when the PD thread's auto-triage analysis doesn't already supply it.
4. **Route.** Resolve ownership of the failing source files — **use the `codeowners-owner` skill**. For `deploy_to_azure`: the deploy script `production/release/deploy_azure_server.py` → `@EightfoldAI/core-infrastructure @EightfoldAI/app-infra`; the DAG `scripts/airflow_v2/dags-us/dag_deploy_to_azure.py` → `@EightfoldAI/app-infra`; the deployer util `www/utils/app_service_utils.py` → **no matching CODEOWNERS rule** (no owner). Consistent with the PD Service = Core Infra. The immediate next step for an on-demand-deploy failure is to ping the engineer who triggered the run (the PD assignee).
5. **Confirm a deploy actually happened (when the page is a deploy DAG).** The deployment record is the **[[../infra/build-log-table|`build_log` table]]** on the **global** db, not the Slack alert feed — **use the `query-build-log` skill** to find the matching `namespace`/`tag`/`status`/`git_revision` row in the window. Deploy alerts land in `#build_alerts` (the deploy script's own `azure-deployments` Slack notification is a separate, secondary channel).
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — confirm-before-post, plain-text non-paging references. Do **not** post unless the user asks.

## The witnessed root-cause shape — uncaught `ResourceNotFound` on an Azure deploy

The witnessed `deploy_to_azure` firing was an **uncaught `ResourceNotFound`** from the first Azure SDK call in the deploy. `AzureAppDeployer.deploy_cluster`'s first call is `self.client.web_apps.get_configuration(self.resource_group, cluster['cluster_name'])` (`app_service_utils.py:552`), executed **before any try/except**. The `cluster_name` is built as `'%s%s-%s%s' % (environment, cluster_id, application, stack_version)` (`deploy_azure_server.py:165`). If the App Service site is not found in `self.resource_group`, the SDK raises `ResourceNotFound`, which propagates uncaught out of `deploy_cluster` → `deploy()` → the script → the bash wrapper exits 1 → `...failed.sum` +1 → page.

There is a **second, structurally-supported candidate for the same `ResourceNotFound`**: the site may exist but in a **different resource group**. The deployer pins site/slot lookups to a hardcoded resource group while plan lookups iterate candidate groups — see [[../infra/azure-app-deployer-resource-groups|Azure App Service deployer resource-group asymmetry]]. "Site genuinely absent" and "site present in the wrong RG" are **indistinguishable from source alone**; distinguishing them needs a live (read-only) Azure lookup of where the site actually lives.

- *anchors:* `www/utils/app_service_utils.py:552` (the unguarded first `get_configuration`), `:543` (`deploy_cluster`), `:627` (`deploy`); `production/release/deploy_azure_server.py:165` (cluster_name), `:171-175` (`AzureAppDeployer(...).deploy()`).

## Not to be confused with — Alarm Provisioning Failures

Both are `airflow`-namespace `Sum >= 1` CloudWatch alarms, but they are **different ticket types**:

| | **Airflow DAG Failure** (this page) | [[alarm-provisioning-failures|Alarm Provisioning Failures]] |
|---|---|---|
| Metric | `airflow-airflow.<dag>.failed.sum` (per-DAG) | `airflow-alarm_provisioning_failures.sum` |
| Meaning of a datapoint | a 15-min bucket had `>= 1` non-zero-exit run of that DAG | the count of **failing alarm keys** in the daily `alarm_manager_alerts` run (N datapoints = N independent config bugs) |
| Period | 900s | 3600s |
| Enumeration path | error-email by script name / Airflow UI logs / PD auto-triage; deploy DAGs → `build_log` | per-key `[Action Needed] Alarm` owner email |
| Source of failure | the wrapped DAG script's process exit code | a per-key `manage_alarms` raise inside the daily DAG |

## Reporting the result

Report as a **detailed, table-structured report** — not prose — per the shared format on [[oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. For *Airflow DAG Failure* the tables are: alarm config (metric, Sum `>= 1`/900s/1-of-1, state, chronic-vs-rare); the failing run (DAG, region, execution date, run_id, the traceback head); root cause (the failing code path + which exit path it took); confirmed deploy (the `build_log` row: id / namespace / tag / git_revision / status); and ownership/routing (file → CODEOWNERS owner). To post it back to the PD thread, **use the `oncall-post-report` skill**.

## Related skills

- `oncall-airflow-dag-failure` — the high-level runbook for this ticket type; start here to run the whole investigation (pull context → characterize the `...failed.sum` metric → trace the failure→exit-code→counter path → confirm the deploy via `build_log` → route). It pulls the page context via the external-context-puller skill at the start (step 1 of the flow above).
- `inspect-cloudwatch-metric` — pull the alarm definition, the `airflow-airflow.<dag>.failed.sum` (and `...success.sum`) series, and the alarm state history; it generalizes to this custom airflow-namespace Sum metric.
- `query-build-log` — confirm the deploy that the DAG ran from the `build_log` table on the global db (match `namespace`/`tag`/`git_revision`/`status` in the window).
- `codeowners-owner` — resolve the owning team/author of the deploy script / DAG / deployer-util files when routing.
- `oncall-post-report` — post the finished table-structured report back to the PagerDuty Slack thread.

## Related

- [[oncall-investigation|Oncall investigation — ticket types]] — the umbrella discipline and the ticket-type catalog.
- [[alarm-provisioning-failures|Alarm Provisioning Failures]] — the sibling airflow-namespace ticket type; *different* metric and semantics (see *Not to be confused with* above).
- [[../infra/azure-app-deployer-resource-groups|Azure App Service deployer resource-group asymmetry]] — the site-RG-pinned-vs-plan-RG-iterated code asymmetry that makes a wrong-RG `ResourceNotFound` plausible for an Azure deploy.
- [[../infra/build-log-table|build_log table (global db)]] — the deployment record (namespace / tag / git_revision / status / data_json), the source of truth for "was a deploy actually triggered".
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the EC2-CPU alarm shape; this page is the airflow-custom-metric (no-dimension, Sum, per-DAG) variant of the same CloudWatch read pattern.
- [[../repo/codeowners-ownership|CODEOWNERS ownership resolution]] — file → owner routing for the failing DAG/deploy source.

---
*Sources:* witness `inputs/2026-06-30-airflow-dag-failure-deploy-to-azure.md` — `[10:25]` no existing wiki page for this ticket type; `[10:26]` PD thread + Confluence "Airflow DAG Failure" runbook (page 1600127059); `[10:30]` live `describe-alarms` + `pull_alarm_history.py` (per-DAG `airflow-airflow.deploy_to_azure.failed.sum`, `Sum >= 1`/900s/1-of-1/`notBreaching`, 7 firings/14d); `[10:34]` deploy script + DAG wrapper + the failing `get_configuration` at `app_service_utils.py:552`; `[10:35]` CODEOWNERS routing; `[10:58]`/`[11:08]` the alarm-trigger precision, on-demand intermittency, and the exit-code→counter chain (`run_cron_script.sh`, `callbacks.py`); failed-vs-success metric series (8 failed vs 162 success buckets over 06-15→06-30). Source anchors cited above against `scripts/airflow_v2/`, `production/release/deploy_azure_server.py`, `www/utils/app_service_utils.py`.
</content>
</invoke>
