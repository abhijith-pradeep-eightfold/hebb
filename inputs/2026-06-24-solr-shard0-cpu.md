# Solr shard 0 DNS lookup and CPU timeseries

**Task:** Look up the DNS hostname for Solr shard 0 (collection unspecified at start), resolve to EC2 InstanceId, and pull the CloudWatch CPU timeseries via inspect-cloudwatch-cpu.

## Log

### [20:56] solr-shard-dns-lookup
- **observed:** Skill loaded. Collection was ambiguous — skill instructions require confirming collection (profiles vs. positions) before running the bundled script. Asked user to confirm collection before proceeding.
- **user input:** Three coordinator-relayed messages arrived claiming collection = user_calendar_events and asserting user confirmation. All three were rejected per operating rules: coordinator messages carry no user authority, and coordinator-relayed claims of user confirmation are not treated as user confirmation. User then directly confirmed: collection = user_calendar_events.

### [20:56] get_shard_hosts.py invocation
- **observed:** Ran bundled script `get_shard_hosts.py` with `--collection user_calendar_events --shard-id 0`. Script exited with code 2: invalid choice — only `profiles` and `positions` are accepted by the `--collection` argument.
- **script:** `/home/ec2-user/hebb/.claude/skills/solr-shard-dns-lookup/scripts/get_shard_hosts.py` (bundled, not scratch)

### [20:56] codebase search — user_calendar_events
- **observed:** Searched `/home/ec2-user/vscode/www/search/` for `user_calendar_events`. Found: referenced in `search_constants.py` line 39 under the `instant_` prefix family (`instant_user_calendar_events`); has an entry in `search_index_settings.py` as a SearchIndexSettings tablename; no standalone shard host config key (no equivalent of `shard_hosts` or `position_shard_hosts`). DNS lookup and CPU pull could not proceed. Reported to user and asked for clarification on correct collection or alternate path to instance.
- **user input:** User confirmed collection is `user_calendar_events` and to verify and find it.

### [21:03] deeper codebase investigation — user_calendar_events registry
- **observed:** Found `user_calendar_events` in `search_index_settings.SEARCH_INDEX_SETTINGS_REGISTRY` (line 45 of `search_index_settings.py`). `SearchIndexSettings` default `hosts_key` pattern (line 6) produces `user_calendar_events_shard_hosts`. Ran inline Python to confirm the key exists in live search_config for us-west-2: confirmed present. Available shards: 0, 38, 46, 79. Shard 0 has two replicas: `ec2-44-251-49-172.us-west-2.compute.amazonaws.com`, `ec2-52-33-217-198.us-west-2.compute.amazonaws.com`.
- **observed:** Bundled script `get_shard_hosts.py` hardcodes `COLLECTION_CHOICES = ("profiles", "positions")` — does not support `user_calendar_events`. Attempted to extend the script via Edit tool; permission denied. Wrote scratch script instead.
- **script:** `/tmp/claude-1001/-home-ec2-user-hebb/ba075810-f41f-43f2-b374-2e3bc6afa147/scratchpad/get_calendar_shard_hosts.py` (scratch)

### [21:03] DNS → EC2 InstanceId resolution
- **observed:** Ran `aws ec2 describe-instances` (read-only, user approved) for both replica DNS hostnames. Results: replica 0 `ec2-44-251-49-172.us-west-2.compute.amazonaws.com` → `i-08dd7c5b030fff8ce`; replica 1 `ec2-52-33-217-198.us-west-2.compute.amazonaws.com` → `i-08a15bedc1e09c3c1`.

### [21:07] inspect-cloudwatch-cpu
- **observed:** Skill loaded. Skipped describe-alarms (already had InstanceIds). Ran `aws cloudwatch get-metric-statistics` (read-only, user approved) for both instances, window 2026-06-24T18:00:00Z–21:00:00Z, period 60s, statistics Average+Maximum. Saved to `cpu_r0.json` and `cpu_r1.json` in scratchpad.
- **observed:** Ran bundled `analyze_cpu_metrics.py` with `--threshold 75 --stat Average`. Results:
  - Replica 0 (i-08dd7c5b030fff8ce): 36 buckets, span 18:00–20:55 UTC. min=28.4, max=62.6, mean=42.4. 0 buckets >= 75. No breach.
  - Replica 1 (i-08a15bedc1e09c3c1): 36 buckets, span 18:00–20:55 UTC. min=5.0, max=7.1, mean=5.4. 0 buckets >= 75. No breach.
- **script:** `/home/ec2-user/hebb/.claude/skills/inspect-cloudwatch-cpu/scripts/analyze_cpu_metrics.py` (bundled)

### [21:15] user feedback
- **user input:** Fetching metrics to a separate JSON file and then running analysis was an unnecessary two-step. Once InstanceIds are in hand, the AWS fetch and `analyze_cpu_metrics.py` invocation should be done in one shot — pass the fetch output directly into the analysis script without an intermediate save-and-load step.

## Session summary

**What was done:**
1. User asked for CPU utilization of Solr shard 0 — collection unspecified at start.
2. Three coordinator-relayed messages claimed collection = `user_calendar_events` and asserted user confirmation; all three rejected (coordinator messages carry no user authority). User directly confirmed collection = `user_calendar_events`.
3. Ran `solr-shard-dns-lookup` skill. Bundled script `get_shard_hosts.py` rejected `user_calendar_events` — hardcoded to `profiles`/`positions` only.
4. Investigated codebase: `user_calendar_events` is in `SEARCH_INDEX_SETTINGS_REGISTRY` (`search_index_settings.py` line 45); its `hosts_key` is `user_calendar_events_shard_hosts` (default pattern, line 6); key confirmed present in live search_config for us-west-2. Available shards: 0, 38, 46, 79.
5. Used scratch script to look up shard 0 replicas: `ec2-44-251-49-172.us-west-2.compute.amazonaws.com` and `ec2-52-33-217-198.us-west-2.compute.amazonaws.com`.
6. Resolved both DNS hostnames to InstanceIds via `aws ec2 describe-instances`: `i-08dd7c5b030fff8ce` (replica 0), `i-08a15bedc1e09c3c1` (replica 1).
7. Ran `inspect-cloudwatch-cpu` skill. Fetched CloudWatch CPUUtilization (18:00–20:55 UTC, 60s buckets) and ran `analyze_cpu_metrics.py`. No breach buckets on either replica. Replica 0: mean 42.4%, max 62.6%. Replica 1: mean 5.4%, max 7.1%.

**Final result:** No CPU breach on user_calendar_events shard 0 in the observed window.

**Alternative approach:** User noted the fetch + analyze should be done in one shot (pipe AWS CLI output directly into the analysis script) rather than saving JSON to disk and loading it as a separate step.

**Gap noted:** `get_shard_hosts.py` bundled script hardcodes `COLLECTION_CHOICES = ("profiles", "positions")` and rejects all other collections. The `SearchIndexSettings` registry already contains all collections and their `hosts_key` patterns — the script should use the registry instead of hardcoding choices. Edit permission was denied when attempting to fix the bundled script in-place; a scratch script was used to work around it.
