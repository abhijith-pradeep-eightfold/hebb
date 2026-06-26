#!/usr/bin/env python3
"""Trace a processor SMID to its root op — thin CLI over hebb_utils.processor.event_log.

Walks the processor_parent_msg_id chain from a target SMID up to the parentless root
and prints each hop, the root op, and the root->target op trace. The reusable read
logic lives in the shared util; this file is just argument-parsing + presentation.

Run (PYTHONPATH must root at www/ — db/cloud_interfaces live under www/):
    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/trace_processor_op.py" <smid> [--max-depth N] [--format json]
"""
from __future__ import absolute_import

import argparse
import json
import os
import sys
import traceback

# Put learned/ (the dir that contains hebb_utils/) on sys.path, at whatever depth this
# script is nested. realpath() resolves the .claude/skills/<name> symlink to the real
# file under learned/skills/, so the walk-up finds learned/hebb_utils either way.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate hebb_utils on any parent directory")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.processor import event_log  # noqa: E402


def _print_human(result):
    print(f"db_type={result['db_type']}  table={result['table']}\n")
    print("=== HOPS (target -> root) ===")
    for hop in result["chain"]:
        print(hop)
    root = result["chain"][-1]
    print("\n=== ROOT PROCESSOR OP ===")
    print(f"operation0 = {root.get('operation0')}")
    print(f"processor_msg_id (root SMID) = {root.get('processor_msg_id')}")
    print("\n=== OP TRACE (root -> target) ===")
    print(" -> ".join(
        f"{hop.get('operation0')}[{(hop.get('processor_msg_id') or '')[:8]}]"
        for hop in reversed(result["chain"])
    ))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trace a processor SMID to its root op.")
    ap.add_argument("smid", help="target processor_msg_id (SMID) to trace")
    ap.add_argument("--max-depth", type=int, default=50,
                    help="safety cap on chain length (default 50)")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    args = ap.parse_args(argv)

    if not event_log.is_valid_smid(args.smid):
        ap.error(f"smid {args.smid!r} is not a valid processor_msg_id (UUID charset expected)")

    result = event_log.walk_parent_chain(args.smid, max_depth=args.max_depth)
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except event_log.ProcessorEventLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
