---
name: config-get
model: sonnet
description: Read a value from the www global config via config.get — the minimal `from config import config; config.get('<config_name>', field_name='<field>')` read, run with the box's own credentials against the global config DB. Use whenever a task needs to read a config value from $CODE_BASE — "what is config X", "read the alarm_config entry for key Y", "does config Z have field F", "confirm a config entry exists / is missing", "what keys are in <config_name>", "resolve a pssh host (config.get('pssh_config')['HOSTNAME_DEV'])", "what instance does shared-gov map to", "read search_group_mappings for tenant T". Reach for this for ANY config.get read instead of writing a raw `python -c "from config import config; config.get(...)"` by hand. Encodes the critical lesson that config is BROADCAST to all regions, so you read it plainly — do NOT override EF_DEFAULT_REGION to "read a region's partition" and do NOT add any IAM / assume-role / STS handling (those overrides cause SignatureDoesNotMatch -> AccessDenied dead-ends). A missing field returns None.
knowledge_required:
  - "[[../../../wiki/infra/config-get|Reading a config value (config.get)]]"
knowledge_optional:
  - "[[../../../wiki/vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]]"
  - "[[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures (oncall)]]"
  - "[[../../../wiki/infra/govcloud-access|GovCloud access]]"
  - "[[../../../wiki/ats/ats-entity-cache|ats_entity_cache write path]]"
---

# Read a config value (`config.get`)

Read any value from the `www` global config. The capability is deliberately **minimal** — a plain `from config import config; config.get(config_name, field_name=...)` — and the whole reason this skill exists is to keep it minimal: the durable facts (config is **broadcast to all regions**; it reaches the global config DB with the **box's own credentials, no assume-role**) live in [[../../../wiki/infra/config-get|Reading a config value]]. The deterministic read is a **bundled script**, `scripts/read_config.py`, that runs unattended.

## The rules this skill enforces (do not re-derive them)

- **Config is broadcast to every region.** A plain read from the box's default environment (us-west-2 signing) reflects every region's value. There is no need to point the read at a region.
- **Do NOT set `EF_DEFAULT_REGION`** to "read region X's partition." Importing `config` triggers an import-time STS `get_caller_identity`; an override pushes it to a region the box's creds don't sign for → `SignatureDoesNotMatch`.
- **Do NOT add any IAM / assume-role / STS / `CURRENT_IAM_USER` handling.** The read reaches the global config DB with the box's own creds directly. Patching around the (self-inflicted) signing error only produces a bogus `AccessDenied` on `secrets-manager-ro` and a wrong "config DB unreachable" conclusion.
- A **missing field** returns `None` (this is exactly what an unguarded downstream `.get()` then crashes on). Partitioned configs (e.g. `alarm_config`) are read the same plain way — do **not** append `::<region>`.

## Run it

`config` lives under `www`, so root the import at `$CODE_BASE/www` (see [[../../../wiki/vscode-repo/python-import-root|Python import root]]) and run with the box's **default** environment — no region override:

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/read_config.py" "$@"
```

Arguments:
- `<config_name>` (positional) — e.g. `alarm_config`.
- `--field-name FIELD` (`-f`) — read a single field; prints the value and whether it is `None`.
- `--has KEY` — when the resolved value is a dict, also report whether `KEY` is present.

Examples:
- `… read_config.py alarm_config --field-name excess_log_volume` → the field's value, or `None` if absent (confirms a missing-`alarm_config`-entry root cause — see [[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures]]).
- `… read_config.py alarm_config --has excess_log_volume` → the full key list + whether the key is present.

## Related

- [[../../../wiki/infra/config-get|Reading a config value (config.get)]] — the broadcast/no-region/no-IAM facts and the source anchors.
- [[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures]] — the oncall ticket type that uses this read to confirm its root cause.
