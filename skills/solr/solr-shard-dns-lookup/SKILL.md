---
name: solr-shard-dns-lookup
description: Look up the EC2 DNS hostnames and InstanceIds for all replicas of a Solr shard, given a collection name (profiles or positions) and a shard ID. Use this whenever a task identifies a Solr shard by collection + shard number but does not yet have a hostname or EC2 instance — e.g. "find the host for positions shard 7", "which EC2 instance serves profiles shard 3", "look up shard hosts from search_config", "get EC2 InstanceId for Solr shard N". This is the right first step before a CloudWatch CPU pull (use inspect-cloudwatch-cpu next) or any task that needs to locate the physical host behind a shard. Do NOT use when you already have a CloudWatch alarm name or an InstanceId — go straight to inspect-cloudwatch-cpu in that case.
---

# Solr shard DNS lookup

Retrieve the live EC2 DNS hostnames for every replica of a Solr shard from `search_config` in `$CODE_BASE`, then resolve each hostname to an EC2 InstanceId for downstream use (CloudWatch, SSH, etc.). The lookup facts live in the wiki ([[../../../wiki/solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]]); the runtime judgment this skill carries is **which collection, which shard, and which region** to look up. The config read is deterministic and is handled by a **bundled script** — `scripts/get_shard_hosts.py` — that runs unattended. The follow-up DNS→InstanceId AWS calls are read-only but require user approval.

## When to use this skill

- You know the collection (`profiles` or `positions`) and a shard number, but you don't yet have a hostname or EC2 instance.
- You need to verify which shard IDs actually exist for a collection (shard numbering is not contiguous — `positions` in us-west-2 has shards 0–7, 38, 46, 79; shard 9 does not exist).
- You are about to pull CloudWatch CPU for a specific shard but are starting from "collection + shard ID" rather than a PagerDuty alarm.

If you already have a CloudWatch alarm name or an InstanceId, skip this skill and go directly to **`inspect-cloudwatch-cpu`**.

## Key distinction: profiles vs. positions config key

The two collections use different keys in `search_config` (see [[../../../wiki/solr/solr-shard-dns-lookup|wiki]] and `www/search/search_constants.py`):

| Collection | Key | `search_constants` constant |
|---|---|---|
| positions | `position_shard_hosts` | `POSITION_HOSTS` (line 48) |
| profiles | `shard_hosts` | `PROFILE_HOSTS` (line 54) |

A task framed as "profiles shard 9" may actually mean the `positions` collection. **Confirm the collection before proceeding.**

## Steps

### 1. Read the wiki (brief)

Skim [[../../../wiki/solr/solr-shard-dns-lookup|Solr shard DNS lookup via search_config]] to confirm the region and key for this task. The wiki also has the full lookup pattern if you need it for reference.

### 2. Run the bundled config lookup

Run the bundled script to get the replica DNS hostnames for the requested shard. This only reads `$CODE_BASE` config — no AWS call, no approval needed:

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/get_shard_hosts.py" --collection <profiles|positions> --shard-id <N>
```

- `PYTHONPATH="$CODE_BASE/www"` (not `$CODE_BASE`) — see [[../../../wiki/vscode-repo/python-import-root|Python import root]].
- Add `--region <region>` if the target is not the default (`EF_DEFAULT_REGION` = `us-west-2`).
- If the shard doesn't exist, the script exits 1 and prints the available shard IDs — report this to the user and confirm the correct shard before proceeding.

The script outputs machine-readable `key=value` lines (one per replica DNS) followed by a human-readable summary.

### 3. Resolve DNS → EC2 InstanceId (user approval required)

For each replica DNS hostname from step 2, resolve it to an EC2 InstanceId. Surface each command to the user before running:

```bash
aws ec2 describe-instances --region us-west-2 \
  --filters "Name=dns-name,Values=<ec2-xx-xx-xx-xx.us-west-2.compute.amazonaws.com>" \
  --query "Reservations[*].Instances[*].InstanceId" --output text
```

Run one command per replica. Collect the `i-...` InstanceId for each.

> **Note:** EC2 `describe-instances` with a DNS filter is a read-only call, but the bash execution policy still gates it because it is not a bundled-script path. Surface the command and run it on user approval.

### 4. Report

Present a table:

| Replica | DNS hostname | InstanceId |
|---|---|---|
| 0 | ec2-xx-xx-xx-xx... | i-... |
| 1 | ec2-yy-yy-yy-yy... | i-... |

Include the collection, shard_id, and region so downstream steps have the full context.

### 5. (Optional) Proceed to CloudWatch CPU pull

If the goal is to check CPU utilization, hand the InstanceIds to **`inspect-cloudwatch-cpu`** (starting from its Step 0 / "if you start from DNS" path — you already have the InstanceIds, so skip `describe-alarms`). See [[../../../wiki/infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]].

## Notes

- **Shard IDs are not contiguous.** Always let the script enumerate available shards rather than assuming sequential IDs.
- **Do not hardcode hostnames or InstanceIds.** Replica-to-host assignments change when instances are replaced or re-balanced. Always run the lookup fresh.
- **Reachability check for AWS.** `aws ec2 describe-instances` can only be confirmed by making the call; env inspection alone (profile present, region set) doesn't prove `ec2:DescribeInstances` is authorized. Report plainly if access is denied rather than guessing.
