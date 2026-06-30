#!/usr/bin/env python3
"""Solr load breakdown over log.search_query_log — thin CLI over hebb_utils.

Two modes, both read-only aggregates of log.search_query_log for one Solr core+shard
(see learned/wiki/data-warehouse/search-query-log and learned/wiki/oncall/solr-cpu-high):

  --mode split    per-bucket indexing (callerid='index') vs query (all other callerids)
                  counts -> which work stream rose with a CPU curve (CPU is a flow
                  metric = indexing + query work).
  --mode drivers  callerid x group_id x env over a spike window vs a baseline window,
                  normalized per-minute (+ spike/baseline ratio, NEW flag) -> which
                  source drove the stream that rose.

Shared read/aggregate logic lives in hebb_utils.solr.query_log; this file is
argument-parsing + presentation. Run with PYTHONPATH rooted at www/ (the util imports
datawarehouse/db, which are www-rooted):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_solr_load.py" \
        --mode split --core profiles --shard-id 21 \
        --since "2026-06-29 10:00:00" --until "2026-06-29 11:45:00" [--format json]
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
from hebb_utils.solr import query_log  # noqa: E402


def _print_rows(result, cols, header_keys):
    head = "  ".join(f"{k}={result[k]}" for k in header_keys)
    print(f"{head}  rows={len(result['rows'])}\n")
    rows = result["rows"]
    widths = {c: max(len(c), *(len(str(r.get(c))) for r in rows)) if rows else len(c)
              for c in cols}
    print("  ".join(c.rjust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c)).rjust(widths[c]) for c in cols))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Solr load breakdown over log.search_query_log (indexing-vs-query "
                    "split, and per-source driver breakdown).")
    ap.add_argument("--mode", choices=("split", "drivers"), required=True)
    ap.add_argument("--core", required=True, help="Solr core (= collection), e.g. profiles")
    ap.add_argument("--shard-id", required=True, type=int, help="shard id (int)")
    ap.add_argument("--since", required=True,
                    help="lower t_create bound 'YYYY-MM-DD[ HH:MM[:SS]]' (UTC; spike window)")
    ap.add_argument("--until", required=True, help="upper t_create bound (UTC)")
    ap.add_argument("--bucket-minutes", type=int, default=15,
                    help="[split] bucket size in minutes (default 15)")
    ap.add_argument("--baseline-since", help="[drivers] baseline window lower bound (UTC)")
    ap.add_argument("--baseline-until", help="[drivers] baseline window upper bound (UTC)")
    ap.add_argument("--dims", default="callerid,group_id,env",
                    help="[drivers] comma-separated subset of callerid,group_id,env "
                         "(default all three)")
    ap.add_argument("--stream", choices=("query", "index", "all"), default="query",
                    help="[drivers] which work stream to break down (default query = "
                         "callerid<>'index')")
    ap.add_argument("--limit", type=int, default=50, help="[drivers] max rows (default 50)")
    ap.add_argument("--cache-ttl-secs", type=int, default=None,
                    help="get_list cache TTL in seconds; default None = fresh/live read")
    ap.add_argument("--format", choices=("table", "json"), default="table")
    ap.add_argument("--region", default=None,
                    help="Region for warehouse routing. Overrides EF_DEFAULT_REGION "
                         "for this invocation (e.g. us-west-2, eu-central-1, "
                         "ca-central-1, ap-southeast-2, westus2). When unset, "
                         "EF_DEFAULT_REGION from the environment is used. StarRocks "
                         "is region-gated; the run reports the gate plainly if unsupported.")
    args = ap.parse_args(argv)

    try:
        if args.mode == "split":
            result = query_log.split_timeseries(
                args.core, args.shard_id, args.since, args.until,
                bucket_minutes=args.bucket_minutes, cache_ttl_secs=args.cache_ttl_secs,
                region=args.region)
            cols = ["bucket", "indexing", "query"]
            header_keys = ["table", "core", "shard_id", "bucket_minutes"]
        else:
            if not (args.baseline_since and args.baseline_until):
                print("error: --mode drivers requires --baseline-since and --baseline-until",
                      file=sys.stderr)
                return 2
            dims = [d.strip() for d in args.dims.split(",") if d.strip()]
            result = query_log.driver_breakdown(
                args.core, args.shard_id, args.since, args.until,
                args.baseline_since, args.baseline_until, dims=dims, stream=args.stream,
                limit=args.limit, cache_ttl_secs=args.cache_ttl_secs, region=args.region)
            cols = dims + ["spike_cnt", "base_cnt", "spike_per_min", "base_per_min", "ratio"]
            header_keys = ["table", "core", "shard_id", "stream"]
    except query_log.SearchQueryLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 — surface the full traceback for an unexpected failure
        traceback.print_exc()
        return 1

    if args.format == "json":
        print(json.dumps(result, default=str, indent=2))
    else:
        _print_rows(result, cols, header_keys)
        if args.mode == "drivers":
            print("\nratio = spike_per_min / base_per_min; ratio=None means NEW "
                  "(zero in baseline). Rates are per-minute (windows are unequal length).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
