#!/usr/bin/env python3
"""Trace a Solr core+shard's env=processor query traffic to its root processor ops.

One invocation runs the whole bridge end to end (no judgment between the steps, so it
is one bundled script — see learned/wiki/data-warehouse/search-query-log
"sequence_message_id" and learned/wiki/processor/tracing-processor-op-lineage):

  1. pull the env='processor' query rows for a core+shard+window, grouped by their
     sequence_message_id (the processor SMID that issued each query)  -- query_log
  2. walk each DISTINCT SMID's processor_parent_msg_id chain to its root op  -- event_log
  3. group the SMIDs by their root->target op-trace and report query volume per chain.

The reusable stages live in the shared utils hebb_utils.solr.query_log
(processor_query_smids) and hebb_utils.processor.event_log (walk_parent_chain), shared
with the query-solr-load and trace-processor-op skills; this file is the thin combined
runner over them. Read-only by construction.

Run (PYTHONPATH must root at www/ — the warehouse utils resolve www-rooted config):
    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/trace_solr_query_ops.py" \
        --core profiles --shard-id 10 --since "2026-06-28 00:00:00" \
        --until "2026-06-29 00:00:00" --region eu-central-1
"""
from __future__ import absolute_import

import argparse
import json
import os
import sys
import traceback
from collections import OrderedDict

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
from hebb_utils.solr import query_log        # noqa: E402
from hebb_utils.processor import event_log    # noqa: E402


def _chain_signature(chain):
    """The root->target op-trace string — the grouping key for identical chains."""
    return " -> ".join((hop.get("operation0") or "?") for hop in reversed(chain))


def _root_op(chain):
    # chain[-1] is the deepest hop: the parentless root, or the deepest knowable op when
    # the walk stopped at a non-UUID dispatch parent / NO ROW FOUND (see event_log).
    return chain[-1].get("operation0") if chain else None


def _trace_all(rows, max_depth, region):
    """Trace each DISTINCT sequence_message_id once -> {smid: {signature, root_op}}."""
    traced = {}
    for r in rows:
        smid = r.get("sequence_message_id")
        if not smid or smid in traced:
            continue
        try:
            chain = event_log.walk_parent_chain(
                smid, max_depth=max_depth, region=region)["chain"]
            traced[smid] = {"signature": _chain_signature(chain), "root_op": _root_op(chain)}
        except event_log.ProcessorEventLogError as exc:
            traced[smid] = {"signature": f"<trace error: {exc}>", "root_op": None}
    return traced


def _group(rows, traced):
    """Aggregate the query rows by their traced op-trace signature."""
    groups = OrderedDict()
    total_q = 0
    for r in rows:
        smid = r.get("sequence_message_id")
        t = traced.get(smid, {"signature": "<untraced>", "root_op": None})
        g = groups.setdefault(t["signature"], {
            "op_trace": t["signature"], "root_op": t["root_op"], "query_cnt": 0,
            "smids": set(), "group_ids": set(), "callerids": set()})
        qc = int(r.get("query_cnt") or 0)
        g["query_cnt"] += qc
        total_q += qc
        g["smids"].add(smid)
        if r.get("group_id"):
            g["group_ids"].add(r["group_id"])
        if r.get("callerid"):
            g["callerids"].add(r["callerid"])
    out = [{"op_trace": g["op_trace"], "root_op": g["root_op"], "query_cnt": g["query_cnt"],
            "distinct_smids": len(g["smids"]), "group_ids": sorted(g["group_ids"]),
            "callerids": sorted(g["callerids"]), "sample_smids": sorted(g["smids"])[:5]}
           for g in groups.values()]
    out.sort(key=lambda x: x["query_cnt"], reverse=True)
    return out, total_q


def _print_human(result):
    print(f"core={result['core']}  shard_id={result['shard_id']}  "
          f"window={result['window'][0]} .. {result['window'][1]}  (env=processor query traffic)")
    print(f"distinct SMIDs: {result['distinct_smids']}   total query rows: {result['total_query_rows']}\n")
    if not result["groups"]:
        print("No env=processor query rows for this core+shard+window — "
              "no processor-issued queries to trace.")
        return
    print("=== ROOT-OP CHAINS (grouped by op trace; highest query volume first) ===")
    for g in result["groups"]:
        print(f"\n  query_cnt={g['query_cnt']}  distinct_smids={g['distinct_smids']}  "
              f"root_op={g['root_op']}")
        print(f"    op trace : {g['op_trace']}")
        print(f"    tenants  : {', '.join(g['group_ids']) or '(none)'}")
        print(f"    callerids: {', '.join(g['callerids']) or '(none)'}")
        print(f"    sample SMIDs: {', '.join(g['sample_smids'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Trace a Solr core+shard's env=processor query traffic to its root processor ops.")
    ap.add_argument("--core", required=True, help="Solr core / collection (e.g. profiles)")
    ap.add_argument("--shard-id", required=True, help="shard id (int)")
    ap.add_argument("--since", required=True, help="UTC window start 'YYYY-MM-DD[ HH:MM[:SS]]'")
    ap.add_argument("--until", required=True, help="UTC window end 'YYYY-MM-DD[ HH:MM[:SS]]'")
    ap.add_argument("--limit", type=int, default=500,
                    help="max distinct SMIDs to pull then trace (default 500). The trace is "
                         "the slow part — one warehouse round-trip per hop per SMID — so keep "
                         "the window tight.")
    ap.add_argument("--max-depth", type=int, default=50, help="per-chain walk cap (default 50)")
    ap.add_argument("--region", default=None,
                    help="warehouse region; sets EF_DEFAULT_REGION for this invocation "
                         "(us-west-2, eu-central-1, ca-central-1, ap-southeast-2, westus2). "
                         "When unset, EF_DEFAULT_REGION from the environment is used.")
    ap.add_argument("--format", choices=("human", "json"), default="human")
    args = ap.parse_args(argv)

    pulled = query_log.processor_query_smids(
        args.core, args.shard_id, args.since, args.until, limit=args.limit, region=args.region)
    rows = pulled["rows"]
    traced = _trace_all(rows, args.max_depth, args.region)
    groups, total_q = _group(rows, traced)

    result = {"core": args.core, "shard_id": int(args.shard_id),
              "window": [args.since, args.until], "env": "processor",
              "total_query_rows": total_q, "distinct_smids": len(traced), "groups": groups}

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (query_log.SearchQueryLogError, event_log.ProcessorEventLogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
