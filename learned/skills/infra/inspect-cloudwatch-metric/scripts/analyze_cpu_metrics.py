#!/usr/bin/env python3
"""Tabulate a CloudWatch `get-metric-statistics` CPUUtilization series and flag breach buckets.

This is the deterministic analysis half of the `inspect-cloudwatch-metric` skill.
The runtime judgment (which alarm, which window, which instance) lives in the
skill body and is exercised by the AWS CLI calls the agent runs under user
approval; *this* script is a pure transform over the JSON those calls saved, so
it is safe to run unattended (no external system is touched, no `$CODE_BASE`
import). It is bundled under the skill dir so the bash execution policy
(`core/tools/bash_exec_policy.py`) auto-allows a clean `python .../scripts/...`
invocation without a prompt.

The analysis logic itself lives in the shared module
`learned/hebb_utils/aws/cloudwatch.py` (also used by the `solr-shard-cpu` skill);
this script is the thin CLI entry point over it — it parses args, loads each saved
JSON file, and prints the per-series breach report. Importing the shared module
does not change the gate-passing run shape: the *invoked* path is still
`${CLAUDE_SKILL_DIR}/scripts/analyze_cpu_metrics.py`.

Input is one or more saved `get-metric-statistics` JSON files, passed **by path**
(never inline) so no shell metacharacter reaches the command line. Each file is
the raw AWS output: `{"Label": "...", "Datapoints": [{"Timestamp": "...",
"Average": .., "Maximum": ..}, ...]}`. AWS returns datapoints unordered; the
shared module sorts by timestamp before reporting.

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
import os
import sys

# Import the shared analysis logic from learned/hebb_utils/. Walk up to the dir that
# contains `hebb_utils/` (i.e. learned/) and put it on sys.path — no hardcoded depth.
# `hebb_utils` never clashes with vscode's own top-level `utils` package.
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _parent = os.path.dirname(_d)
    if _parent == _d:
        raise RuntimeError("could not locate learned/hebb_utils/ above this script")
    _d = _parent
sys.path.insert(0, _d)
from hebb_utils.aws.cloudwatch import load_series, report  # noqa: E402


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
        rows, json_label = load_series(path, args.stat)
        label = (args.label[i] if i < len(args.label)
                 else (json_label or os.path.basename(path)))
        report(label, rows, args.threshold, args.stat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
