# Reading a config value (`config.get`)

**Summary:** How to read any value from the `www` global config from the agent environment — the minimal `from config import config; config.get('<config_name>', field_name='<field>')` pattern. The durable lesson: **config is broadcast to all regions**, so a plain read from the box's default environment reflects every region's value — do **not** override `EF_DEFAULT_REGION` and do **not** add IAM/assume-role handling (both cause self-inflicted signing/access dead-ends).

## The read pattern

```python
from config import config

v = config.get('alarm_config', field_name='excess_log_volume')
# v is the field's value, or None if that field is not present in the config
```

- `config.get(*args, **kwargs)` delegates straight to `get_internal` / `_get`. Signature: positional **`config_name`**, plus kwargs **`field_name`**, **`default`**, **`region`** (defaults to `os.getenv('EF_DEFAULT_REGION')`). The source carries an explicit *"Please don't make any changes to this method"* note — it is the stable public entry point.
  - *anchor:* `www/config/config.py:822-823` (`get(*args, **kwargs) -> get_internal`).
- A **missing field** returns `None` (unless a `default=` is passed). This is exactly the value that makes an unguarded downstream `.get()` raise `AttributeError: 'NoneType' object has no attribute 'get'` — see [[../oncall/alarm-provisioning-failures|Alarm Provisioning Failures]] for the witnessed instance of that crash.

### Run it with the www import root and the box's default env

Run with **`PYTHONPATH="$CODE_BASE/www"`** (config lives under `www/`, like the other `www`-level packages — see [[../vscode-repo/python-import-root|Python import root]]) and the **box's default environment** (us-west-2 signing):

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" read_config.py
```

The read connects to the **global config DB** (`mysql+pymysql ... global-database-cluster-1...us-west-2.rds.amazonaws.com/global`) using the **box's own credentials — no assume-role is needed**.

## Config is broadcast to all regions — read it plainly

Config changes are **broadcast to every region**: each region holds a copy of every region's config. So a plain `config.get(name, field_name=...)` from the default (us-west-2-signed) environment reflects **every region's** value — there is no need to point the read at a specific region to "see" that region's config.

Two anti-patterns, both observed to backfire this session:

- **Do NOT set `EF_DEFAULT_REGION`** to "read region X's partition." Importing `config` triggers a module-init STS `get_caller_identity` call (via `boto_utils.get_current_iam_user`); with `EF_DEFAULT_REGION=eu-central-1` the STS client signs for eu-central-1 while the box's creds are scoped to us-west-2 → `SignatureDoesNotMatch`.
  - *anchor:* `www/utils/boto_utils.py:2731-2733` (`get_current_iam_user` → STS `get_caller_identity`).
- **Do NOT add IAM / assume-role / `CURRENT_IAM_USER` pre-seed handling.** Patching around the signing error above led to fetching the `GLOBAL_VSDB_URI` secret via an assume-role of `secrets-manager-ro`, which the box's user is denied → `AccessDenied ... not authorized to perform: sts:AssumeRole on resource: .../secrets-manager-ro`, and a wrong "config DB unreachable" conclusion. **None of this is needed** — a plain `config.get` reaches the global config DB with the box's own creds.
  - *anchors:* `www/db/db_connection.py:150` (`_fetch_db_secret → secrets.get_secret('GLOBAL_VSDB_URI')`), `www/utils/boto_utils.py:616` (`get_session_credentials → assume_role(... secrets-manager-ro ...)` — the denied call that only appears when you over-build it).

Strip all of it: a plain `from config import config; config.get(...)` works.

## Partitioned configs

Some configs are **partitioned** — internally namespaced by a partition key with separator `::` (e.g. `'<config_name>::<partition>'`). `alarm_config` is one such partitioned config (reading it logs `Loading the entire partitioned config alarm_config`). **Even for a partitioned config, a plain `field_name=` read resolves correctly** (because of the broadcast model above); you do not append `::<region>`. A missing field still returns `None`.

- *anchors:* `www/config/config.py:57` (`NAMESPACE_SEP = '::'`), `:75-79` (`build_partition_id_namespace`), `:190-195` (`is_config_partitioned` / `is_config_regional`).

## Related skills

- `config-get` — use it to read a config value (`config.get(config_name, field_name=...)`) from the live global config DB. It encodes the broadcast/no-region/no-IAM rules above so you don't re-derive them; pass a `config_name` and optional `field_name`.
- `oncall-alarm-provisioning-failures` — the oncall runbook that uses this read to confirm a missing-`alarm_config`-entry root cause.

## Related

- [[../vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — why this read runs with `PYTHONPATH="$CODE_BASE/www"`.
- [[../oncall/alarm-provisioning-failures|Alarm Provisioning Failures]] — the oncall ticket type whose root cause (a registered alarm key with no `alarm_config` entry) is confirmed with exactly this read.
- [[cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the sibling "telemetry read from the box" pattern; note the contrast: CloudWatch reads use the box's creds **directly with no STS**, while config reaches the global DB (also with the box's own creds, no assume-role) — neither needs a region override.

---
*Sources:* witness `inputs/2026-06-29-alarm-provisioning-failures.md` (`[19:14]` config.py partition machinery + import-time STS snag; `[19:15]` broadcast-model correction; `[19:17]` the self-inflicted `secrets-manager-ro` `AccessDenied`; `[19:19]` "stop over-complicating — plain `import config; config.get`"; `[19:20]` clean minimal read confirming the box's own creds reach the global config DB with no assume-role). Confirmed against `www/config/config.py:822` and `www/monitoring/alarm_base.py:12,384` in the live tree.
