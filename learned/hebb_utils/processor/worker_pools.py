"""Resolve a processor SQS queue's worker-pool (queue-group) membership from live config.

Shared logic for the `resolve-queue-worker-pool` skill. Processor drain capacity is
segregated into named **queue groups**, each a worker pool draining a fixed set of
queues with its own ``max_count`` / ``scale_out_pending_messages_per_worker``. The
mapping is region-scoped **runtime config** (``processor_worker_<instance_type>_ecs_config``),
not a repo file — see learned/wiki/processor/queue-worker-pool-segregation.

This is **vscode-dependent**: it imports ``processor.ecs_scaling_utils`` (www-rooted),
so a caller must run with ``PYTHONPATH=$CODE_BASE/www`` (see
learned/wiki/vscode-repo/python-import-root). It lives in ``hebb_utils`` (not
``utils``) so it can be imported in the same process as vscode's own top-level
``utils`` package without a collision.

The registry (`ecs_scaling_utils.get_ecs_registry`) enumerates the instance types
(spot / on-demand / canary / hotfix / highmem-spot); each record names its
``ecs_config``, fetched live per region with ``ecs_scaling_utils.config.get(name,
region=...)``. A queue commonly appears in several groups (a dedicated pool, a
catch-all ``everything_else``, a tiny ``unallocated``) — so "which pool" is "which
set of pools," each with its own capacity and sibling queues. The value read is the
**current** config, not necessarily the value at a past incident time.
"""
import os


class QueuePoolLookupError(Exception):
    """A queue's worker-pool membership could not be resolved; message is user-facing.

    The message is everything after the conventional ``error: `` prefix, so a CLI
    caller can print ``f"error: {exc}"`` and reproduce the original wording.
    """


def resolve_queue_pools(target_queue, region=None):
    """Resolve every worker-pool group ``target_queue`` belongs to, plus its siblings.

    Returns a dict::

        {
          "region": <region used>,
          "target_queue": <queue>,
          "in_pool": <bool — found in at least one group>,
          "pools": [ {instance_type, queue_group, max_count,
                      scale_out_pending_messages_per_worker, queues, siblings}, ... ],
          "siblings": <sorted unique list of co-tenant queues across all pools>,
          "fetch_errors": [ (instance_type, ecs_config_name, error_str), ... ],
        }

    Raises ``QueuePoolLookupError`` when the region is unset or the vscode import
    fails (wrong PYTHONPATH). Per-instance-type config-fetch failures are collected in
    ``fetch_errors`` rather than aborting, so a partial result is still returned.
    """
    region = region or os.getenv("EF_DEFAULT_REGION")
    if not region:
        raise QueuePoolLookupError("--region not specified and EF_DEFAULT_REGION is not set")

    # vscode import — requires PYTHONPATH=$CODE_BASE/www.
    try:
        from processor import ecs_scaling_utils
    except ImportError as exc:
        raise QueuePoolLookupError(
            f"import failed — is PYTHONPATH set to $CODE_BASE/www?\n  {exc}") from exc

    registry = ecs_scaling_utils.get_ecs_registry(region=region)
    pools, siblings, fetch_errors = [], set(), []
    for instance_type, rec in registry.items():
        try:
            # The region-targeted fetch: get_config_for_instance_type() is locked to
            # the current runtime region, so go through the lower-level config.get to
            # aim at an arbitrary region (the validated path from the witness).
            cfg = ecs_scaling_utils.config.get(rec.ecs_config, region=region)
        except Exception as exc:  # config backend errors — keep going, report at end.
            fetch_errors.append((instance_type, rec.ecs_config, str(exc)))
            continue
        if not cfg:
            continue
        for queue_group, qgc in (cfg.get("worker_config", {}) or {}).items():
            queues = qgc.get("queues", []) or []
            if target_queue in queues:
                others = [q for q in queues if q != target_queue]
                pools.append({
                    "instance_type": instance_type,
                    "queue_group": queue_group,
                    "max_count": qgc.get("max_count"),
                    "scale_out_pending_messages_per_worker":
                        qgc.get("scale_out_pending_messages_per_worker"),
                    "queues": queues,
                    "siblings": others,
                })
                siblings.update(others)
    return {
        "region": region,
        "target_queue": target_queue,
        "in_pool": bool(pools),
        "pools": pools,
        "siblings": sorted(siblings),
        "fetch_errors": fetch_errors,
    }
