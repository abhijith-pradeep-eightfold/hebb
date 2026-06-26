#!/usr/bin/env python3
"""Read processor_event_log rows by filter — thin CLI over hebb_utils.processor.event_log.

The reusable building block for "look at processor op events": filter by
processor_msg_id, processor_parent_msg_id, group_id, operation0, and/or a recent
time window, and print the matching rows. The shared read/resolution logic lives in
the util; this file is argument-parsing + presentation.

Run (PYTHONPATH must root at www/ — db/cloud_interfaces live under www/):
    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_processor_event_log.py" [filters...] [--format json]
"""
from __future__ import absolute_import

import argparse
import json
import os
import sys
import traceback

# Put learned/ (the dir that contains hebb_utils/) on sys.path, at whatever depth.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate hebb_utils on any parent directory")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.processor import event_log  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read processor_event_log rows by filter.")
    ap.add_argument("--msg-id", help="processor_msg_id (SMID)")
    ap.add_argument("--parent-msg-id", help="processor_parent_msg_id")
    ap.add_argument("--group-id", help="group_id (tenant)")
    ap.add_argument("--operation", help="operation0 (op name)")
    ap.add_argument("--queue", help="queue_name (matched trimmed; trailing space tolerated)")
    ap.add_argument("--event-type",
                    help="event_type (message_dispatched/received/fetched/processed)")
    ap.add_argument("--since", help="absolute lower t_create bound 'YYYY-MM-DD[ HH:MM[:SS]]'")
    ap.add_argument("--until", help="absolute upper t_create bound 'YYYY-MM-DD[ HH:MM[:SS]]'")
    ap.add_argument("--since-hours", type=int,
                    help="only rows with t_create within the last N hours")
    ap.add_argument("--count-by",
                    help="aggregate mode: comma-separated columns to GROUP BY with COUNT(*) "
                         "(e.g. operation0,group_id). Allowed: operation0, group_id, "
                         "queue_name, event_type, status, system_id")
    ap.add_argument("--limit", type=int, default=200, help="max rows (default 200)")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    args = ap.parse_args(argv)

    if args.count_by:
        group_by = [c.strip() for c in args.count_by.split(",") if c.strip()]
        result = event_log.count_events(
            group_by=group_by,
            processor_parent_msg_id=args.parent_msg_id,
            group_id=args.group_id,
            operation0=args.operation,
            queue_name=args.queue,
            event_type=args.event_type,
            since=args.since,
            until=args.until,
            since_hours=args.since_hours,
            limit=args.limit,
        )
        if args.format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            cols = result["group_by"]
            print(f"db_type={result['db_type']}  table={result['table']}  "
                  f"count-by={','.join(cols)}  groups={len(result['rows'])}\n")
            for row in result["rows"]:
                vals = "  ".join(f"{c}={row.get(c)}" for c in cols)
                print(f"{row.get('cnt'):>10}  {vals}")
        return 0

    result = event_log.fetch_rows(
        processor_msg_id=args.msg_id,
        processor_parent_msg_id=args.parent_msg_id,
        group_id=args.group_id,
        operation0=args.operation,
        queue_name=args.queue,
        event_type=args.event_type,
        since=args.since,
        until=args.until,
        since_hours=args.since_hours,
        limit=args.limit,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"db_type={result['db_type']}  table={result['table']}  "
              f"rows={len(result['rows'])}\n")
        for row in result["rows"]:
            print(row)
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
