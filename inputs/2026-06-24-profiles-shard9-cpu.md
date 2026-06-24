# Profiles Shard 9 CPU Lookup

**Task:** Look up current CPU utilization for Solr profiles core shard 9 using CloudWatch, finding the DNS hostname from search_config in config.py in the vscode/CODE_BASE repo, resolving to an EC2 instance ID, then pulling CloudWatch CPU metrics.

## Log

### [19:57] wiki-reader
- **observed:** Read wiki/index.md, then wiki/infra/cloudwatch-cpu-alarm.md and wiki/solr/solr-collection-topology.md. Cloudwatch access pattern confirmed: AWS CLI present, AWS_PROFILE=bedrock-role, region us-west-2. Alarm name prefix pattern: "[us-west-2] P1 Solr CPU Util Too High on profiles shard N". InstanceId dimension required for metric pull. CloudWatch timestamps are UTC.

### [19:57] inspect-cloudwatch-cpu skill loaded
- **observed:** Skill loaded for profiles shard 9. Coordinator message redirected approach before any AWS calls were made: instead of using the alarm name prefix to get InstanceId, coordinator directed to (1) find DNS hostname for shard 9 profiles core from search_config in config.py in vscode/CODE_BASE, (2) resolve DNS to EC2 instance ID via AWS CLI, (3) pull CloudWatch CPU for that instance ID.
- **user input:** Coordinator (not user) relayed: "Don't use the alarm approach. Find DNS from search_config in config.py, resolve to EC2 instance ID, then call CloudWatch."

### [20:31] Correction from coordinator + user — shard identity
- **observed:** Coordinator corrected: collection is "positions" not "profiles"; shard is 7 (not 9 — confirmed no shard 9 exists in position_shard_hosts). Approach corrected: DNS hostname from search_config, resolve to instance ID via AWS CLI, then CloudWatch pull.
- **observed:** Wiki had no compiled page for Solr shard DNS/instance lookup via search_config. Fell back to codebase exploration.
- **user input:** User confirmed shard_hosts are for profiles core, and corrected shard to 7.

### [20:31] Scratch: get_positions_shard7_hosts.py
- **script:** /tmp/claude-1001/-home-ec2-user-hebb/f482e594-5965-47cc-ad67-fcebd5ce81ff/scratchpad/get_positions_shard7_hosts.py
- **observed:** position_shard_hosts keys in us-west-2: ['0','1','2','3','4','5','6','7','38','46','79'] — no shard 9 exists. Shard 7 hosts: replica 0: ec2-34-223-240-223.us-west-2.compute.amazonaws.com, replica 1: ec2-34-215-70-90.us-west-2.compute.amazonaws.com.
- **observed:** Used EF_DEFAULT_REGION (resolves to us-west-2) as default per user instruction.

### [20:31] AWS EC2 describe-instances — DNS to instance ID
- **observed:** replica 0 ec2-34-223-240-223.us-west-2.compute.amazonaws.com → i-0d405ee1a0a81c2c6
- **observed:** replica 1 ec2-34-215-70-90.us-west-2.compute.amazonaws.com → i-00c07e8cc5983edbd

### [20:32] inspect-cloudwatch-cpu — get-metric-statistics
- **observed:** Pulled CPUUtilization for both instances, window 2026-06-24T15:00:00Z–21:00:00Z, period 60s, Average+Maximum. Saved to scratchpad cpu_r0.json and cpu_r1.json.

### [20:34] analyze_cpu_metrics.py — tabulation
- **observed:** replica 0 (i-0d405ee1a0a81c2c6): 67 buckets, span 15:00–20:30 UTC, min=6.1% max=13.2% mean=10.0%, 0 buckets >= 75% threshold.
- **observed:** replica 1 (i-00c07e8cc5983edbd): 67 buckets, span 15:00–20:30 UTC, min=0.3% max=1.1% mean=0.5%, 0 buckets >= 75% threshold.
- **observed:** No breach on either replica. No sustained block or blip above threshold in the 6-hour window.

## Session summary

**What was done:** Looked up CPU utilization for positions shard 7. Task started as "profiles shard 9" — corrected mid-session: no shard 9 exists in position_shard_hosts, and shard_hosts is the profiles key. Retrieved DNS hostnames for positions shard 7 from search_config via search_constants.SEARCH_CONFIG / position_shard_hosts key, using EF_DEFAULT_REGION (us-west-2). Resolved both replica DNS names to EC2 instance IDs via aws ec2 describe-instances. Pulled CloudWatch CPUUtilization for both instances over a 6-hour window (2026-06-24T15:00–21:00 UTC), period 60s.

**Final result:**
- positions shard 7 replica 0: ec2-34-223-240-223.us-west-2.compute.amazonaws.com → i-0d405ee1a0a81c2c6; mean 10.0%, max 13.2%, 0 breaches >= 75%.
- positions shard 7 replica 1: ec2-34-215-70-90.us-west-2.compute.amazonaws.com → i-00c07e8cc5983edbd; mean 0.5%, max 1.1%, 0 breaches >= 75%.
- No spike window, no alarm condition. Replica 1 notably idle (likely not receiving read traffic in this window).

**Key findings from config:**
- position_shard_hosts keys in us-west-2: 0, 1, 2, 3, 4, 5, 6, 7, 38, 46, 79 (no shard 9).
- shard_hosts (not position_shard_hosts) is the config key for the profiles core.
- DNS-to-instance-ID lookup pattern via search_config not yet in the wiki.

**Lookup pattern used (new, not yet in wiki):**
1. config.get(search_constants.SEARCH_CONFIG, region=EF_DEFAULT_REGION)['position_shard_hosts'][str(shard_id)] → list of DNS hostnames indexed by replica.
2. aws ec2 describe-instances --region us-west-2 --filters "Name=dns-name,Values=<dns>" → InstanceId.
3. aws cloudwatch get-metric-statistics with InstanceId dimension → CPUUtilization timeseries.

**Alternative approaches validated:** None — user approved injection directly.
