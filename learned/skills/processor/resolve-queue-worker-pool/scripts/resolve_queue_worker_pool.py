#!/usr/bin/env python3
"""Resolve a processor SQS queue's worker-pool (queue-group) membership + siblings.

Thin CLI over ``hebb_utils.processor.worker_pools.resolve_queue_pools``. Given a
queue name + region, it prints every worker-pool group the queue belongs to (with
each pool's ``max_count`` / ``scale_out`` capacity) and the **sibling queues** that
share those pools — the inputs to a drain-side "is my queue starved by a noisy
neighbour" check (see learned/wiki/processor/queue-worker-pool-segregation and the
drain branch of learned/wiki/oncall/queue-backed-up).

The mapping is region-scoped runtime config read from $CODE_BASE via
``processor.ecs_scaling_utils``, so this imports ``www`` in-process — run with
PYTHONPATH rooted at www/:

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/resolve_queue_worker_pool.py" \
        --queue index_requests --region us-west-2

The value printed is the **current** config — not necessarily the layout at a past
incident time (queue-group config changes over time).
"""
from __future__ import absolute_import

import argparse
import json
import os
import sys
import traceback

# Put learned/ (the dir that contains hebb_utils/) on sys.path, at whatever depth.
# hebb_utils coexists with vscode's own top-level `utils` package on sys.path.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate hebb_utils on any parent directory")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.processor import worker_pools  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve a processor queue's worker-pool groups + sibling queues.")
    ap.add_argument("--queue", required=True,
                    help="SQS queue_name to resolve (e.g. index_requests)")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: EF_DEFAULT_REGION). The mapping is "
                         "region-scoped — pass the incident's region.")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    args = ap.parse_args(argv)

    result = worker_pools.resolve_queue_pools(args.queue, args.region)

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"queue   : {result['target_queue']}")
    print(f"region  : {result['region']}")
    print(f"in_pool : {result['in_pool']}  (groups containing this queue: {len(result['pools'])})")
    print("note    : config is the CURRENT value; queue-group layout changes over time.")
    print()
    if not result["pools"]:
        print("This queue is not present in any worker_config group for this region.")
    for p in result["pools"]:
        print(f"=== [{p['instance_type']}] {p['queue_group']} "
              f"(max_count={p['max_count']}, scale_out={p['scale_out_pending_messages_per_worker']}) ===")
        if p["siblings"]:
            print(f"  sibling queues ({len(p['siblings'])}): {', '.join(p['siblings'])}")
        else:
            print("  sibling queues: (none — dedicated pool)")
        print()
    print(f"all sibling queues (union across pools): "
          f"{', '.join(result['siblings']) if result['siblings'] else '(none)'}")
    if result["fetch_errors"]:
        print()
        print("config fetch errors (partial result):")
        for it, name, err in result["fetch_errors"]:
            print(f"  [{it}] {name}: {err}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except worker_pools.QueuePoolLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
