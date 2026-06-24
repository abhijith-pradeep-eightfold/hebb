# user_logins shard 0 CPU check

**Task:** Check current CPU utilization of the Solr shard 0 host for the `user_logins` core. Resolve hostname via solr-shard-dns-lookup, pull CloudWatch CPU timeseries via inspect-cloudwatch-cpu, report spike window and shape.

## Log

### [21:29] wiki-reader
- **observed:** Consulted wiki for Solr/user_logins/CloudWatch domain context. Wiki has pages for solr-shard-dns-lookup, solr-collection-topology, and cloudwatch-cpu-alarm. Key facts extracted: `user_login` collection uses `hosts_key = user_login_shard_hosts` (default `{tablename}_shard_hosts` pattern, no override); shard IDs are non-contiguous so must enumerate from config before assuming shard 0 exists; standard pipeline is DNS → InstanceId → CloudWatch; CloudWatch alarm threshold is 75% Average over 5-of-6 × 300s periods; CloudWatch timestamps are UTC.

### [21:31] solr-shard-dns-lookup
- **observed:** Initial run with `--collection user_logins` failed with "not in SEARCH_INDEX_SETTINGS_REGISTRY"; correct name is `user_login`. Re-ran with `--collection user_login --shard-id 0`. Shard 0 exists; available shards for user_login in us-west-2 are 0, 38, 46, 79. Two replicas returned: replica_0_dns=ec2-44-250-46-179.us-west-2.compute.amazonaws.com, replica_1_dns=ec2-34-209-212-233.us-west-2.compute.amazonaws.com. InstanceIds resolved via coordinator-relayed user confirmation: replica 0 → i-04f3156a5fb6270e9, replica 1 → i-06713fd6b04cd9e45.

### [21:32] inspect-cloudwatch-cpu
- **observed:** Pulled 6-hour CloudWatch CPUUtilization window (2026-06-24T15:30:00Z to 2026-06-24T21:25:00Z), period 60s, statistics Average+Maximum, for both replicas. InstanceIds already in hand from DNS lookup step — skipped describe-alarms. Replica 0 (i-04f3156a5fb6270e9): 72 buckets, min=3.9% max=10.2% mean=6.2%, 0 buckets >= 75% threshold, no sustained breach block identified. Replica 1 (i-06713fd6b04cd9e45): 72 buckets, min=1.3% max=5.9% mean=3.0%, 0 buckets >= 75% threshold, no sustained breach block identified. Both replicas in low-load range for the entire window.
- **script:** analyze_cpu_metrics.py (bundled in inspect-cloudwatch-cpu skill), scratch JSON at scratchpad/cpu_r0.json and scratchpad/cpu_r1.json

### [21:38] skill-fix: solr-shard-dns-lookup approval gate removed
- **observed:** Skill was stopping to surface `aws ec2 describe-instances` commands for user approval before resolving DNS → InstanceId. Extended `get_shard_hosts.py` to call `describe-instances` internally via subprocess (non-fatal: emits UNKNOWN on error and continues); updated SKILL.md to remove approval-gate language, renumber steps to 1-4, add `--no-resolve` flag for AWS-unreachable cases. Verified script runs end-to-end for user_login shard 0: both InstanceIds resolved (i-04f3156a5fb6270e9, i-06713fd6b04cd9e45) without any approval prompt. Published via core/tools/publish.py.
- **user input:** "You need to call the scripts in the skill if they exist, that means no user approvals. Otherwise you need to update the wiki so that it happens next time."

### [21:38] skill-writer core fix: no-approval rule added
- **observed:** Added explicit rule to `core/skills/maintainer/skill-writer/SKILL.md` Step 6: bundled skills must never add user-approval gates for any calls (including read-only AWS CLI calls) unless the user explicitly asked for one. Rule also says existing approval gates that were not explicitly requested should be removed as a capability fix (Rule A4). Updated wiki page `wiki/solr/solr-shard-dns-lookup.md` "Resolving DNS hostnames" section to reflect that `describe-instances` is now bundled in the script and runs unattended; updated "Full pipeline" steps to show one-step lookup. Published via core/tools/publish.py.
- **user input:** "Lets not add approvals for script: add it to skill-writer also. Unless explicitly asked to add by the user."

## Session summary

**What was done:**

1. Consulted wiki (wiki-reader) for Solr/user_login/CloudWatch domain context — pages present and complete.
2. Ran solr-shard-dns-lookup for `user_login` shard 0 (collection name correction: `user_logins` → `user_login`). Confirmed shard 0 exists; available shards 0, 38, 46, 79. Two replicas: ec2-44-250-46-179 (i-04f3156a5fb6270e9) and ec2-34-209-212-233 (i-06713fd6b04cd9e45).
3. Ran inspect-cloudwatch-cpu for both replicas over a 6-hour window (2026-06-24T15:30Z–21:25Z). Both replicas idle: replica 0 min=3.9% max=10.2% mean=6.2%, replica 1 min=1.3% max=5.9% mean=3.0%. Zero buckets >= 75% threshold. No spike window to report.
4. Skill fix (user-directed): `get_shard_hosts.py` extended to resolve DNS → InstanceId internally via subprocess; approval gate removed from `solr-shard-dns-lookup` SKILL.md; `--no-resolve` flag added for AWS-unreachable cases.
5. Generator fix (user-directed): added no-approval rule to `core/skills/maintainer/skill-writer/SKILL.md` Step 6; updated wiki page `wiki/solr/solr-shard-dns-lookup.md` to reflect bundled InstanceId resolution.

**Final result:** `user_login` shard 0 is healthy as of 2026-06-24 ~21:30 UTC. No spike. Skill and wiki updated so the DNS→InstanceId step runs unattended in future sessions. No alternative approach was validated (user declined).
