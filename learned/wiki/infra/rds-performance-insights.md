# RDS Performance Insights — DB-load decomposition + the write-storm pattern

**Summary:** RDS Performance Insights (PI) decomposes a database instance's load (`db.load.avg` = **average active sessions, AAS**) by wait event, SQL statement, user, and host over a window — the analytical core of an "RDS CPU too high" oncall. This page covers the PI access pattern (the `aws pi` calls, the `DbiResourceId`, the aurora-mysql `db.sql` gotcha), how to read AAS against the instance's vCPU ceiling, the **commit / redo-log-flush write-storm** signature, and the **rate-vs-load two-axis rule** for reading DB counters (why `ROLLBACK/sec ≈ COMMIT/sec` is a benign SQLAlchemy pool artifact, not failing queries).

## Access pattern (the `aws pi` calls)

Performance Insights is reachable read-only via the AWS CLI (in GovCloud with the `GOV_AWS_*` creds — see [[govcloud-access|GovCloud access]]). The `--identifier` is the instance's **`DbiResourceId`** (a `db-…` string), resolved from `describe-db-instances`; PI uses **Unix epoch** start/end times.

```bash
# resolve the writer instance + its DbiResourceId
aws rds describe-db-clusters  --region us-gov-west-1 --db-cluster-identifier <cluster>
aws rds describe-db-instances --region us-gov-west-1 --db-instance-identifier <writer-instance>
# -> DbiResourceId db-XXXX, class db.r5.large (2 vCPU), PerformanceInsightsEnabled=true

# load broken down by a dimension over the spike window (epoch seconds)
aws pi get-resource-metrics --region us-gov-west-1 --service-type RDS \
  --identifier db-XXXX \
  --metric-queries '[{"Metric":"db.load.avg","GroupBy":{"Group":"db.wait_event","Limit":10}}]' \
  --start-time <epoch> --end-time <epoch> --period-in-seconds 300
```

Swap the `Group` to decompose by a different dimension: **`db.wait_event`** (what sessions block on), **`db.sql`** / **`db.sql_tokenized`** (the statements), **`db.user`** (the DB user), **`db.host`** (the app host). Use `--metric-queries '[{"Metric":"db.load.avg"}]'` (no `GroupBy`) for the ungrouped total.

### Fetching full SQL text — the aurora-mysql `db.sql` gotcha

To read the **full statement text** behind a digest, `get-dimension-key-details` returns the `statement` dimension for a `db.sql.id`:

```bash
aws pi get-dimension-key-details --region us-gov-west-1 --service-type RDS \
  --identifier db-XXXX --group db.sql --group-identifier <db.sql.id> \
  --requested-dimensions statement
```

**Gotcha:** on **aurora-mysql**, `get-dimension-key-details` supports only the **`db.sql`** group — it **rejects `db.sql_tokenized`** with `InvalidArgumentException (... supports only the db.sql dimension groups)`. So to read literal statement text, group `db.load.avg` by `db.sql` (full statements with literals) to get the `db.sql.id` digests, then fetch each statement.

## Reading AAS against the vCPU ceiling

`db.load.avg` is **average active sessions (AAS)** — the mean number of sessions actively running or waiting. A healthy ceiling is roughly the instance **vCPU count**: AAS near vCPU = fully busy, AAS far above vCPU = saturation/queueing. A `db.r5.large` writer has **2 vCPU**, so a spike-window mean ~118 / peak ~174 AAS is ~60–85× the capacity — severe saturation.

The deterministic ranking of a PI breakdown JSON (mean/peak AAS + share per dimension) is automated by the `query-rds-performance-insights` skill.

## The commit / redo-log-flush write-storm signature

A **write storm** — a flood of small single-row writes, each individually committed — shows a distinctive PI profile:

- **By wait event:** `wait/io/redo_log_flush` dominates (observed 87.4%); actual `CPU` is a small fraction. The sessions are blocked on **redo-log (WAL) flush I/O**, i.e. fsync-ing committed redo to disk — the cost of committing, not of executing queries.
- **By SQL:** `COMMIT` dominates (observed 88.8%), with single-row `INSERT`s making up the rest (here `INSERT INTO ats_entity_cache` ~11%). Each row is its **own transaction → its own COMMIT → its own redo flush**.
- **By user / host:** spread across the app fleet under the read-write app user (observed: ~10 app hosts, user `read_write`) ⇒ a **fleet-wide application workload**, not a single rogue host or a `db_explorer` ad-hoc query.

The driver is therefore the **per-row commit rate**, not query complexity. The fix is to **batch the writes / reduce per-row commits** (fewer transactions = fewer redo flushes); the immediate relief is to scale up the writer. This is the write-side specialization of the EP runbook's "increased query volume" branch (see [[../oncall/rds-cpu-high|RDS CPU too high]]). In this incident the writes were single-row upserts into [[../ats/ats-entity-cache|ats_entity_cache]].

## Rate vs load — the two-axis rule for DB counters

Two different measures of the same statement are easy to conflate; name which axis you are on:

- **Load (AAS)** — *time-weighted*. A near-instant statement contributes ≈ 0 AAS no matter how often it runs.
- **Rate (calls/sec)** — *count-weighted*. A statement that runs once per request counts once per request regardless of how fast it is.

A fast statement can be **negligible on the AAS axis yet ~1:1 with COMMIT on the calls/sec axis**. This is exactly why `ROLLBACK` was ~0.16% of AAS but ≈ `COMMIT/sec` on the rate counter in this incident — both true, on different axes.

### Why `ROLLBACK/sec ≈ COMMIT/sec` is benign (the SQLAlchemy pool reset-on-return)

`ROLLBACK/sec ≈ INSERT/sec ≈ COMMIT/sec` is **not** a sign that queries are failing and rolling back. It is the **SQLAlchemy `QueuePool` reset-on-return** firing a no-op rollback on every connection check-in. Per save (one `ae.save(db='log')`, SQLAlchemy 1.4.45):

1. `INSERT … ON DUPLICATE KEY UPDATE` runs via a **bare** `connection.execute(query, vals)` (`www/db/db_connection.py:409`). Under SQLAlchemy 1.4 **legacy DML-autocommit**, a bare DML execute emits an **autocommit `COMMIT`** right after the statement — this is the per-statement COMMIT and the row IS persisted (the heavy `redo_log_flush` work). Contrast `execute_raw_sql`, which sets `.execution_options(autocommit=True)` explicitly (`:526`).
2. The write is wrapped in `with self.connection as conn:` (`www/db/db_client.py:257-258`); on exit `connection.close()` (`www/db/db_connection.py:782-784`) **returns the connection to the pool**.
3. `QueuePool`'s default `reset_on_return=True` normalizes to **`reset_rollback`**, so **every check-in issues a `ROLLBACK`** to clear residual session state — here a **no-op on the already-committed, empty transaction** (it rolls back nothing).

The code has a knob to suppress that per-return rollback — `pool_reset_on_return=None` — but it is **gated to `op_type=='read'`** (`www/db/db_connection.py:345-347`) and explicitly **popped back out for write endpoints** (`:671-673`, log line "Not disabling pool_reset_on_return for write endpoints"). Every save is hard-set `op_type='write'` (`www/db/db_utils.py:517`), so the writer **always** keeps the per-return rollback. Net per save: `INSERT` → autocommit `COMMIT` → pool-reset `ROLLBACK`, one each → the three rates track. The redo-flush wait (87%) is entirely from the **COMMITs** (the ROLLBACKs touch no redo), which is why ROLLBACK is ~0.16% of AAS despite being ~1:1 on the call-rate counter.

**Cross-check the outcome with the logs.** Whether writes are truly succeeding (vs failing-then-retrying) is settled by reading the in-region `Processor` CloudWatch Logs over the window: 0 ERROR lines, 0 exceptions/deadlocks/lock-waits confirms the COMMITs succeed; the only WARNs are `[High latency save] log_db_write took N ms` (successful but slow, from `www/db/query_log.py:130` — consistent with redo-flush saturation) and benign SQS `Multiple receives` redelivery. See [[govcloud-access|GovCloud access]] for the Logs-Insights access pattern.

## Related skills

- `query-rds-performance-insights` — use it to pull `db.load.avg` grouped by `db.wait_event`/`db.sql`/`db.user`/`db.host` for an RDS instance + window and rank each dimension by mean/peak AAS (the load-split that finds the write-storm driver).
- `inspect-cloudwatch-metric` — use it to confirm the RDS `CPUUtilization` alarm + WRITER/READER curve before decomposing the load in PI.
- `oncall-rds-cpu-high` — the high-level runbook that sequences this PI load-split into the full RDS-CPU investigation.

## Related

- [[../oncall/rds-cpu-high|RDS CPU too high]] — the oncall ticket type whose investigation core this is.
- [[govcloud-access|GovCloud access]] — PI is reachable in GovCloud with the GOV creds; the Logs-Insights cross-check too.
- [[../ats/ats-entity-cache|ats_entity_cache write path]] — the table the write storm targeted and the per-row save path.
- [[cloudwatch-cpu-alarm|CloudWatch CPU alarm + EC2 metric access]] — the metric-curve step that precedes the PI load-split.

---
*Sources:* witness `inputs/2026-06-29-rds-cpu-alarm-triage.md` (`[20:14]` PI access + AAS interpretation + wait/SQL/user/host breakdown + the ranker script; `[20:32]` `get-dimension-key-details` + the aurora-mysql `db.sql`-only gotcha; `[22:02]`/`[22:16]`/`[20:56]` the rate-vs-AAS two-axis reconciliation; `[20:51]` the SQLAlchemy pool reset-on-return write-path trace; `[20:55]` the Processor Logs 0-ERROR cross-check). Code anchors: `www/db/db_connection.py:409,526,782-784,345-347,671-673`, `www/db/db_client.py:257-258`, `www/db/db_utils.py:517`, `www/db/db_loader.py:2404,2415`, `www/db/query_log.py:125-130`. SQLAlchemy 1.4.45.
