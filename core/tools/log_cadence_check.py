#!/usr/bin/env python3
"""Stop hook: nudge the SE agent to keep its inputs/ log current.

Fires only for an active SE-logging session (a session-doc dated today exists in
inputs/). If that log looks stale relative to the session's activity, emits a
single, sentinel-guarded reminder to append before stopping — so it can never
deadlock the session. Maintainer/injector sessions (which don't create a
today-dated inputs log) are never nudged. Fail-silent; the safe default is to
let the agent stop.
"""
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUTS = os.path.join(ROOT, "inputs")
CACHE = os.path.join(ROOT, ".claude")   # sentinels here, never in immutable inputs/
STALE_SECONDS = 120


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("stop_hook_active"):
        return  # we already continued once from a stop hook -> don't loop
    session = data.get("session_id", "nosession")
    sentinel = os.path.join(CACHE, f".cadence-nudged-{session}")
    if os.path.exists(sentinel):
        return  # nudged once this session already -> never deadlock
    today = time.strftime("%Y-%m-%d")
    logs = glob.glob(os.path.join(INPUTS, f"{today}-*.md"))
    if not logs:
        return  # not an active SE-logging session -> stay silent
    newest = max(logs, key=os.path.getmtime)
    transcript = data.get("transcript_path", "")
    try:
        t_mtime = os.path.getmtime(transcript) if transcript else time.time()
    except OSError:
        t_mtime = time.time()
    if t_mtime - os.path.getmtime(newest) < STALE_SECONDS:
        return  # log is fresh relative to activity -> nothing to nudge
    try:
        os.makedirs(CACHE, exist_ok=True)
        open(sentinel, "w").close()
    except OSError:
        pass
    reason = (f"Your session log {os.path.relpath(newest, ROOT)} looks behind your recent work. "
              "Append the steps you haven't logged yet (via log-appender) before stopping.")
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
