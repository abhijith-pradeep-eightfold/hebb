#!/usr/bin/env python3
"""Tabulate a CloudWatch `get-metric-statistics` CPUUtilization series and flag breach buckets.

This is the deterministic analysis half of the `inspect-cloudwatch-cpu` skill.
The runtime judgment (which alarm, which window, which instance) lives in the
skill body and is exercised by the AWS CLI calls the agent runs under user
approval; *this* script is a pure transform over the JSON those calls saved, so
it is safe to run unattended (no external system is touched, no `$CODE_BASE`
import). It is bundled under the skill dir so the bash execution policy
(`core/tools/bash_exec_policy.py`) auto-allows a clean `python .../scripts/...`
invocation without a prompt.

Input is one or more saved `get-metric-statistics` JSON files, passed **by path**
(never inline) so no shell metacharacter reaches the command line. Each file is
the raw AWS output: `{"Label": "...", "Datapoints": [{"Timestamp": "...",
"Average": .., "Maximum": ..}, ...]}`. AWS returns datapoints unordered; we sort
by timestamp before reporting.

For each series the script prints:
  - count of datapoints, and the time span covered;
  - min / max / mean of the chosen statistic;
  - the count of buckets at or above the threshold, and the contiguous
    high-CPU block(s) — so you can see whether a breach was sustained (a real
    alarm-clearing spike) or a one-minute blip.

Run it (the gate-passing shape — never hardcode the interpreter; no $CODE_BASE
import is needed, but PYTHONPATH is harmless if set):

    "$VSCODE_PYTHON" \
        "${CLAUDE_SKILL_DIR}/scripts/analyze_cpu_metrics.py" \
        --threshold 75 --stat Average /path/to/cpu_r0.json /path/to/cpu_r1.json

`--label` tags each file in the output (repeatable, paired positionally with the
files); without it the JSON `Label` / filename is used.
"""
import argparse
import json
import os
import sys
from datetime import datetime


def _parse_ts(s):
    """Parse an AWS ISO-8601 timestamp (e.g. '2026-06-15T08:20:00Z' or with offset)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _load_series(path, stat):
    with open(path) as fh:
        doc = json.load(fh)
    pts = doc.get("Datapoints", []) if isinstance(doc, dict) else list(doc)
    rows = []
    for p in pts:
        ts = p.get("Timestamp")
        val = p.get(stat)
        if ts is None or val is None:
            continue
        rows.append((_parse_ts(ts), float(val)))
    rows.sort(key=lambda r: r[0])
    return rows, (doc.get("Label") if isinstance(doc, dict) else None)


def _contiguous_breaches(rows, threshold):
    """Return list of (start_ts, end_ts, n, peak) for runs of consecutive >= threshold buckets."""
    blocks = []
    run = []
    for ts, val in rows:
        if val >= threshold:
            run.append((ts, val))
        elif run:
            blocks.append(run)
            run = []
    if run:
        blocks.append(run)
    return [
        (b[0][0], b[-1][0], len(b), max(v for _, v in b))
        for b in blocks
    ]


def _report(label, rows, threshold, stat):
    print(f"=== {label} ===")
    if not rows:
        print("  (no datapoints)")
        return
    vals = [v for _, v in rows]
    n = len(vals)
    n_breach = sum(1 for v in vals if v >= threshold)
    print(f"  {stat}: {n} buckets, span {rows[0][0].isoformat()} .. {rows[-1][0].isoformat()}")
    print(f"  min={min(vals):.1f}  max={max(vals):.1f}  mean={sum(vals)/n:.1f}")
    print(f"  buckets >= {threshold}: {n_breach}")
    blocks = _contiguous_breaches(rows, threshold)
    if blocks:
        print(f"  contiguous >= {threshold} block(s):")
        for start, end, count, peak in blocks:
            kind = "SUSTAINED" if count >= 5 else "blip"
            print(f"    {start.isoformat()} .. {end.isoformat()}  "
                  f"({count} bucket(s), peak {peak:.1f})  [{kind}]")
    else:
        print(f"  no bucket reached {threshold}")
    print()


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Tabulate CloudWatch CPUUtilization series and flag breach buckets.")
    p.add_argument("json_files", nargs="+",
                   help="paths to saved get-metric-statistics JSON files")
    p.add_argument("--threshold", type=float, default=75.0,
                   help="breach threshold for the chosen statistic (default 75.0, "
                        "the Solr CPU alarm threshold)")
    p.add_argument("--stat", default="Average", choices=["Average", "Maximum", "Minimum", "Sum"],
                   help="which statistic to analyze (default Average — matches the alarm)")
    p.add_argument("--label", action="append", default=[],
                   help="label for a file (repeatable, paired with json_files in order)")
    args = p.parse_args(argv)

    for i, path in enumerate(args.json_files):
        if not os.path.exists(path):
            print(f"missing file: {path}", file=sys.stderr)
            return 2
        rows, json_label = _load_series(path, args.stat)
        label = (args.label[i] if i < len(args.label)
                 else (json_label or os.path.basename(path)))
        _report(label, rows, args.threshold, args.stat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
