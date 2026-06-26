#!/usr/bin/env python3
"""Trace a processor SMID to its root op by walking the processor_parent_msg_id chain.

Given a target SMID (processor_msg_id) of a row in processor_event_log, repeatedly
look up the row, read its parent (processor_parent_msg_id), and follow the edge upward
until a row has no parent. That terminal row is the ROOT processor op (its operation0).

Read-only: the script only ever issues SELECTs that it constructs itself, against the
warehouse the model resolves to (REDSHIFT_LOG -> per-region warehouse, e.g. StarRocks).

Run (note PYTHONPATH must root at www/ — db/cloud_interfaces live under www/):
    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/trace_processor_op.py" <smid> [--max-depth N] [--format json]

Knowledge: learned/wiki/processor/processor-event-log.md,
           learned/wiki/processor/tracing-processor-op-lineage.md
"""
from __future__ import absolute_import

import argparse
import json
import re
import sys
import traceback

# SMID is a SQS/processor message UUID — restrict to the UUID charset so the value is
# safe to interpolate into the SELECT (defense-in-depth; the query is read-only anyway).
_SMID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# Columns to project per row. One message emits several rows (one per event_type);
# parent / op / group / queue are identical across them.
COLS = (
    "processor_msg_id, processor_parent_msg_id, operation0, operations_list, "
    "event_type, group_id, system_id, queue_name, status, request_trace_id, "
    "DATE_TRUNC('second', t_create) AS t_create"
)


def _fetch_rows(dwh, table, db_type, smid):
    """All rows for one SMID, ordered by event time. Read-only SELECT."""
    query = (
        f"SELECT {COLS} FROM {table} "
        f"WHERE processor_msg_id = '{smid}' ORDER BY t_create"
    )
    return dwh.get_list(query, db_type=db_type) or []


def _hop_from_rows(smid, depth, rows):
    """Collapse a message's rows into one hop summary."""
    events = sorted({r.get("event_type") for r in rows if r.get("event_type")})
    r0 = rows[0]
    parent = (r0.get("processor_parent_msg_id") or "").strip()
    return {
        "depth": depth,
        "processor_msg_id": smid,
        "operation0": r0.get("operation0"),
        "operations_list": r0.get("operations_list"),
        "events": events,
        "group_id": r0.get("group_id"),
        "queue_name": r0.get("queue_name"),
        # status is populated on the message_processed row (PASS/FAIL or a reroute marker)
        "status": [r.get("status") for r in rows if r.get("event_type") == "message_processed"],
        "t_create": str(r0.get("t_create")),
        "parent": parent or None,
    }


def trace(target, max_depth=50):
    # Imported lazily so --help works without PYTHONPATH=$CODE_BASE/www set.
    from db.base_log_event import ProcessorLogEvent
    from db.db_type import DBType
    from cloud_interfaces import datawarehouse as dwh

    # The model declares a logical db_type (REDSHIFT_LOG); resolve it to the region's
    # physical warehouse, then resolve the schema-qualified table name the same way.
    db_type = dwh.get_db_type_override(DBType.REDSHIFT_LOG.value)
    table = ProcessorLogEvent.get_full_table_name(db_type=db_type)

    chain, visited, smid, depth = [], set(), target, 0
    while smid and smid not in visited and depth < max_depth:
        visited.add(smid)
        rows = _fetch_rows(dwh, table, db_type, smid)
        if not rows:
            chain.append({"depth": depth, "processor_msg_id": smid, "_note": "NO ROW FOUND"})
            break
        hop = _hop_from_rows(smid, depth, rows)
        chain.append(hop)
        smid = hop["parent"]
        depth += 1

    return {"db_type": db_type, "table": table, "chain": chain}


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

    if not _SMID_RE.match(args.smid):
        ap.error(f"smid {args.smid!r} is not a valid processor_msg_id (UUID charset expected)")

    result = trace(args.smid, max_depth=args.max_depth)
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
