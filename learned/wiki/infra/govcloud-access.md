# GovCloud (us-gov-west-1) access

**Summary:** GovCloud is a **separate AWS partition** (`aws-us-gov`), not just another region. The default commercial credentials cannot reach it; AWS calls into `us-gov-west-1` need the dedicated `GOV_AWS_*` credential pair. CloudWatch, RDS, Performance Insights, and CloudWatch Logs are reachable directly from the agent box with those creds; the GovCloud **data warehouse** (Redshift log warehouse) is **not** reachable from the agent box and must be read in-region (via `pssh shared-gov`) using the model's region-agnostic warehouse path.

## The partition split

`us-gov-west-1` lives in the `aws-us-gov` partition with its own account and IAM. The environment's default `AWS_ACCESS_KEY_ID` is a **commercial-partition** key (account `948299231917`) and **will not authenticate** against GovCloud — using it yields signing/access failures, not data. GovCloud has its own credential pair shipped in the environment:

- `GOV_AWS_ACCESS_KEY_ID` / `GOV_AWS_SECRET_ACCESS_KEY` — the GovCloud creds.
- The GovCloud account id is exported as `US_GOV_WEST_1_AWS_ACCOUNT_ID` (`095104455888`).

Confirm reachability with a caller-identity check using the GOV creds and the gov region:

```bash
export AWS_ACCESS_KEY_ID="$GOV_AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$GOV_AWS_SECRET_ACCESS_KEY"
aws sts get-caller-identity --region us-gov-west-1
# -> arn:aws-us-gov:iam::095104455888:user/<you>
```

ARNs in GovCloud use the `aws-us-gov` partition prefix (e.g. `arn:aws-us-gov:iam::…`). These are read-only telemetry reads — run them unattended; reachability is only knowable by trying, so make the call and report plainly if it is denied.

## What IS reachable from the agent box (with GOV creds)

With `GOV_AWS_*` exported and `--region us-gov-west-1`, the following AWS APIs answer directly from the agent box — no in-region hop needed:

- **CloudWatch** — `describe-alarms`, `get-metric-statistics`, `describe-alarm-history` (e.g. the `AWS/RDS CPUUtilization` alarm — see [[../oncall/rds-cpu-high|RDS CPU too high]]).
- **RDS** — `describe-db-clusters`, `describe-db-instances` (topology, the `DbiResourceId` needed for Performance Insights).
- **Performance Insights** — `pi get-resource-metrics`, `pi get-dimension-key-details` (see [[rds-performance-insights|RDS Performance Insights]]).
- **CloudWatch Logs Insights** — `logs start-query` / `logs get-query-results` against the in-region log groups (e.g. the `Processor` log group, ~1.5 TB, readable).

The same access patterns documented for the commercial partition in [[cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] apply verbatim once the GOV creds + gov region are set — only the namespace/dimension change with the resource type.

## What is NOT reachable from the agent box — the warehouse

The GovCloud **Redshift log warehouse** (the home of `processor_event_log` in gov) is **not** reachable from the agent box, and the bundled processor tooling cannot reach it either:

- The `trace-processor-op` / `query-processor-event-log` skills and their shared util `hebb_utils.processor.event_log` are **StarRocks-only by construction** — supported regions are `us-west-2`, `eu-central-1`, `ca-central-1`, `ap-southeast-2`. They **reject** `us-gov-west-1` (`region 'us-gov-west-1' is not a StarRocks region`). See [[../processor/tracing-processor-op-lineage|tracing processor-op lineage]].

### The region-agnostic warehouse read path

The model itself resolves the physical warehouse **region-agnostically**, so a read written against the model's `dwh` path works in GovCloud (where the log warehouse is Redshift, not StarRocks) when `EF_DEFAULT_REGION=us-gov-west-1`:

- `ProcessorLogEvent._db_type = dwh.get_db_type_override(DBType.REDSHIFT_LOG.value)` (`www/db/base_log_event.py:202`; `REDSHIFT_LOG = 'redshift_log'` at `www/db/db_type.py:15`).
- table name: `dwh.get_db_tablename_with_schema_prefix('processor_event_log', db_type=db_type)` (`www/db/base_log_event.py:206,213,298`; `www/cloud_interfaces/datawarehouse.py:126`).
- rows: `dwh.get_list(query, db_type=db_type)` → `DataWarehouseAdapterFactory.create(db_type=…)` (`www/cloud_interfaces/datawarehouse.py:87`; `get_db_type_override` at `:111`).

`get_db_type_override(REDSHIFT_LOG)` resolves to GovCloud's actual log warehouse when run in-region. See [[../data-warehouse/datawarehouse-adapter-factory|DataWarehouseAdapterFactory]] and [[../processor/processor-event-log|processor_event_log table]].

### Running an in-region read: `pssh shared-gov`

To read the gov warehouse, run the model-native `dwh` read **on an in-region box** that has the gov network and credentials. The shared gov box is reached by `pssh shared-gov`:

- **`pssh` is a shell alias**, not a binary on PATH: `alias pssh="python $REPO_HOME/scripts/aws/ssh.py"` (`dotfiles/.bashrc:352`).
- `scripts/aws/ssh.py` dispatches its first argument (a **logical host name**) against several maps; `HOSTNAME_DEV` is checked first (`scripts/aws/ssh.py:275`), sourced from `config.get('pssh_config')['HOSTNAME_DEV']` (`:256`). `HOSTNAME_ADMIN` (`:320-322`, map at `:232-251`) holds the gov jump boxes (`gov`, `airflow-gov`, `proxy-gov`).
- The logical host `shared-gov` is a `HOSTNAME_DEV` key resolving to an EC2 instance in `us-gov-west-1`. Resolve it with the `config-get` skill: `config.get('pssh_config', field_name='HOSTNAME_DEV')` returns an OrderedDict including `shared-gov` (and `shared-gov-old`, `shared-gov-wfx`, …). **Do not record the instance id here — it is ephemeral; look it up.**
- `_do_ssh` (`scripts/aws/ssh.py:61-82`) builds an **interactive** `ssh -q -t … bash -l` login shell, jumping via the airflow/gov admin host when cross-region.

**Boundary — the agent sandbox cannot drive this.** The interactive `pssh` login shell cannot be driven by the non-interactive agent Bash tool. Reconstructing `ssh.py`'s prod-key-fetch + gov-jump routing to inject a non-interactive remote command is a **materially more invasive action** than the interactive login a user runs, and is **not** an action the agent is authorized to auto-drive from its sandbox. The correct shape is a **hand-off**: prepare a self-contained, read-only script (the model-native `dwh` read above) and have the user (or their interactive `pssh shared-gov` session) run it in-region. The warehouse trace is corroboration; it does not change a root cause already confirmed from Performance Insights query tags + the code path.

## Related skills

- `config-get` — use it to resolve a `pssh` logical host (e.g. `config.get('pssh_config', field_name='HOSTNAME_DEV')`) or any global-config value; config is broadcast to all regions, so read it plainly with the box's own creds (do not override `EF_DEFAULT_REGION`).
- `inspect-cloudwatch-metric` — use it to pull a GovCloud CloudWatch alarm + metric (e.g. the RDS writer CPU curve) once the GOV creds + gov region are set.
- `query-rds-performance-insights` — use it to decompose RDS load in GovCloud (PI is reachable from the agent box with the GOV creds + gov region).
- `oncall-rds-cpu-high` — the high-level RDS-CPU runbook; many of those alarms are in GovCloud, so it sets the GOV creds before any AWS read.

## Related

- [[cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the read-only AWS CLI access pattern; the same calls work in GovCloud with the GOV creds + gov region (only the namespace/dimension change per resource type).
- [[rds-performance-insights|RDS Performance Insights]] — the PI load-split, reachable in GovCloud from the agent box with the GOV creds.
- [[../oncall/rds-cpu-high|RDS CPU too high]] — the oncall ticket type that surfaced GovCloud access.
- [[config-get|Reading a config value (`config.get`)]] — resolves the `pssh_config` host maps; config is broadcast to all regions.
- [[../processor/tracing-processor-op-lineage|Tracing processor-op lineage]] — the bundled tracer is StarRocks/commercial-only and cannot reach the gov warehouse.

---
*Sources:* witness `inputs/2026-06-29-rds-cpu-alarm-triage.md` (`[19:58]` GOV creds + partition + `sts get-caller-identity`; `[20:46]` StarRocks-only tracer rejects gov + the model-native `dwh` path; `[20:54]`/`[20:58]` the `pssh` alias + `ssh.py` dispatch; `[21:02]` `pssh_config.HOSTNAME_DEV['shared-gov']` resolution + the agent-sandbox interactivity boundary; `[21:24]` the auto-ssh-injection denial; `[20:55]` GovCloud `Processor` Logs-Insights reachable). Code anchors: `www/db/base_log_event.py:202,206,213,298`, `www/cloud_interfaces/datawarehouse.py:87,111,126`, `www/db/db_type.py:15`, `scripts/aws/ssh.py:61-82,232-251,256,275,320-322`, `dotfiles/.bashrc:352`.
