# Processor worker-pool / queue-group segregation

**Summary:** The `www` processor does not drain every SQS queue from one shared worker fleet — capacity is **segregated into queue groups**, each group a named worker pool that drains a fixed set of queues with its own scaling cap. A queue's drain rate is bounded by the pools it belongs to and is contended by the **sibling queues** in those pools. The mapping is **region-scoped runtime config** (not a repo file), read through `processor.ecs_scaling_utils`. This is the capacity model behind a [[../oncall/queue-backed-up#drain-branch-when-inflow-is-flat-but-depth-rose|drain-side queue backup]]: to test "is my queue starved by a noisy neighbor," you resolve its groups and their siblings.

## The config

- Worker scaling is driven by per-instance-type config named **`processor_worker_<instance_type>_ecs_config`**.
- `processor.ecs_scaling_utils.get_ecs_registry(region=...)` returns the registry of instance types — observed **5**: `spot`, `on-demand`, `canary`, `hotfix`, `highmem-spot` — each record carrying the name of its `ecs_config` (`www/processor/ecs_scaling_utils.py:111-146`).
- The config for one instance type is fetched with `config.get(rec.ecs_config, region=...)` — so it is **resolved live, per region** (`www/processor/ecs_scaling_utils.py:148-162`). The same queue can be allocated differently in different regions.
- Inside a config, **`cfg['worker_config']`** maps **`queue_group → {queues: [...], max_count, scale_out_pending_messages_per_worker}`**:
  - `queues` — the list of SQS queues this pool drains.
  - `max_count` — the pool's worker ceiling (its hard drain-capacity bound).
  - `scale_out_pending_messages_per_worker` — the backlog-per-worker threshold that triggers scale-out.
- This mapping is consumed by `queue_utils.compute_queue_to_worker_allocation` (`www/processor/queue_utils.py:1749`: `for queue_group, queue_group_config in ecs_config.get('worker_config', {}).items(): queues = queue_group_config.get('queues', [])`).

## How a queue maps to pools

A single queue commonly appears in **several** groups across instance types — e.g. a **dedicated** high-capacity pool, a catch-all `everything_else` pool, and a tiny `unallocated` pool — so "which pool" is really "which set of pools," each with its own cap and siblings. To find them for a queue, walk the registry, fetch each instance type's config, and collect every `worker_config` group whose `queues` list contains the target — then read off the **sibling queues** (the other entries in those `queues` lists). The sibling queues are the ones that can contend for the same workers.

**To do this lookup, use the `resolve-queue-worker-pool` skill** — given a queue name + region it dumps the queue's groups (with `max_count`/`scale_out`) and the sibling queues sharing each pool. Then, for a noisy-neighbor check, compare each sibling's inbound (`message_dispatched`) rate baseline-vs-breach with the **`query-queue-throughput` skill**: a sibling whose inbound spiked in-window is a contention suspect; if none spiked, shared-pool contention is ruled out.

## Caveats

- **The config is the *current* value.** Because it is fetched live per region, the pool layout you read now is not guaranteed identical to the layout at the time of a past incident — queue-group config changes over time. Note this when reasoning about a historical window.
- A queue being in a high-`max_count` dedicated pool does **not** mean it is uncontended — check the dedicated pool's *other* siblings, and remember the queue also rides the `everything_else` pool.

## Related skills

- `resolve-queue-worker-pool` — use it to resolve a queue's worker-pool groups (with capacities) and the sibling queues sharing each pool, for a given region.

## Related

- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — the drain-branch contention check that this model supports.
- [[processor-event-log|processor_event_log]] — `queue_name` / `message_processed` (drain) events whose rate this capacity bounds.
- [[../data-warehouse/querying-starrocks|Querying StarRocks]] — where the per-queue inflow/drain rates compared against the sibling set come from.

---
*Sources:* `www/processor/ecs_scaling_utils.py:111-146` (`get_ecs_registry`, 5 instance types), `:148-162` (`config.get(ecs_config, region=...)`), `www/processor/queue_utils.py:1749` (`worker_config` → `queue_group`/`queues`). Witness: `inputs/2026-06-26-queue-backed-up-index-requests.md` (`[23:14]`).
