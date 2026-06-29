#!/usr/bin/env python3
"""Queue throughput / drain diagnostics over processor_event_log — thin CLI over hebb_utils.

Three modes, all read-only aggregates of log.processor_event_log for one queue over a
window (see learned/wiki/processor/processor-event-log and the drain branch of
learned/wiki/oncall/queue-backed-up):

  --mode rates    per-bucket inflow (message_dispatched) vs drain (message_processed)
                  + net delta  -> the stock/flow fork (inflow surge vs drain dip).
  --mode latency  per-bucket and/or per-op/-group p50/p90 latency (percentile_approx)
                  + total_proc_sec (volume x latency = worker capacity consumed).
  --mode parents  the distinct-parent driver breakdown (the CORRECT metric:
                  COUNT(DISTINCT processor_msg_id), no event_type filter on the outer).

Shared read/aggregate logic lives in hebb_utils.processor.event_log; this file is
argument-parsing + presentation. Run with PYTHONPATH rooted at www/ (the util imports
db/cloud_interfaces, which are www-rooted):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_queue_throughput.py" \
        --queue index_requests --mode rates \
        --since "2026-06-23 13:00:00" --until "2026-06-23 21:00:00" [--format json]
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


def _print_rows(result, cols):
    print(f"db_type={result['db_type']}  table={result['table']}  "
          f"queue={result['queue']}  rows={len(result['rows'])}\n")
    widths = {c: max(len(c), *(len(str(r.get(c))) for r in result["rows"])) if result["rows"]
              else len(c) for c in cols}
    print("  ".join(c.rjust(widths[c]) for c in cols))
    for r in result["rows"]:
        print("  ".join(str(r.get(c)).rjust(widths[c]) for c in cols))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Queue throughput / drain diagnostics over processor_event_log.")
    ap.add_argument("--queue", required=True, help="queue_name (matched trimmed)")
    ap.add_argument("--mode", choices=("rates", "latency", "parents"), default="rates")
    ap.add_argument("--since", required=True,
                    help="lower t_create bound 'YYYY-MM-DD[ HH:MM[:SS]]' (child/queue window)")
    ap.add_argument("--until", required=True, help="upper t_create bound")
    ap.add_argument("--bucket-minutes", type=int, default=None,
                    help="time-bucket size (rates: default 15; latency: optional)")
    ap.add_argument("--by", default=None,
                    help="latency mode: comma-separated dims to group by (operation0,group_id)")
    ap.add_argument("--operation", help="latency mode: filter to one operation0")
    ap.add_argument("--group-id", help="latency mode: filter to one group_id")
    ap.add_argument("--parent-since", help="parents mode: outer parent-window lower bound "
                                           "(widen earlier than --since to catch delayed parents)")
    ap.add_argument("--parent-until", help="parents mode: outer parent-window upper bound")
    ap.add_argument("--limit", type=int, default=200, help="max rows (default 200)")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    args = ap.parse_args(argv)

    if args.mode == "rates":
        result = event_log.throughput_timeseries(
            args.queue, args.since, args.until,
            bucket_minutes=args.bucket_minutes if args.bucket_minutes is not None else 15)
        cols = ["bucket", "dispatched_in", "processed_out", "net_delta"]
    elif args.mode == "latency":
        by = [c.strip() for c in (args.by or "").split(",") if c.strip()]
        result = event_log.latency_breakdown(
            args.queue, args.since, args.until,
            bucket_minutes=args.bucket_minutes, by=by,
            operation0=args.operation, group_id=args.group_id, limit=args.limit)
        cols = (["bucket"] if args.bucket_minutes is not None else []) + by + \
               ["processed_out", "p50_ms", "p90_ms", "total_proc_sec"]
    else:  # parents
        result = event_log.parent_attribution(
            args.queue, args.since, args.until,
            parent_since=args.parent_since, parent_until=args.parent_until, limit=args.limit)
        cols = ["operation0", "distinct_msgs"]

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_rows(result, cols)
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
