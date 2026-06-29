---
name: solr-shard-dns-lookup
model: sonnet
description: Look up the EC2 DNS hostnames and InstanceIds for all replicas of a Solr shard, given any collection name in SEARCH_INDEX_SETTINGS_REGISTRY (e.g. profiles, positions, user_calendar_events, user_login, courses, org_units, etc.) and a shard ID. Use this whenever a task identifies a Solr shard by collection + shard number but does not yet have a hostname or EC2 instance — e.g. "find the host for positions shard 7", "which EC2 instance serves user_calendar_events shard 0", "look up shard hosts from search_config", "get EC2 InstanceId for Solr shard N". This is the right first step before a CloudWatch CPU pull (use inspect-cloudwatch-metric next) or any task that needs to locate the physical host behind a shard. Do NOT use when you already have a CloudWatch alarm name or an InstanceId — go straight to inspect-cloudwatch-metric in that case.
---

# Solr shard DNS lookup

Retrieve the live EC2 DNS hostnames and InstanceIds for every replica of a Solr shard from `search_config` in `$CODE_BASE`. The lookup facts live in the wiki ([[../../../wiki/solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]]); the runtime judgment this skill carries is **which collection, which shard, and which region** to look up. The entire pipeline — config read and DNS→InstanceId resolution — is handled by a **bundled script** — `scripts/get_shard_hosts.py` — that runs unattended with no user approval required.

## When to use this skill

- You know the collection name and a shard number, but you don't yet have a hostname or EC2 instance. The collection can be any entry in `SEARCH_INDEX_SETTINGS_REGISTRY` — `profiles`, `positions`, `user_calendar_events`, `user_login`, `courses`, `org_units`, and others.
- You need to verify which shard IDs actually exist for a collection (shard numbering is not contiguous — always let the script enumerate the available IDs rather than assuming sequential numbering).
- You are about to pull CloudWatch CPU for a specific shard but are starting from "collection + shard ID" rather than a PagerDuty alarm.

If you already have a CloudWatch alarm name or an InstanceId, skip this skill and go directly to **`inspect-cloudwatch-metric`**.

## How the config key is determined

The bundled script uses `SEARCH_INDEX_SETTINGS_REGISTRY` (in `www/search/search_index_settings.py`) to derive the right `hosts_key` for any collection — you do not need to know it in advance. The registry applies the default pattern `{tablename}_shard_hosts`; `profiles` and `positions` are special-cased to `shard_hosts` and `position_shard_hosts` respectively. See [[../../../wiki/solr/solr-shard-dns-lookup|wiki]] for the full derivation table.

**Always confirm the collection name before proceeding.** A task framed as "profiles shard 9" may actually mean the `positions` collection (different shard space), and a collection like `user_calendar_events` has a completely separate shard set.

## Steps

### 1. Read the wiki (brief)

Skim [[../../../wiki/solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]] to confirm the region and key for this task. The wiki also has the full lookup pattern if you need it for reference.

### 2. Run the bundled script (DNS hostnames + InstanceIds, no approval needed)

Run the bundled script to get the replica DNS hostnames and EC2 InstanceIds for the requested shard. The script reads `$CODE_BASE` config and then resolves each DNS hostname to an InstanceId via a read-only EC2 `describe-instances` call — all bundled, no approval required:

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/get_shard_hosts.py" --collection <collection-name> --shard-id <N>
```

- `PYTHONPATH="$CODE_BASE/www"` (not `$CODE_BASE`) — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
- Add `--region <region>` if the target is not the default (`EF_DEFAULT_REGION` = `us-west-2`).
- Add `--no-resolve` to skip the AWS InstanceId resolution and emit only DNS hostnames (e.g. when AWS is unreachable).
- If the shard doesn't exist, the script exits 1 and prints the available shard IDs — report this to the user and confirm the correct shard before proceeding.
- If InstanceId resolution fails for a replica (e.g. `AccessDenied`), the script emits `UNKNOWN` for that replica and continues — it does not exit 1 for AWS errors.

The script outputs machine-readable `key=value` lines (one `replica_N_dns` and `replica_N_instance_id` per replica) followed by a human-readable summary.

### 3. Report

Present a table using the script's output:

| Replica | DNS hostname | InstanceId |
|---|---|---|
| 0 | ec2-xx-xx-xx-xx... | i-... |
| 1 | ec2-yy-yy-yy-yy... | i-... |

Include the collection, shard_id, and region so downstream steps have the full context. If any InstanceId is `UNKNOWN`, note the AWS error from stderr and report plainly — do not halt the task.

### 4. (Optional) Proceed to CloudWatch CPU pull

If the goal is to check CPU utilization, hand the InstanceIds to **`inspect-cloudwatch-metric`** (starting from its "already have InstanceIds" entry point — skip `describe-alarms`). See [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]].

> If the task is simply **"the CPU of `<collection>` shard `<N>`"**, you don't need to run this skill and `inspect-cloudwatch-metric` by hand — the combined **`solr-shard-cpu`** skill runs this lookup and the per-replica CPU pull in one call. This skill stays the right choice when you need only the hosts/InstanceIds.

## Notes

- **Shard IDs are not contiguous.** Always let the script enumerate available shards rather than assuming sequential IDs.
- **Do not hardcode hostnames or InstanceIds.** Replica-to-host assignments change when instances are replaced or re-balanced. Always run the lookup fresh.
- **AWS errors are non-fatal.** If `describe-instances` is denied or returns no results, the script emits `UNKNOWN` and continues. Report this to the user but do not stop the task.
