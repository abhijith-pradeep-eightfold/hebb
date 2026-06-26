#!/usr/bin/env python3
"""Run a single read-only SQL query against the StarRocks data warehouse.

This is the bundled execution half of the `query-starrocks` skill. It exists as
a committed, skill-anchored script (not a scratch file) for two reasons:

1. **It passes the bash execution gate.** `core/tools/bash_exec_policy.py`
   auto-allows a single, non-compound python command whose script lives under
   the skill dir (`${CLAUDE_SKILL_DIR}/...`). A scratch `/tmp` script does not.
2. **It enforces read-only at the call site.** Reads go through
   `starrocks_utils.get_list`, which acquires an `op_type='read'` client (the
   `STARROCKS-CLUSTER-RO` cluster). Writes/DDL would have to go through the
   separate `execute_query` (`op_type='write'`) path, which this script never
   calls. On top of that connection-level guarantee we add a cheap, explicit
   guard here so a stray UPDATE/INSERT/DROP fails locally with a clear message
   instead of being shipped to the warehouse at all.

The SQL is read from a **file** (or `--sql`) rather than the command line on
purpose: warehouse predicates routinely contain `>`/`<` (e.g. `t_create >=
DATE_SUB(...)`), and any of `> < ; |` in the command string would trip the gate
and force an approval prompt. Passing a file path keeps the command operator-free
so the gate keeps auto-allowing it.

Run it (the gate-passing shape — never hardcode the interpreter):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/query_starrocks.py" /path/to/query.sql

`PYTHONPATH=$CODE_BASE/www` (not `$CODE_BASE`) is required: the codebase imports
`datawarehouse` / `db` as bare top-level packages rooted at `www/`.
"""
import argparse
import json
import re
import sys

# A read query begins with one of these. An allowlist (reject anything else) is
# safer than a denylist: there is no write/DDL verb we forget to ban. WITH covers
# CTEs that resolve to a SELECT; SHOW/DESCRIBE/EXPLAIN are read-only introspection.
_READ_ONLY_LEADERS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


def _strip_comments(s):
    """Return `s` with SQL comments blanked out: `/* ... */` block comments and
    `-- ...`/`# ...` line comments.

    Both checks below — the leader-keyword match and the multi-statement `;`
    scan — must run on this comment-stripped copy, so a `;` (or a leading verb)
    that appears *inside a comment* is never mistaken for query syntax. We keep
    the original text intact for the executor; this copy is validation-only, so
    its crude handling can't mangle a string literal in the query that runs.
    """
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)   # block comments
    s = re.sub(r"(--|#)[^\n]*", "", s)                   # line comments to EOL
    return s


def ensure_read_only(sql):
    """Return `sql` unchanged if it is a single read-only statement, else raise.

    We validate against a comment-stripped copy but hand the *original* text to
    the executor, so crude comment handling can never mangle a string literal in
    the query that actually runs.
    """
    s = sql.strip()
    if not s:
        raise ValueError("empty query")

    # Validate against a comment-stripped copy so neither check trips on a `;` or
    # a verb that lives inside a `--`/`/* */` comment (a `-- describe the query`
    # preamble, or a `;` in a note like `-- pin the window; normalize`).
    stripped = _strip_comments(s)

    # Find the leading keyword on the comment-stripped text.
    head = stripped.lstrip()
    m = re.match(r"[A-Za-z_]+", head)
    leader = m.group(0).upper() if m else None
    if leader not in _READ_ONLY_LEADERS:
        raise ValueError(
            f"read-only guard: query must begin with one of "
            f"{sorted(_READ_ONLY_LEADERS)} (got {leader!r}). "
            f"Writes go through execute_query, not this skill."
        )

    # Reject a second statement (e.g. `SELECT ...; DROP ...`). A lone trailing
    # `;` terminator is fine; a `;` anywhere before the end is not. Scanning the
    # comment-stripped copy means a `;` inside a comment is ignored; it may still
    # over-reject a `;` inside a string literal — that errs on the safe side.
    if ";" in stripped.rstrip().rstrip(";"):
        raise ValueError(
            "read-only guard: multiple statements are not allowed "
            "(found ';' mid-query)."
        )
    return s


def _print_table(rows):
    """Print dict rows as a simple aligned text table."""
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
    p = argparse.ArgumentParser(description="Run a read-only StarRocks query.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("sql_file", nargs="?",
                     help="path to a file containing the SQL (gate-safe default)")
    src.add_argument("--sql",
                     help="inline SQL; only safe for queries with no > < ; | "
                          "(those operators trip the bash gate)")
    p.add_argument("--cache-ttl-secs", type=int, default=None,
                   help="get_list cache TTL in seconds; default None = fresh/live read "
                        "(get_list's own default is 600)")
    p.add_argument("--format", choices=["table", "json"], default="table",
                   help="stdout format (default: table)")
    p.add_argument("--json-out", metavar="PATH",
                   help="also write the full result as JSON to PATH "
                        "(handy for feeding a downstream plot step)")
    args = p.parse_args(argv)

    if args.sql is not None:
        sql = args.sql
    else:
        with open(args.sql_file, "r") as fh:
            sql = fh.read()

    try:
        query = ensure_read_only(sql)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    # Imports are deferred until after the read-only guard so a bad query fails
    # instantly without paying the cost of loading the (heavy) codebase modules.
    from datawarehouse.starrocks import starrocks_utils
    from db.db_type import DBType

    print(f"[query-starrocks] running (cache_ttl_secs={args.cache_ttl_secs}):\n{query.strip()}",
          file=sys.stderr)
    try:
        rows = starrocks_utils.get_list(
            query, db_type=DBType.STARROCKS.value, cache_ttl_secs=args.cache_ttl_secs)
    except AssertionError:
        # get_list asserts db_type in DBType.all_starrocks_values(); in a region
        # where StarRocks isn't supported that list is empty and the assert fails.
        print("StarRocks is not supported in the resolved region (region gate). "
              "Report this exactly rather than guessing.", file=sys.stderr)
        return 3

    rows = list(rows or [])
    print(f"[query-starrocks] {len(rows)} row(s)", file=sys.stderr)

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(rows, fh, default=str, indent=2)
        print(f"[query-starrocks] wrote JSON to {args.json_out}", file=sys.stderr)

    if args.format == "json":
        print(json.dumps(rows, default=str, indent=2))
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
