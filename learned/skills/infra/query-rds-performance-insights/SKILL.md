---
name: query-rds-performance-insights
model: sonnet
description: Decompose an RDS database instance's load via Performance Insights — pull `db.load.avg` (average active sessions, AAS) grouped by wait event, SQL statement, user, and host over a window, rank each key by mean/peak AAS with its share, and fetch the full SQL text behind a top digest. Use to find the driver of an "RDS CPU Utilization Too High" page once you have confirmed the CPU curve — "what is saturating this RDS writer", "split the DB load by wait event / SQL / host", "is this a commit/redo-log-flush write storm", "which statement is piling up on the writer", "show me the top SQL on cluster X over the spike", "get the full query text for this db.sql.id". It is the RDS analog of query-solr-load. Reads via the read-only `aws pi` CLI; for a GovCloud instance export the GOV creds and pass --region us-gov-west-1.
knowledge_required:
  - "[[../../../wiki/infra/rds-performance-insights|RDS Performance Insights]]"
knowledge_optional:
  - "[[../../../wiki/oncall/rds-cpu-high|RDS CPU too high (oncall)]]"
  - "[[../../../wiki/infra/govcloud-access|GovCloud access]]"
  - "[[../../../wiki/ats/ats-entity-cache|ats_entity_cache write path]]"
---

# Query RDS Performance Insights — DB-load split

Decompose a database instance's load to find what is saturating it. Performance
Insights reports `db.load.avg` = **average active sessions (AAS)**; grouping it by a
dimension over the spike window names the load type. The domain facts — the PI access
pattern, reading AAS against the instance vCPU count, the **commit / redo-log-flush
write-storm** signature, the aurora-mysql `db.sql`-only gotcha, and the rate-vs-load
two-axis rule — live in [[../../../wiki/infra/rds-performance-insights|RDS Performance
Insights]]. The runtime judgment this skill carries is **which window**, **which
dimension explains the rise**, and **reading the breakdown** (a write storm vs a heavy
query). The PI reads are read-only telemetry; the fetch + ranking is a **bundled
script** — `scripts/pi_load_split.py` — that runs unattended.

## When to use this

After confirming an [[../../../wiki/oncall/rds-cpu-high|RDS CPU too high]] alarm's
curve (with **`inspect-cloudwatch-metric`**), this is the step that finds the driver:
pull the load split, see which wait event / SQL / host dominates, then spot-check the
actual SQL (whose query tags name the op/tenant/caller) and route. It is the RDS analog
of the **`query-solr-load`** indexing-vs-query split.

## Steps

1. **Resolve the instance `DbiResourceId`.** PI is keyed by the instance's
   `DbiResourceId` (a `db-...` string), not the cluster/instance name. Get it from the
   read-only describe calls (run with the GOV creds for a gov cluster):
   ```bash
   aws rds describe-db-clusters  --region <region> --db-cluster-identifier <cluster>
   aws rds describe-db-instances --region <region> --db-instance-identifier <writer-instance>
   ```
   Note the instance class too (e.g. `db.r5.large` = 2 vCPU) — the vCPU count is the
   AAS ceiling you read the load against.

2. **Convert the spike window to Unix epoch.** PI takes epoch seconds, not ISO:
   ```bash
   date -u -d '2026-06-29 19:10:00' +%s   # -> 1782760200
   ```

3. **Pull the load split** (read-only, unattended). One call pulls the four
   diagnostic breakdowns (`db.wait_event`, `db.sql`, `db.user`, `db.host`) plus the
   ungrouped total, each ranked by mean/peak AAS with its share:
   ```bash
   "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pi_load_split.py" --identifier <db-XXXX> --region <region> --start <epoch> --end <epoch>
   ```
   For a **GovCloud** instance, export the GOV creds first (see
   [[../../../wiki/infra/govcloud-access|GovCloud access]]):
   ```bash
   export AWS_ACCESS_KEY_ID="$GOV_AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$GOV_AWS_SECRET_ACCESS_KEY"
   ```
   Pass `--group <dimension>` to pull just one breakdown, `--period`/`--limit` to tune.

4. **Read the breakdown.** Against the instance vCPU count, a mean AAS far above the
   vCPU count is saturation. The dominant dimension names the load:
   - `wait/io/redo_log_flush` + `COMMIT`-dominated + single-row `INSERT`s spread across
     the app fleet under the read-write user ⇒ a **commit/write storm** (the per-row
     commit rate is the cost, not query complexity).
   - CPU-bound execution + a heavy `SELECT` ⇒ an inefficient or high-volume query.

5. **Spot-check the actual SQL.** Grouping by `db.sql` gives full statements (with
   literals) and their `db.sql.id` digests. Fetch a digest's full text — on
   aurora-mysql use the **`db.sql`** group (it rejects `db.sql_tokenized`):
   ```bash
   "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/pi_load_split.py" --identifier <db-XXXX> --region <region> --start <epoch> --end <epoch> --sql-id <db.sql.id>
   ```
   The literal SQL carries **query tags** in a comment (`env=`, `op=`, `group_id=`,
   `processor_msg_id=`, `db_exp=<email>`) that name the source directly — for a uniform
   write storm this replaces a warehouse `group_id` breakdown.

6. **Trace & route.** Map the op/table named in the tags to its source and owner
   (e.g. an `INSERT INTO ats_entity_cache` from the `position_index` op — see
   [[../../../wiki/ats/ats-entity-cache|ats_entity_cache write path]]) — use the
   **`codeowners-owner`** skill.

## Notes

- **AAS vs calls/sec are different axes.** A near-instant statement (e.g. a pool-reset
  `ROLLBACK`) is negligible on the **AAS** axis yet ~1:1 with COMMIT on the **calls/sec**
  axis — both true. Don't read a high rate counter as a high load contributor. See the
  rate-vs-load rule in [[../../../wiki/infra/rds-performance-insights|RDS Performance Insights]].
- **Reachability is only knowable by trying.** Make the read and report plainly if it
  is denied (a gov instance needs the GOV creds + gov region).

## Related skills

- `inspect-cloudwatch-metric` — the step before this: confirm the RDS `CPUUtilization`
  alarm + WRITER/READER curve.
- `codeowners-owner` — route the producing op/table to its owning team.
- `oncall-rds-cpu-high` — the high-level runbook that sequences this skill into the full
  RDS-CPU investigation.
