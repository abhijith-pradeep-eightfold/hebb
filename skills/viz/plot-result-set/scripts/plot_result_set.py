#!/usr/bin/env python3
"""Plot a result set (a JSON array of row dicts) to a PNG.

The generic, domain-agnostic half of the `plot-result-set` skill. It is the
downstream of any step that emits rows as JSON — e.g. the `query-starrocks`
runner's `--json-out PATH`. It draws one x/y series as a bar or line chart and
saves a PNG headlessly (matplotlib **Agg** backend, no display needed).

Why this is its own bundled script, separate from the query runner:

* **Orthogonality.** Charting rows is generic matplotlib with no `$CODE_BASE`
  coupling — it would serve any domain unchanged. It is its own capability, not
  a feature of the StarRocks query skill (which stays purely about composing and
  running read-only SQL).
* **It passes the bash gate.** Living under the skill dir
  (`${CLAUDE_SKILL_DIR}/scripts/...`) makes `core/tools/bash_exec_policy.py`
  auto-allow a single, non-compound run — no per-run approval prompt.

Pure transform: JSON in, PNG out. It reads only the file you name and writes
only the `--out` PNG, so it carries no read-only/write allowlist (nothing
external to guard) — just input validation that fails fast with a clear message.

Input shape: a JSON array of objects, e.g. exactly what the query runner writes:

    [ {"bucket_start": "2026-06-24 13:05:00", "n": 81234}, ... ]

Run it (the gate-passing shape — note: NO PYTHONPATH needed, matplotlib has no
`www` dependency; never hardcode the interpreter):

    "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/plot_result_set.py" \
        /path/to/rows.json --x bucket_start --y n --kind bar \
        --out /path/to/chart.png

`matplotlib` 3.10.0 ships in the `$VSCODE_PYTHON` venv.
"""
import argparse
import json
import sys
from datetime import datetime


def _load_rows(path):
    with open(path, "r") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON array of row objects")
    if not all(isinstance(r, dict) for r in data):
        raise ValueError(f"{path}: every element must be an object (row dict)")
    return data


def _require_cols(rows, *cols):
    missing = [c for c in cols if c not in rows[0]]
    if missing:
        raise ValueError(
            f"column(s) {missing} not in the rows; available: {sorted(rows[0])}")


def _coerce_y(rows, ycol):
    out = []
    for i, r in enumerate(rows):
        try:
            out.append(float(r[ycol]))
        except (TypeError, ValueError):
            raise ValueError(f"row {i}: {ycol}={r[ycol]!r} is not numeric")
    return out


def _parse_x(rows, xcol, as_datetime):
    xs = [r[xcol] for r in rows]
    if not as_datetime:
        return xs
    parsed = []
    for i, v in enumerate(xs):
        if isinstance(v, datetime):
            parsed.append(v)
            continue
        try:
            parsed.append(datetime.fromisoformat(str(v)))
        except ValueError:
            raise ValueError(
                f"row {i}: --x-datetime set but {xcol}={v!r} is not ISO datetime")
    return parsed


def main(argv=None):
    p = argparse.ArgumentParser(description="Plot a JSON result set to a PNG.")
    p.add_argument("data_json", help="path to a JSON array of row objects")
    p.add_argument("--x", required=True, help="column for the x-axis")
    p.add_argument("--y", required=True, help="column for the y-axis (numeric)")
    p.add_argument("--out", required=True, metavar="PATH", help="output PNG path")
    p.add_argument("--kind", choices=["bar", "line"], default="bar")
    p.add_argument("--x-datetime", action="store_true",
                   help="parse x values as ISO datetimes and format the time axis")
    # Title/labels are optional and default to the column names. If passed
    # inline they must be metachar-free (>,<,;,|,`,$( ) — those trip the bash
    # gate, same caveat as the query runner's --sql.
    p.add_argument("--title", default=None)
    p.add_argument("--xlabel", default=None)
    p.add_argument("--ylabel", default=None)
    args = p.parse_args(argv)

    try:
        rows = _load_rows(args.data_json)
        _require_cols(rows, args.x, args.y)
        y = _coerce_y(rows, args.y)
        x = _parse_x(rows, args.x, args.x_datetime)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"plot-result-set: {e}", file=sys.stderr)
        return 2

    # Import matplotlib only after inputs validate; pin the headless backend
    # BEFORE importing pyplot so it never needs a display.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 5))
    if args.x_datetime:
        import matplotlib.dates as mdates
        if args.kind == "bar":
            # width in days; 5-min buckets ~ 0.0035d. Derive from spacing so bars
            # don't overlap or leave gaps regardless of bucket size.
            width = (min((x[i + 1] - x[i]).total_seconds() for i in range(len(x) - 1))
                     / 86400 * 0.9) if len(x) > 1 else 0.01
            ax.bar(x, y, width=width, align="edge")
        else:
            ax.plot(x, y, marker="o", markersize=3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        fig.autofmt_xdate()
    else:
        idx = range(len(x))
        if args.kind == "bar":
            ax.bar(list(idx), y)
        else:
            ax.plot(list(idx), y, marker="o", markersize=3)
        ax.set_xticks(list(idx))
        ax.set_xticklabels([str(v) for v in x], rotation=45, ha="right")

    ax.set_xlabel(args.xlabel or args.x)
    ax.set_ylabel(args.ylabel or args.y)
    ax.set_title(args.title or f"{args.y} by {args.x}")
    fig.tight_layout()
    fig.savefig(args.out, dpi=120)
    print(f"plot-result-set: wrote {args.out} ({len(rows)} points, kind={args.kind})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
