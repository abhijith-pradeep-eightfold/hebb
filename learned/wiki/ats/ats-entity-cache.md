# ats_entity_cache write path + the position_index producer

**Summary:** `ats_entity_cache` is an ATS-domain table on the **`log` DB** (the `shared-log-cluster` family) that caches entity data and the last-synced/expiry state per entity. It is written one row at a time by `AtsEntity.save`; the relevant write for incidents is `invalidate_ats_entity` (a per-entity expiry/tombstone upsert), driven per-deleted-position by the processor **`position_index`** op, whose `pid` batches are dispatched by the bulk re-index CLI `re-index-db-positions.py`. There is **no rate/volume config gate** on the invalidation calls — only an on/off indexing gate — so call volume equals the number of deleted positions in a dispatch.

## The model

`AtsEntity` (`www/ats/ats_entity.py:59`, a `db_loader.DBLoader` subclass):

- `tablename()` returns `'ats_entity_cache'` (`www/ats/ats_entity.py:90`).
- `get_default_db()` returns `'log'` (`www/ats/ats_entity.py:96`) — so the model writes to the **`log` DB**, i.e. the `shared-log-cluster-*-mysql*` family. This is why an `ats_entity_cache` write storm shows up as load on the **log cluster's** writer (see [[../oncall/rds-cpu-high|RDS CPU too high]]).
- The class caches entity data and the last time an entity was synced, plus expiry state.

## The invalidation write

`invalidate_ats_entity(group_id, system_id, entity_type, entity_id, …)` (`www/ats/ats_entity.py:648`) writes a single expiry/tombstone row:

- sets `expiry_ts` and `metadata_json` (with `expiry_reason = ef_entity_deleted`),
- stamps `_set_processor_msg_id(caller_id='invalidate_ats_entity')` (`:678`),
- calls `ae.save(db=db)` with default `db='log'` (`:679`) — **one committed single-row upsert per call**.

The save is **not batched** — each call is its own `INSERT … ON DUPLICATE KEY UPDATE` and (via the bare-DML autocommit path) its own COMMIT. That per-row commit is the cost driver in a write storm; see the [[../infra/rds-performance-insights|commit / redo-log-flush write-storm pattern]] for the SQLAlchemy save/commit/rollback mechanics. The emitted upsert carries query tags (`env=processor`, `op=position_index`, `group_id`, `processor_msg_id`) that name the source directly.

## The producer — the `position_index` op

The op `position_index` maps via [[../processor/op-registry|op_registry]] to `('processor.position_index_operation', 'PositionIndexOperation')` (`www/processor/op_registry.py:40`) → `www/processor/position_index_operation.py`:

- `_handle_with_metrics` builds `pid_list` from `request['pid']`, loads `usp_list = Position().load(filter_by={'id': pid_list})`, then **loops one iteration per position**: `for usp in usp_list: process_usp(...)` (`:312,316-328,324`).
- `process_usp` computes `delete_doc = (not usp or usp.deleted_at > 0)` and calls `invalidate_ats_entity_if_needed(usp, delete_doc)` (`:477-481`), which — **only when the position is deleted** — calls `ats_entity.invalidate_ats_entity(... entity_type=POSITION, metadata_json={'expiry_reason': EF_ENTITY_DELETED})` (`:485-496`).
- Only the **Solr** docs are chunked (`config.get('processor_config','position_index_operation').get('solr_batch_size', 10)`, `:79-80,116`); the `ats_entity_cache` write is **not** batched.

So **call volume = the number of deleted positions in the dispatched `pid` lists.**

### Who dispatches the `pid` batches

The bulk DB re-index CLI `re-index-db-positions.py:main()` builds per-`group_id` `pid` batches and enqueues them:

- `queue_utils.add_to_processing_queue(operations=['position_index'], extra_params={'pid': batch, 'group_id': …})` per batch (`www/processor/re-index-db-positions.py:359,377,401`); for **deleted** positions the op list is just `['position_index']` (`:450-452`). Entry point `main()` at `:462-463`/`:495`.
- The queue is `nlx_position_index_queue` (SQS).
- The enqueue stamps a `_traceback` into the payload — `payload['_traceback'] = thread_utils.current_stack(compress=True)` (`www/processor/queue_utils.py:650`) — so consumer-side logs (e.g. a `Multiple receives` WARN) name their producer for free, without a warehouse lineage trace.

A bulk re-index or a mass position deletion for one tenant therefore fans out to many `position_index` messages → many per-position single-row committed invalidations.

## The config gate — on/off, not a rate throttle

The only gate on whether the invalidation path runs is an **indexing on/off check**, not a volume cap:

- `should_process_usp` → `is_indexing_enabled(group_id)` → `search_server.should_index_group_id(group_id)` (`www/processor/position_index_operation.py:245-246`).
- `should_index_group_id` reads `config.get('search_group_mappings', [group_id, region])` and honors a **`do_not_index`** flag; indexing is also disabled when `data_deletion_config[group_id][region].delete_all_data` is truthy, or for `eightfolddemo-` groups missing `ats_config` (`www/search/search_server.py:1616`).

There is **no config that caps the number of invalidation calls per op or per tenant** — if a group is indexable, every deleted position in the request fires its own committed invalidation. (For a tenant with a live `search_group_mappings` entry and no `do_not_index`, nothing gates the surge.) Read a specific tenant's mapping with the `config-get` skill.

## Ownership

`www/ats/` is owned by **`@EightfoldAI/dp-integrations`** (CODEOWNERS `/www/ats/`, line 312) — including `ats_entity.py` and the invalidate/save path. If a bulk **file ingestion** batch under `www/ats/data_ingestion/` was the trigger, the secondary owner is **`@EightfoldAI/dp-file-ingestion`** (CODEOWNERS `/www/ats/data_ingestion/`, line 314). Resolve with the `codeowners-owner` skill. See [[../repo/codeowners-ownership|CODEOWNERS ownership resolution]].

## Related skills

- `codeowners-owner` — use it to resolve the owning team of `www/ats/ats_entity.py` (or an op like `position_index` via op_registry) so an `ats_entity_cache` write storm routes to the right team.
- `config-get` — use it to read `search_group_mappings[<group_id>]` (the indexing on/off gate) or `processor_config.position_index_operation` (the Solr batch size).
- `query-rds-performance-insights` — use it to confirm an `ats_entity_cache` write storm in Performance Insights (the `COMMIT` + single-row `INSERT INTO ats_entity_cache` signature) and read the per-row upsert's query tags.
- `oncall-rds-cpu-high` — the high-level runbook for the RDS-CPU page this table's write storm produces.

## Related

- [[../infra/rds-performance-insights|RDS Performance Insights — write-storm pattern]] — the per-row save/commit/rollback mechanics behind the storm this table can cause.
- [[../oncall/rds-cpu-high|RDS CPU too high]] — the oncall ticket type where an `ats_entity_cache` write storm appeared as log-cluster writer CPU.
- [[../processor/op-registry|op_registry]] — maps `position_index` to its operation class/file.
- [[../repo/codeowners-ownership|CODEOWNERS ownership resolution]] — `www/ats/` → dp-integrations.

---
*Sources:* witness `inputs/2026-06-29-rds-cpu-alarm-triage.md` (`[20:18]` model tablename/default-db + ownership; `[20:32]` the invalidate→save path + the sampled upsert payload/tags; `[21:52]` the per-position loop + the `should_index_group_id` config gate; `[20:59]` the producer→queue→consumer→SQL pin via the enqueue `_traceback`). Code anchors: `www/ats/ats_entity.py:59,90,96,648,678,679`, `www/processor/op_registry.py:40`, `www/processor/position_index_operation.py:79-80,116,245-246,312,316-328,324,477-481,485-496`, `www/search/search_server.py:1616`, `www/processor/re-index-db-positions.py:359,377,401,450-452,462-463,495`, `www/processor/queue_utils.py:650`; CODEOWNERS `/www/ats/` line 312, `/www/ats/data_ingestion/` line 314.
