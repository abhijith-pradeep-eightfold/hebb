# Solr shard DNS lookup via search_config

**Summary:** How to retrieve the EC2 DNS hostnames for a given Solr shard's replicas from the live `search_config` in `$CODE_BASE`, and how to resolve those hostnames to EC2 InstanceIds for use in CloudWatch. This is the starting point when you know a collection and shard ID but do not yet have a CloudWatch alarm or InstanceId.

## The general mechanism: SearchIndexSettings and SEARCH_INDEX_SETTINGS_REGISTRY

The right way to look up the `search_config` key for any Solr collection is through `SEARCH_INDEX_SETTINGS_REGISTRY` in `www/search/search_index_settings.py`. This registry maps every human-facing collection name to a `SearchIndexSettings` instance, which exposes the correct `hosts_key` for that collection.

```python
from search.search_index_settings import SEARCH_INDEX_SETTINGS_REGISTRY
from search import search_constants
from config import config

settings = SEARCH_INDEX_SETTINGS_REGISTRY[collection_name]  # e.g. 'user_calendar_events'
hosts_key = settings.hosts_key                              # e.g. 'user_calendar_events_shard_hosts'
search_cfg = config.get(search_constants.SEARCH_CONFIG, region=region)
shard_hosts = search_cfg[hosts_key]
```

### How hosts_key is derived

`SearchIndexSettings.__init__` (line 6 of `search_index_settings.py`) sets the default:

```
self.hosts_key = '{tablename}_shard_hosts'
```

Two collections override this default via hard-coded branches:

| Collection key | `tablename` | `hosts_key` | Source |
|---|---|---|---|
| `profiles` | `candidate_profiles` | `shard_hosts` | override, lines 17–22 |
| `positions` | `sourcing_profiles` | `position_shard_hosts` | override, lines 23–30 |
| `suggestions` | `suggestions` | `suggestions_hosts` | override, line 32–33 |
| **all others** | same as collection key | `<tablename>_shard_hosts` | default (line 6) |

So `user_calendar_events` (tablename = `user_calendar_events`, no override) gets `hosts_key = 'user_calendar_events_shard_hosts'` — confirmed present in live `search_config` for us-west-2. Similarly, `user_login` (tablename = `user_login`) gets `hosts_key = 'user_login_shard_hosts'`.

> **Collection-name trap:** the registry key is `user_login`, not `user_logins` (no trailing `s`). Passing `user_logins` to the script or the registry raises "not in SEARCH_INDEX_SETTINGS_REGISTRY". Always use the exact registry key — check the list below when in doubt.

### Registry entries (as of search_index_settings.py lines 36–56)

`profiles`, `positions`, `user_login`, `courses`, `profile_feedback`, `planned_event`, `suggestions`, `user_calendar_events`, `career_graph`, `config_description`, `offers`, `org_units`, `form_submissions`, `admin_assistant_entities`, `air_document_index`, `question_template`, `agentic_conversations`.

### instant search index

Several collections also have an instant-search index alongside their shard index. The `instant_hosts_key` follows the pattern `instant_{tablename}_hosts`. `user_calendar_events` appears in `INSTANT_SEARCH_CORES` in `www/search/search_constants.py` (lines 38–40) as `instant_user_calendar_events`.

## The two special-case config keys for profiles and positions

For the two most-used collections the `hosts_key` constants are also available directly via `search_constants`:

| Collection | `hosts_key` | `search_constants` constant | Line |
|---|---|---|---|
| **Positions** (job postings) | `position_shard_hosts` | `search_constants.POSITION_HOSTS` | 48 |
| **Profiles** (candidates) | `shard_hosts` | `search_constants.PROFILE_HOSTS` | 54 |

The namespace itself: `search_constants.SEARCH_CONFIG = 'search_config'` (line 78).

These are easy to confuse — a task framed as "profiles shard 9" may actually mean the positions collection. **Always confirm which collection before looking up shard IDs.**

## Shard numbering is not contiguous

Shard IDs are **not sequential** — examples in us-west-2:

- `positions`: `0, 1, 2, 3, 4, 5, 6, 7, 38, 46, 79` (shard 9 does not exist)
- `user_login`: `0, 38, 46, 79` (sparse; most integers in between do not exist)

Always enumerate `shard_hosts.keys()` to discover the real shard ID list before assuming a shard exists. The script exits 1 with the available shard list if the requested shard ID is not found.

> *Sources: `inputs/2026-06-24-profiles-shard9-cpu.md` `[20:31]` — position_shard_hosts keys in us-west-2 confirmed by script; `inputs/2026-06-24-user-logins-shard0-cpu.md` `[21:31]` — user_login available shards confirmed by script.*

## Resolving DNS hostnames to EC2 InstanceIds

The `solr-shard-dns-lookup` skill's bundled script (`scripts/get_shard_hosts.py`) resolves DNS hostnames to InstanceIds automatically as part of its single run — no separate call needed. It calls `aws ec2 describe-instances` internally per replica:

```bash
aws ec2 describe-instances --region us-west-2 \
  --filters "Name=dns-name,Values=<ec2-xx-xx-xx-xx.us-west-2.compute.amazonaws.com>" \
  --query "Reservations[*].Instances[*].InstanceId" --output json
```

- The filter key is `dns-name`; the value is the full EC2 public DNS hostname exactly as returned by `search_config`.
- The result (`i-...`) is the InstanceId passed to `aws cloudwatch get-metric-statistics --dimensions Name=InstanceId,Value=<i-...>`.
- The script emits `UNKNOWN` for any replica where the AWS call fails (e.g. `AccessDenied`, no match) and continues without exiting 1.
- Pass `--no-resolve` to skip InstanceId resolution and emit only DNS hostnames (e.g. when AWS is unreachable).
- The script output includes both `replica_N_dns` and `replica_N_instance_id` fields per replica.

This is the **forward direction** (DNS → InstanceId). The reverse direction (InstanceId → DNS) is documented in [[solr-collection-topology|Solr collection topology]].

## Full pipeline: collection + shard_id → CPU metrics

When you know the collection and shard ID and need the CPU curve:

1. Run `solr-shard-dns-lookup` bundled script — it returns both DNS hostnames and InstanceIds in one unattended run.
2. Pull CPUUtilization per InstanceId via CloudWatch — [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] Steps 2–4.

## Related

- [[solr-collection-topology|Solr collection topology]] — how alarm names map to shard/replica/host; the reverse DNS lookup (InstanceId → hostname); replica traffic semantics.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — pull the CPU timeseries once you have InstanceIds.
- [[../vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — PYTHONPATH convention for scripts that import `www` packages.

---
*Sources:* witness `inputs/2026-06-24-profiles-shard9-cpu.md` (`[20:31]` config lookup script, available shard keys, DNS→InstanceId via describe-instances); witness `inputs/2026-06-24-solr-shard0-cpu.md` (`[21:03]` user_calendar_events registry lookup, hosts_key derivation, confirmed key in live search_config); confirmed against `www/search/search_index_settings.py` lines 3–56, `www/search/search_constants.py` lines 38–40, 48, 54, 78 and `www/utils/os_constants.py` line 8.
