---
task: Triage the "[us-west-2] [P2] Alarm Provisioning Failures" PagerDuty oncall page from a Slack thread — characterize the alarm, find the driver, trace to a root code path, route to an owning team.
date: 2026-06-29
skills_used:
  - {name: external-context-puller, note: pulled the PagerDuty Slack thread for the alarm}
  - {name: oncall-alarm-provisioning-failures, note: high-level runbook; sequences the building-block skills}
  - {name: wiki-reader, note: read the alarm-provisioning-failures wiki page for domain facts}
interventions: 0
---

# Alarm Provisioning Failures oncall triage (us-west-2 P2)

**Task:** Triage an oncall PagerDuty issue linked from Slack. Pull the thread, determine the alarm type, consult the matching Hebb oncall runbook, and run the end-to-end investigation: read/characterize the alarm against the real metric, find the driver, trace to a root op/code path, route to an owning team. Do not post anything outward-facing without explicit consent.

## Log

### [21:27] external-context-puller
- **observed:** Parsed the Slack URL (channel C07NZL0PL9K, ts 1782767459.853759) and read the thread via slack_read_thread. Thread parent is a PagerDuty page: ":red_circle: [us-west-2] [P2] Alarm Provisioning Failures", Incident type Base Incident, Assigned: Mohit Uniyal, Urgency High, Service: Core Infra, incident Q0ZEPMHL5NNCOA. Triggered via CloudWatch alarm "[us-west-2] [P2] Alarm Provisioning Failures".
  - One thread reply: a "DP On-Call Auto-Triage (v2)" bot post with a leading hypothesis: the brand-new `excess_log_volume` alarm (PR #110618, commit 052c522f58b, merged 2026-06-29T13:31Z by tarun-arora-eightfold) is raising during the daily `alarm_manager_alerts` DAG run because its runtime config partitions `alarm_config::excess_log_volume::{Processor,WWW,azure-Processor,azure-WWW}` were never added in us-west-2. Bot cites alarm code raising `Exception('Missing alarm_config::excess_log_volume::<log_group> entry for current region')` at `/src/www/monitoring/alarms/excess_log_volume_alarms.py:80-82`, each raise incrementing the failure metric at `/src/www/monitoring/alarm_manager.py:934-937`. Bot says it scraped 27 us-west-2 firings in 1yr (each a different failing-alarm name, same missing-config shape). Bot CAVEAT: CloudWatch Logs / DescribeAlarmHistory were AccessDenied for its triage identity (prod-search), so it did NOT read the DAG log and could not confirm the actual failing-alarm name; it asks that the `[Action Needed] Alarm <name>` lines be confirmed before acting.
- **effort:** Single Slack thread read after one ToolSearch to load the tool. The auto-triage bot's hypothesis is unconfirmed (it could not read the failing-key source); this is the starting context, not a verified finding.
