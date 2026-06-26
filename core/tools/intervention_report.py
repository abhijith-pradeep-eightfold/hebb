#!/usr/bin/env python3
"""Cross-doc intervention report — the "analyse all inputs" learning-loop view.

Scans every session-doc in inputs/, tabulates [INTERVENTION] entries by type and
source, and reports the human-intervention count per session (listed oldest-first
by the YYYY-MM-DD filename prefix, so the trend over time is visible). The
intervention rate is the canonical signal of whether the learning loop is closing
— a prioritization signal for the maintainer, not a target to optimize. Read-only.
"""
import glob
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUTS = os.path.join(ROOT, "inputs")


def parse_doc(path):
    text = open(path, encoding="utf-8").read()
    out = []
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        head = block.split("\n", 1)[0]
        if "[INTERVENTION]" not in head:
            continue

        def field(name):
            m = re.search(rf"\*\*{name}:\*\*\s*(.+)", block)
            return m.group(1).strip() if m else ""

        out.append({"type": field("type"), "source": field("source"),
                    "missing": field("what was missing")})
    return out


def main():
    docs = sorted(glob.glob(os.path.join(INPUTS, "*.md")))
    if not docs:
        print("no session-docs in inputs/")
        return
    by_type, by_source, by_missing = Counter(), Counter(), Counter()
    grand = 0
    print(f"{'session-doc':<50} {'interventions':>13}")
    print("-" * 65)
    for d in docs:
        ivs = parse_doc(d)
        grand += len(ivs)
        for iv in ivs:
            by_type[iv["type"] or "?"] += 1
            by_source[iv["source"] or "?"] += 1
            if iv["missing"]:
                by_missing[iv["missing"]] += 1
        print(f"{os.path.basename(d):<50} {len(ivs):>13}")
    print("-" * 65)
    n = len(docs)
    print(f"{'TOTAL (' + str(n) + ' sessions)':<50} {grand:>13}")
    print(f"\navg interventions/session: {grand / n:.2f}")
    if by_type:
        print("by type:   " + ", ".join(f"{k}={v}" for k, v in by_type.most_common()))
        print("by source: " + ", ".join(f"{k}={v}" for k, v in by_source.most_common()))
    recurring = [(m, c) for m, c in by_missing.most_common() if c > 1]
    if recurring:
        print("\nrecurring gaps (candidates that clear Rule A1's 'recurs across docs' bar):")
        for m, c in recurring:
            print(f"  {c}x  {m}")
    print("\n(intervention rate is a prioritization signal — falling over time means the "
          "loop is closing — not a target to game.)")


if __name__ == "__main__":
    main()
