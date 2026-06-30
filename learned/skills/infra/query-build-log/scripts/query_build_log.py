#!/usr/bin/env python3
"""Read deployment records from the `build_log` table (global db).

Bundled execution half of the `query-build-log` skill. It exists as a committed,
skill-anchored script (not a scratch file) so `core/tools/bash_exec_policy.py`
auto-allows it (a single, non-compound python command under the skill dir).

It reads the shared `build_log` access logic from `hebb_utils.deploy.build_log`,
which encodes the critical gotcha: **never `SELECT *` over a window** — select
scalar columns + push `LIKE`/range filters into SQL, then pull the heavy
compressed `data_json` only for a single matched id.

Run it (the gate-passing shape — never hardcode the interpreter):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_build_log.py" \
        --namespace mcp --start "2026-06-29 00:00:00" --end "2026-07-01 00:00:00"

`PYTHONPATH=$CODE_BASE/www` (not `$CODE_BASE`) is required: `BuildLog` is rooted
at `www/` (`internal.build_log`). This is a read-only load against the `global`
read-only cluster.
"""
import argparse
import json
import os
import sys

# Put the dir that contains `hebb_utils/` (i.e. learned/) on sys.path so the
# shared module resolves regardless of nesting depth. Imports don't affect the
# bash gate, which keys on the *invoked* path being under the skill dir.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate hebb_utils/ above %s" % __file__)
    _d = _parent
sys.path.insert(0, _d)


def _print_table(rows):
    if not rows:
        print("(0 rows)")
        return
    cols = list(rows[0].keys())
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(cols[i]), *(len(row[i]) for row in cells)) for i in range(len(cols))]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*cols))
    print(fmt.format(*["-" * w for w in widths]))
    for row in cells:
        print(fmt.format(*row))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Read build_log deployment records (global db). Scalar columns "
                    "only by default; --full pulls data_json for matched ids.")
    p.add_argument("--namespace", help="app namespace LIKE match (e.g. mcp, api)")
    p.add_argument("--status", help="status LIKE match (e.g. 'Deployment', 'Building')")
    p.add_argument("--tag", help="tag LIKE match (e.g. stage, prod, canary, qa)")
    p.add_argument("--start", help="t_create >= this (e.g. '2026-06-29 00:00:00')")
    p.add_argument("--end", help="t_create < this (e.g. '2026-07-01 00:00:00')")
    p.add_argument("--limit", type=int, default=100, help="max rows (default 100)")
    p.add_argument("--full", action="store_true",
                   help="also fetch + print the decompressed data_json for each "
                        "matched row (pulls the heavy payload per id)")
    p.add_argument("--data-json-truncate", type=int, default=300,
                   help="truncate each data_json value to N chars in --full output "
                        "(default 300; 0 = no truncation)")
    p.add_argument("--format", choices=["table", "json"], default="table")
    args = p.parse_args(argv)

    # Deferred import after arg-parse so --help is instant and the heavy codebase
    # only loads when we actually query.
    from hebb_utils.deploy.build_log import build_filter, query_window, fetch_full

    filter_by = build_filter(namespace=args.namespace, status=args.status,
                             tag=args.tag, start=args.start, end=args.end)
    if not filter_by:
        print("provide at least one of --namespace/--status/--tag/--start/--end",
              file=sys.stderr)
        return 2

    rows = query_window(filter_by, limit=args.limit)
    print("[query-build-log] %d row(s) for filter %r" % (len(rows), filter_by),
          file=sys.stderr)

    if args.format == "json":
        print(json.dumps(rows, default=str, indent=2))
    else:
        _print_table(rows)

    if args.full:
        trunc = args.data_json_truncate
        for r in rows:
            dj = fetch_full(r["id"])
            print("--- full data_json id=%s ns=%s status=%r tag=%s ---" % (
                r.get("id"), r.get("namespace"), r.get("status"), r.get("tag")))
            if trunc and trunc > 0:
                dj = {k: (str(v)[:trunc] + "...[trunc]" if len(str(v)) > trunc else v)
                      for k, v in dj.items()}
            print(json.dumps(dj, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
