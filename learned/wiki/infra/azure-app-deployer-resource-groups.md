# Azure App Service deployer resource-group asymmetry

**Summary:** `AzureAppDeployer` (`www/utils/app_service_utils.py`) handles two kinds of Azure resource lookup **asymmetrically**: **App Service Plan / autoscale** lookups iterate a list of candidate resource groups (because plans live in either group due to historical naming inconsistencies), but **site / slot** lookups are pinned to a single **hardcoded** resource group with **no fallback**. A site provisioned into the *other* resource group therefore yields a spurious `ResourceNotFound` from a site lookup even though it exists. This makes "site exists but in the wrong resource group" a fully plausible cause of a deploy `ResourceNotFound` — the same symptom as a genuinely-absent site.

## The two resource groups

The codebase explicitly acknowledges a **two-resource-group world**, owing to historical naming inconsistencies (the single Azure region is `westus2`, but the default RG name carries a `west-2` suffix):

- `POSSIBLE_PLAN_RESOURCE_GROUPS = ['eightfold-infra-resource-group-west-2', 'eightfold-infra-resource-group-westus2']`, with a class docstring noting "Our App Service Plans may live in **either** of these resource groups due to **historical naming inconsistencies**."
- *anchor:* `www/utils/app_service_utils.py:23-29`.

Relevant environment facts: `AZURE_DEFAULT_REGION=westus2`, `AZURE_ALL_REGIONS=westus2`, `AZURE_RESOURCE_GROUP_NAME=eightfold-infra-resource-group-west-2`. The deployer reads its service-principal credentials from AWS Secrets Manager via `get_secret(Secrets.get('AZURE_CD_CREDS'))` (`:53-64`).

## The asymmetry

| Lookup kind | Resource group resolution | Fallback? |
|---|---|---|
| **Plan / autoscale** | iterates `POSSIBLE_PLAN_RESOURCE_GROUPS` (both groups), and derives the RG from the actual plan resource-id | yes — both RGs tried |
| **Site / slot** | always `self.resource_group`, **hardcoded** to `eightfold-infra-resource-group-west-2` at `__init__` (`:47`) and **never reassigned** anywhere in the file | **no** — only `...west-2` |

- `self.resource_group` is set once at `__init__` (`:47`) and is the sole assignment of that attribute in the file (a grep for `self.resource_group\s*=` returns only `:47`).
- **All** web_apps / site / slot calls use this single hardcoded RG with no fallback: `get_configuration` (`:552` — the first SDK call in `deploy_cluster`, and the witnessed failing call), `web_apps.get` (`:108`, `:667`), `list_slots` (`:110`), `get_configuration_slot` (`:316`), slot create/swap/start/stop (`:323`, `:353`, `:421`, `:444`).
- By contrast, the plan/autoscale lookups iterate both RGs (`_get_autoscale_capacity` at `:116-120`, `:228-229`, `:489`, `:762-763`) and derive the RG from the plan resource-id (`_extract_plan_info` at `:95-104`).

## Consequence — wrong-RG `ResourceNotFound`

If an App Service site (e.g. `stage0-<app>6`) were provisioned into `eightfold-infra-resource-group-westus2` rather than `...west-2`, then `get_configuration(self.resource_group=...west-2, '<site>')` (`:552`) raises `ResourceNotFound` **even though the site exists** in the other group. This is **indistinguishable from a genuinely-absent site** by source inspection alone — distinguishing the two needs a live (read-only) Azure lookup of where the site actually lives.

The deploy path does **not** create sites (no `web_apps.begin_create_or_update`) — it deploys to **pre-existing** sites only — so a site provisioned into the wrong RG cannot be auto-created by the deploy; it just fails to be found.

> **Status of the wrong-RG hypothesis:** **source-supported, not confirmed against live Azure.** The hardcoded-site-RG-vs-iterated-plan-RG asymmetry is fully anchored in source. Whether a *specific* failing site actually lives in `...westus2` was **not** confirmed against live Azure in the witnessed incident (the responder had `az` CLI + SP creds available but the user declined touching prod Azure). Treat "site present in the wrong RG" as a plausible-but-unverified alternative to "site absent", not as established fact.

## Suggested durable fix (on record for Core Infra / App-Infra)

Make the **site / slot** lookups resolve the resource group the way the **plan** lookups already do — iterate the candidate RGs (`POSSIBLE_PLAN_RESOURCE_GROUPS`) or derive the site's actual RG — instead of pinning `self.resource_group` to `...west-2` (`:47`). That removes the asymmetry fault: a site provisioned into `...westus2` would still be found, turning a spurious `ResourceNotFound` deploy failure into a successful lookup.

## Related skills

- `oncall-airflow-dag-failure` — the runbook for the `deploy_to_azure` Airflow DAG Failure page; it names this RG asymmetry as the structurally-supported alternate cause of the deploy's `ResourceNotFound`.

## Related

- [[../oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]] — the `deploy_to_azure` page whose witnessed `ResourceNotFound` surfaced this asymmetry; the deployer is invoked from `production/release/deploy_azure_server.py`.
- [[build-log-table|build_log table (global db)]] — confirm which app/revision a deploy actually ran (the deploy that hit the `ResourceNotFound`).
- [[../repo/codeowners-ownership|CODEOWNERS ownership resolution]] — `www/utils/app_service_utils.py` matches **no** CODEOWNERS rule (no owner); route via the deploy script (`/production/` → core-infrastructure + app-infra) or git author.

---
*Sources:* witness `inputs/2026-06-30-airflow-dag-failure-deploy-to-azure.md` — `[10:58]`/`[11:03]` Q3 resource-group derivation traced from source (`app_service_utils.py:47` hardcoded site RG, sole assignment; `:23-29` `POSSIBLE_PLAN_RESOURCE_GROUPS` + historical-naming docstring; `:95-104` `_extract_plan_info`; `:116-120`/`:228-229`/`:489`/`:762-763` plan/autoscale iterate both RGs; site/slot calls `:108`,`:110`,`:316`,`:323`,`:353`,`:421`,`:444`,`:552`,`:667` all on `self.resource_group`; `:53-64` SP creds from AWS Secrets Manager); `[11:02]` user declined live Azure check, so the wrong-RG hypothesis stays source-supported but not live-confirmed.
</content>
</invoke>
