#!/usr/bin/env python3
"""Resolve the CODEOWNERS owner(s) of repo-relative path(s).

Applies GitHub CODEOWNERS semantics — gitignore-style globs, and **the LAST
matching pattern wins**. Prints, per path, the winning pattern (with its line
number) and the owner set, or "(none)" when no pattern matches. Pure stdlib,
no repo imports — runs anywhere the CODEOWNERS file is readable.

Usage:
    codeowners_for.py <path> [<path> ...] [--codeowners FILE]
Defaults --codeowners to $CODE_BASE/.github/CODEOWNERS, then ./.github/CODEOWNERS.

Example:
    codeowners_for.py www/processor/sync_ats_operation.py
"""
from __future__ import absolute_import
import argparse
import os
import re
import sys


def _pattern_to_regex(pat):
    """Convert a CODEOWNERS/gitignore pattern to a regex over a repo-relative path.

    Rules: a leading '/' or any internal '/' anchors to repo root; a pattern with
    no '/' floats (matches at any depth); a trailing '/' matches directory
    contents; '*' matches within a path segment, '**' across segments.
    """
    raw = pat
    dir_only = raw.endswith("/")
    anchored = raw.startswith("/") or ("/" in raw.rstrip("/"))
    body = raw.strip("/")
    esc = re.escape(body)
    esc = (esc.replace(r"\*\*", "\x00")   # ** placeholder
              .replace(r"\*", "[^/]*")    # * = within-segment
              .replace("\x00", ".*")       # ** = across segments
              .replace(r"\?", "[^/]"))
    prefix = "^" if anchored else r"(^|.*/)"
    suffix = r"/.*$" if dir_only else r"(/.*)?$"
    return re.compile(prefix + esc + suffix)


def _load_rules(path):
    """Parse CODEOWNERS into an ordered list of (lineno, pattern, owners, regex)."""
    rules = []
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            rules.append((lineno, parts[0], parts[1:], _pattern_to_regex(parts[0])))
    return rules


def resolve(path, rules):
    """Return (lineno, pattern, owners) of the LAST matching rule, or None."""
    norm = path.strip("/")
    winner = None
    for lineno, pat, owners, rx in rules:
        if rx.match(norm):
            winner = (lineno, pat, owners)   # last match wins
    return winner


def main(argv=None):
    ap = argparse.ArgumentParser(description="Resolve CODEOWNERS owner(s) for path(s).")
    ap.add_argument("paths", nargs="+", help="repo-relative path(s)")
    ap.add_argument("--codeowners", help="path to CODEOWNERS file")
    args = ap.parse_args(argv)

    co = args.codeowners
    if not co:
        base = os.environ.get("CODE_BASE", ".")
        for cand in (os.path.join(base, ".github/CODEOWNERS"),
                     ".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
            if os.path.isfile(cand):
                co = cand
                break
    if not co or not os.path.isfile(co):
        print("error: CODEOWNERS file not found (tried --codeowners / "
              "$CODE_BASE/.github/CODEOWNERS)", file=sys.stderr)
        return 2

    rules = _load_rules(co)
    print(f"CODEOWNERS: {co}  ({len(rules)} rules)\n")
    for p in args.paths:
        w = resolve(p, rules)
        if w:
            lineno, pat, owners = w
            print(f"{p}\n    owners : {' '.join(owners)}\n    rule   : {pat}  (line {lineno})\n")
        else:
            print(f"{p}\n    owners : (none — no matching CODEOWNERS rule)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
