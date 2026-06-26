# DataWarehouseAdapterFactory

**Summary:** A factory that selects which analytics warehouse adapter — `StarrocksAdapter`, `RedshiftAdapter`, or `DatabricksAdapter` — handles a data-warehouse query, based on region and feature config rather than on the caller. This is why the same logical table (e.g. [[search-query-log|log.search_query_log]]) can be served from three different warehouses transparently.

## Where

`www/cloud_interfaces/datawarehouse.py` (`class DataWarehouseAdapterFactory`, `:47`). Adapters imported at `:10-12`.

## Selection logic

`DataWarehouseAdapterFactory.create(region=None, db_type=None, group_id=None)` (`:70-73`) defers to `_get_datawarehouse_adapter_cls(db_type, group_id)` (`:48-67`), which is `lru_cache(ttl_secs=600)`'d. The decision, in order:

1. If `db_type == 'starrocks'` **and** the `enable_starrocks` config is on for `EF_DEFAULT_REGION` → `StarrocksAdapter`.
2. Else if `EF_DEFAULT_REGION` is **not** an Azure region → `RedshiftAdapter`.
3. Else if `enable_azure_databricks` config is on for the region → `DatabricksAdapter`.
4. Otherwise → `RedshiftAdapter` (default fallback).

So StarRocks is opt-in per region (gated by both [[starrocks#region-gating|region support]] and the `enable_starrocks` config); Redshift is the default elsewhere.

## Restriction

Anonymous/background jobs are not allowed to use StarRocks (comment + guard at `datawarehouse.py:35`).

## Logical db_type overrides

A model can declare a *logical* db_type that this factory resolves to a physical warehouse at runtime via `dwh.get_db_type_override(<logical>.value)`. For example [[../processor/processor-event-log|processor_event_log]] (`ProcessorLogEvent`) declares `DBType.REDSHIFT_LOG` and was observed resolving to **starrocks** in a StarRocks region — the same one-logical-table/many-warehouses routing as [[search-query-log|log.search_query_log]].

## Related

- [[starrocks|StarRocks data warehouse]] — the warehouse this factory may select.
- [[search-query-log|log.search_query_log table]] — one logical table defined in all three warehouses.
- [[../processor/processor-event-log|processor_event_log table]] — another logical table (`REDSHIFT_LOG`) routed by this factory.
- [[querying-starrocks|Querying StarRocks]] — the direct `starrocks_utils` path (used when StarRocks is the chosen warehouse).

---
*Sources:* `www/cloud_interfaces/datawarehouse.py` (:10-12, :35, :47, :48-67, :70-73). Witness: `inputs/2026-06-24-starrocks-query-count.md`.
