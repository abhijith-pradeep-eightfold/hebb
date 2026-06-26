#!/usr/bin/env python3
"""SessionStart hook: inject the Hebb wiki index into the session as context.

So every session starts knowing what knowledge is already compiled (and, via the
index's Skills section, what capabilities exist) instead of re-deriving it. Emits
the wiki index as `additionalContext`. Read-only; fail-silent; always exits 0.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX = os.path.join(ROOT, "wiki", "index.md")
CAP = 10000  # SessionStart additionalContext is capped ~10k chars


def main():
    try:
        json.load(sys.stdin)  # consume hook stdin; we don't need its fields
    except Exception:
        pass
    try:
        with open(INDEX, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return  # no index yet -> inject nothing
    if len(text) > CAP:
        text = text[:CAP] + "\n\n[...truncated; Read wiki/index.md for the rest...]"
    context = ("# Hebb wiki index (compiled knowledge)\n"
               "Consult these pages (and the Skills catalog they link) before re-deriving "
               "anything from source; follow the wikilinks.\n\n" + text)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}))


if __name__ == "__main__":
    main()
