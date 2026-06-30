---
name: oncall-airflow-dag-failure
model: sonnet
description: High-level oncall runbook for an "[<region>] Airflow DAG Failure-<dag>" PagerDuty page — a per-DAG `airflow-airflow.<dag>.failed.sum` `Sum >= 1` / 900s / 1-of-1 CloudWatch counter alarm on the custom `airflow` namespace. Use when you pick up an "Airflow DAG Failure" page (e.g. "[us-west-2] Airflow DAG Failure-deploy_to_azure") and want the end-to-end investigation, not just one step — pull the PD thread + the Confluence "Airflow DAG Failure" runbook, characterize the `...failed.sum` metric + alarm state history, trace the failure → script process-exit-code → counter chain (the counter fires only on a non-zero exit; many exit-0 aborts never page), confirm the deploy via the build_log table for a deploy DAG, and route the failing source via CODEOWNERS. Distinct from "Alarm Provisioning Failures" (a different airflow metric). Sequences external-context-puller -> inspect-cloudwatch-metric -> (source trace) -> query-build-log -> codeowners-owner -> oncall-post-report. Reach for this whenever a per-DAG Airflow DAG Failure alarm pages.
knowledge_required:
  - "[[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]]"
knowledge_optional:
  - "[[../../../wiki/oncall/oncall-investigation|Oncall investigation — ticket types]]"
  - "[[../../../wiki/infra/build-log-table|build_log table (global db)]]"
  - "[[../../../wiki/infra/azure-app-deployer-resource-groups|Azure App Service deployer resource-group asymmetry]]"
---

# Oncall runbook — Airflow DAG Failure

The high-level flow for a `[<region>] Airflow DAG Failure-<dag>` PagerDuty page. The domain facts — the per-DAG `airflow-airflow.<dag>.failed.sum` `Sum >= 1` / 900s / 1-of-1 / `notBreaching` alarm, the **exit-code → counter mechanic** (`run_cron_script.sh` emits `...failed.sum` iff the wrapped script exits non-zero, `...success.sum` on exit 0; so many exit-0 aborts never page), the on-demand-DAG intermittency (`schedule_interval=None`, `retries=0`), and how it differs from *Alarm Provisioning Failures* — live in [[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure]]; this skill **sequences the building-block skills** and carries the runtime judgment between them. There **is** judgment between steps (which DAG/region, what the traceback shows, whether the failing run took a hard-exit/uncaught-exception path that pages vs an exit-0 abort that wouldn't, whether a deploy actually ran, who to route to), so read each step's output before the next.

> **The crux, before you explain anything:** the `...failed.sum` counter is keyed on the wrapped script's **process exit code**, not Airflow's notion of failure. The alarm fires on the **first non-zero-exit run** in any 15-min window (`Sum >= 1`, 1-of-1) — but a script can fail to do what was intended yet `sys.exit(0)` and never page. So "why doesn't this DAG page every time it misbehaves" is answered by *which exit path the run took*, and a clean re-run auto-resolves. See [[../../../wiki/oncall/airflow-dag-failure|Airflow DAG Failure]].

## Execution flow

1. **Pull the page context.** Read the PagerDuty/Slack alert thread and the Confluence **"Airflow DAG Failure"** runbook — **use the `external-context-puller` skill**. Note the **region** and the **DAG name** from the alarm (`[<region>] Airflow DAG Failure-<dag>`; the DAG file is `dag_<name>.py`). A peer auto-triage bot's RCA may be based on **no-CloudWatch-access** reads (its IAM principal, not yours) — confirm your own box can read CloudWatch and trust a real read over the bot's no-read RCA.
2. **Characterize the metric.** Pull the alarm definition, the `airflow-airflow.<dag>.failed.sum` series, and the alarm state history — **use the `inspect-cloudwatch-metric` skill**; its custom airflow-namespace **Sum** pattern generalizes directly (Namespace `airflow`, Sum, **no dimensions**, `>= 1` / 900s / 1-of-1 / `notBreaching`). Pull the **`...success.sum`** series alongside to show the success-vs-failure background (the DAG runs constantly and mostly succeeds; the failures are the rare exit-non-zero runs). The state history tells you chronic vs rare (this family is intermittent, auto-resolving).
3. **Trace the failure → exit-code → counter path.** The counter comes from `run_cron_script.sh` keyed on the wrapped script's exit code. Confirm whether the failing run took a **hard `sys.exit(1)` / uncaught-exception** path (which pages) vs an exit-0 abort (which wouldn't). Get the actual traceback from the PD thread's auto-triage analysis, the Confluence runbook's **error-email** (subject `[Airflow <region>] Failure for script <name>, status: 1`), or the region-specific Airflow V2 web UI (Grid View → task → Logs). For `deploy_to_azure` the witnessed shape was an **uncaught `ResourceNotFound`** from `AzureAppDeployer.deploy_cluster`'s first SDK call (`app_service_utils.py:552`) — note that a wrong-resource-group site is a structurally-supported alternate cause of the same error ([[../../../wiki/infra/azure-app-deployer-resource-groups|RG asymmetry]]).
4. **Confirm the deploy actually ran (deploy DAGs).** For a deploy DAG, confirm what was being deployed from the **`build_log`** table on the global db — **use the `query-build-log` skill** (match `--namespace`/`--tag`/`--start`/`--end` in the failure window, then `--full` for the one matched id's `data_json`). Line its `t_create` up against the alarm datapoint to fit the timeline. (`build_log` is the source of truth; deploy alerts land in `#build_alerts`.)
5. **Route.** Resolve ownership of the failing source files — **use the `codeowners-owner` skill**. For `deploy_to_azure`: the deploy script `production/release/deploy_azure_server.py` → core-infrastructure + app-infra; the DAG `scripts/airflow_v2/dags-us/dag_deploy_to_azure.py` → app-infra; the deployer util `www/utils/app_service_utils.py` → **no matching CODEOWNERS rule** (route via the deploy script's owner or git author). The immediate next step for an on-demand-deploy failure is to ping the engineer who triggered the run (the PD assignee); a clean re-run against a valid target auto-resolves the page.
6. **Report, and post if asked.** Assemble the table-structured report (below). When asked to post it to Slack, **use the `oncall-post-report` skill** — it drafts **both** a concise reply and the full report and asks which to post, confirms the destination, and renders owner/customer names as plain text so the post pages no one. Do **not** post unless the user asks, and obtain the user's own approval of the wording before posting.

## What to report

Deliver a **detailed, table-structured report** — not a prose summary — following the shared format on [[../../../wiki/oncall/oncall-investigation#reporting-an-oncall-ticket|Oncall investigation → reporting]]. Use a table per section:

1. **Alarm** — name, region, backing metric (`airflow-airflow.<dag>.failed.sum`, Sum, `>= 1`, 900s, 1-of-1, `notBreaching`), state, and how chronic (this trigger / prior trigger / gap).
2. **Failing run** — DAG, region, execution date, run_id, the traceback head; whether it was a hard-exit / uncaught-exception (paged) vs an exit-0 abort.
3. **Root cause** — the failing code path and which exit path it took; for a deploy DAG, the resource that was missing/unfound (and the wrong-RG alternate, if applicable).
4. **Confirmed deploy** — the `build_log` row: id / namespace / tag / git_revision / status.
5. **Ownership / routing** — file → CODEOWNERS owner / git author; the proposed next step (re-run against a valid target).
6. **Timeline** — the key timestamps in one place, all UTC.

## Constituent skills (each independently usable)

- `external-context-puller` — step 1, pull the PagerDuty/Slack thread + the Confluence "Airflow DAG Failure" runbook.
- `inspect-cloudwatch-metric` — step 2, the alarm definition + `airflow-airflow.<dag>.failed.sum` (and `...success.sum`) series + state history; generalizes to this custom airflow-namespace Sum metric.
- `query-build-log` — step 4, confirm the deploy from the `build_log` table on the global db.
- `codeowners-owner` — step 5, resolve the owning team/author of the failing DAG/deploy source files.
- `oncall-post-report` — step 6 (optional), post the finished report back to the PagerDuty Slack thread.
</content>
