# Solr shard DNS lookup via search_config

**Summary:** How to retrieve the EC2 DNS hostnames for a given Solr shard's replicas from the live `search_config` in `$CODE_BASE`, and how to resolve those hostnames to EC2 InstanceIds for use in CloudWatch. This is the starting point when you know a collection and shard ID but do not yet have a CloudWatch alarm or InstanceId.

## The two shard-host config keys

The `search_config` namespace holds **two separate shard-host maps**, one per collection type. The keys are defined in `www/search/search_constants.py`:

| Collection | Config key | `search_constants` constant | Line |
|---|---|---|---|
| **Positions** (job postings) | `position_shard_hosts` | `search_constants.POSITION_HOSTS` | 48 |
| **Profiles** (candidates) | `shard_hosts` | `search_constants.PROFILE_HOSTS` | 54 |

The namespace itself: `search_constants.SEARCH_CONFIG = 'search_config'` (line 78).

These are easy to confuse — a task framed as "profiles shard 9" may actually mean the positions collection (key `position_shard_hosts`), and vice versa. **Always confirm which collection and which config key before looking up shard IDs.**

## Lookup pattern

```python
import os
from config import config
from search import search_constants
from utils.os_constants import EF_DEFAULT_REGION

region = EF_DEFAULT_REGION  # resolves to 'us-west-2' in the agent environment
search_cfg = config.get(search_constants.SEARCH_CONFIG, region=region)

# For positions:
shard_hosts = search_cfg[search_constants.POSITION_HOSTS]  # 'position_shard_hosts'
# For profiles:
# shard_hosts = search_cfg[search_constants.PROFILE_HOSTS]  # 'shard_hosts'

# List available shard IDs:
available_shards = list(shard_hosts.keys())

# Get replica DNS hostnames for a specific shard (shard_id is an int, key is a string):
replica_dns_list = shard_hosts[str(shard_id)]  # list indexed by replica number
```

`EF_DEFAULT_REGION` is defined in `www/utils/os_constants.py` line 8 as `os.getenv('EF_DEFAULT_REGION')`, which resolves to `us-west-2` in the agent environment.

Scripts that import `www` packages need `PYTHONPATH=$CODE_BASE/www` (not `$CODE_BASE`) — see [[../vscode-repo/python-import-root|Python import root]].

## Shard numbering is not contiguous

Shard IDs are **not sequential** — the positions collection in us-west-2 has shards: `0, 1, 2, 3, 4, 5, 6, 7, 38, 46, 79`. Shard 9 does not exist. Always enumerate `shard_hosts.keys()` to discover the real shard ID list before assuming a shard exists.

> *Source: `inputs/2026-06-24-profiles-shard9-cpu.md` `[20:31]` — position_shard_hosts keys in us-west-2 confirmed by script.*

## Resolving DNS hostnames to EC2 InstanceIds

Once you have DNS hostnames from `search_config`, resolve each to an EC2 InstanceId for use in CloudWatch metric pulls (see [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — Step 2 requires an InstanceId, not a hostname):

```bash
aws ec2 describe-instances --region us-west-2 \
  --filters "Name=dns-name,Values=<ec2-xx-xx-xx-xx.us-west-2.compute.amazonaws.com>" \
  --query "Reservations[*].Instances[*].InstanceId" --output text
```

- The filter key is `dns-name`; the value is the full EC2 public DNS hostname exactly as returned by `search_config`.
- Run one call per replica DNS hostname.
- The result (`i-...`) is the InstanceId you pass to `aws cloudwatch get-metric-statistics --dimensions Name=InstanceId,Value=<i-...>`.

This is the **forward direction** (DNS → InstanceId). The reverse direction (InstanceId → DNS) is documented in [[solr-collection-topology|Solr collection topology]].

## Full pipeline: collection + shard_id → CPU metrics

When you know the collection and shard ID and need the CPU curve:

1. Use the lookup pattern above to get replica DNS hostnames from `search_config`.
2. Use `aws ec2 describe-instances` to resolve each DNS → InstanceId (this section).
3. Pull CPUUtilization per InstanceId via CloudWatch — [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] Steps 2–4.

## Related

- [[solr-collection-topology|Solr collection topology]] — how alarm names map to shard/replica/host; the reverse DNS lookup (InstanceId → hostname); replica traffic semantics.
- [[../infra/cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — pull the CPU timeseries once you have InstanceIds.
- [[../vscode-repo/python-import-root|Python import root ($CODE_BASE/www)]] — PYTHONPATH convention for scripts that import `www` packages.

---
*Sources:* witness `inputs/2026-06-24-profiles-shard9-cpu.md` (`[20:31]` config lookup script, available shard keys, DNS→InstanceId via describe-instances); confirmed against `www/search/search_constants.py` lines 48, 54, 78 and `www/utils/os_constants.py` line 8.
