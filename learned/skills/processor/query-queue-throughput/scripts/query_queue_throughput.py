#!/usr/bin/env python3
"""Queue throughput / drain diagnostics over processor_event_log — thin CLI over hebb_utils.

Four modes, all read-only aggregates of log.processor_event_log for one queue over a
window (see learned/wiki/processor/processor-event-log and learned/wiki/oncall/queue-backed-up):

  --mode rates         per-bucket inflow (message_dispatched) vs drain (message_processed)
                       + net delta  -> the stock/flow fork (inflow surge vs drain dip).
  --mode latency       per-bucket and/or per-op/-group p50/p90 latency (percentile_approx)
                       + total_proc_sec (volume x latency = worker capacity consumed).
  --mode parents       the distinct-parent driver breakdown (the CORRECT metric:
                       COUNT(DISTINCT processor_msg_id), no event_type filter on the outer).
  --mode drivers-lift  the comparative-window driver breakdown (inflow branch): the same
                       message_dispatched `operation0 x group_id` breakdown over a pre /
                       spike / post window triple, normalised to a per-hour rate and
                       ranked by lift = spike_rate / pre_rate -> separates a spike-specific
                       driver (high lift, ~0 outside the spike) from high-baseline noise
                       (flat lift ~1). Absolute count alone surfaces the highest-baseline
                       tenant, not the one that spiked.

Shared read/aggregate logic lives in hebb_utils.processor.event_log; the generic
per-window lift transform in hebb_utils.analytics.window_lift. This file is
argument-parsing + presentation. Run with PYTHONPATH rooted at www/ (the util imports
db/cloud_interfaces, which are www-rooted):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_queue_throughput.py" \
        --queue index_requests --mode rates \
        --since "2026-06-23 13:00:00" --until "2026-06-23 21:00:00" [--format json]
"""
from __future__ import absolute_import

import argparse
import datetime as _dt
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
from hebb_utils.analytics import window_lift  # noqa: E402


def _minutes_between(since, until):
    """Window length in minutes from two 'YYYY-MM-DD[ HH:MM[:SS]]' bounds (floored at 1)."""
    def _parse(s):
        s = str(s).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"unparseable timestamp {s!r}")
    return max((_parse(until) - _parse(since)).total_seconds() / 60.0, 1.0)


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
    ap.add_argument("--mode", choices=("rates", "latency", "parents", "drivers-lift"),
                    default="rates")
    ap.add_argument("--since", required=True,
                    help="lower t_create bound 'YYYY-MM-DD[ HH:MM[:SS]]' (child/queue "
                         "window; in drivers-lift mode this is the SPIKE window start)")
    ap.add_argument("--until", required=True,
                    help="upper t_create bound (in drivers-lift mode, the SPIKE window end)")
    ap.add_argument("--bucket-minutes", type=int, default=None,
                    help="time-bucket size (rates: default 15; latency: optional)")
    ap.add_argument("--by", default=None,
                    help="latency mode: comma-separated dims to group by (operation0,group_id)")
    ap.add_argument("--operation", help="latency mode: filter to one operation0")
    ap.add_argument("--group-id", help="latency mode: filter to one group_id")
    ap.add_argument("--parent-since", help="parents mode: outer parent-window lower bound "
                                           "(widen earlier than --since to catch delayed parents)")
    ap.add_argument("--parent-until", help="parents mode: outer parent-window upper bound")
    ap.add_argument("--pre-since", help="drivers-lift mode: baseline (pre-spike) window "
                                        "lower bound; the pre window is [pre-since, --since]. "
                                        "Required for this mode (it is the lift denominator).")
    ap.add_argument("--pre-until", help="drivers-lift mode: pre window upper bound "
                                        "(default: --since, so pre and spike are contiguous)")
    ap.add_argument("--post-since", help="drivers-lift mode: post-spike window lower bound "
                                         "(default: --until)")
    ap.add_argument("--post-until", help="drivers-lift mode: post window upper bound "
                                         "(optional; supplying it adds the post_per_hr column "
                                         "that reveals ramping drivers sustained after the spike)")
    ap.add_argument("--limit", type=int, default=200, help="max rows (default 200)")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    ap.add_argument("--region", default=None,
                    help="Region for warehouse routing. Overrides EF_DEFAULT_REGION "
                         "for this invocation (e.g. us-west-2, eu-central-1, "
                         "ca-central-1, ap-southeast-2, westus2). When unset, "
                         "EF_DEFAULT_REGION from the environment is used.")
    args = ap.parse_args(argv)

    if args.mode == "rates":
        result = event_log.throughput_timeseries(
            args.queue, args.since, args.until,
            bucket_minutes=args.bucket_minutes if args.bucket_minutes is not None else 15,
            region=args.region)
        cols = ["bucket", "dispatched_in", "processed_out", "net_delta"]
    elif args.mode == "latency":
        by = [c.strip() for c in (args.by or "").split(",") if c.strip()]
        result = event_log.latency_breakdown(
            args.queue, args.since, args.until,
            bucket_minutes=args.bucket_minutes, by=by,
            operation0=args.operation, group_id=args.group_id, limit=args.limit,
            region=args.region)
        cols = (["bucket"] if args.bucket_minutes is not None else []) + by + \
               ["processed_out", "p50_ms", "p90_ms", "total_proc_sec"]
    elif args.mode == "parents":
        result = event_log.parent_attribution(
            args.queue, args.since, args.until,
            parent_since=args.parent_since, parent_until=args.parent_until, limit=args.limit,
            region=args.region)
        cols = ["operation0", "distinct_msgs"]
    else:  # drivers-lift
        if not args.pre_since:
            ap.error("--mode drivers-lift requires --pre-since (the baseline window start; "
                     "it is the lift denominator)")
        by = [c.strip() for c in (args.by or "operation0,group_id").split(",") if c.strip()]
        # Contiguous-by-default window triple: pre=[pre_since, --since], spike=[--since,
        # --until], post=[--until, post_until]. Post is optional.
        wins = [("pre", args.pre_since, args.pre_until or args.since),
                ("spike", args.since, args.until)]
        if args.post_until:
            wins.append(("post", args.post_since or args.until, args.post_until))
        windows_for_lift, meta = [], None
        for name, w_since, w_until in wins:
            res = event_log.count_events(
                group_by=by, queue_name=args.queue, event_type="message_dispatched",
                since=w_since, until=w_until, limit=args.limit, region=args.region)
            meta = meta or res
            windows_for_lift.append((name, _minutes_between(w_since, w_until), res["rows"]))
        rows = window_lift.compute_lift(windows_for_lift, baseline="pre", spike="spike",
                                        key_cols=by)[:args.limit]
        result = {"db_type": meta["db_type"], "table": meta["table"], "queue": args.queue,
                  "by": by, "event_type": "message_dispatched",
                  "windows": {name: [s, u] for name, s, u in wins}, "rows": rows}
        cols = by + [f"{name}_per_hr" for name, _s, _u in wins] + ["lift"]

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
