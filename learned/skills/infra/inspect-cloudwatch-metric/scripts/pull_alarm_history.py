#!/usr/bin/env python3
"""Pull a CloudWatch alarm's state-transition history and summarize how chronic it is.

Answers "is this alarm chronic or rare?" for an oncall page: reads the alarm's
`StateUpdate` history via `describe-alarm-history`, extracts the transitions **into**
the ALARM state (the trigger events), and reports the most recent trigger (this
incident's onset), the prior trigger, and the gap between them — so you can tell a
first-page-in-months from a daily flapper. Shells out to the read-only AWS CLI
(region/profile from the environment). Bundled so it runs unattended.

CAVEAT: CloudWatch retains alarm history for ~14 days. A prior trigger older than the
retention window will NOT appear here — if only this incident's trigger shows, the alarm
may still have a much older prior page; cross-check PagerDuty/incident history for the
longer view. `--days-back` cannot see past CloudWatch's retention regardless of value.

Usage:
    pull_alarm_history.py --alarm-name "[us-west-2] P1 Solr CPU Util Too High on profiles shard 21 replica 1 (...)"
        [--region us-west-2] [--days-back 14] [--max-items 200]
"""
from __future__ import absolute_import
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys


def _aws(args):
    """Run an aws CLI command, return parsed JSON stdout (or None on failure)."""
    try:
        out = subprocess.run(["aws"] + args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"warning: aws call failed: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"warning: aws {' '.join(args[:2])} exited {out.returncode}: "
              f"{out.stderr.strip()[:300]}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout or "null")
    except json.JSONDecodeError:
        return None


def _alarm_transitions(items):
    """Return [(timestamp, old_state, summary), ...] for transitions INTO ALARM, newest first."""
    triggers = []
    for it in items or []:
        if it.get("HistoryItemType") != "StateUpdate":
            continue
        new_state = old_state = None
        data = it.get("HistoryData")
        if data:
            try:
                parsed = json.loads(data)
                new_state = (parsed.get("newState") or {}).get("stateValue")
                old_state = (parsed.get("oldState") or {}).get("stateValue")
            except (json.JSONDecodeError, AttributeError):
                pass
        if new_state == "ALARM":
            triggers.append((it.get("Timestamp"), old_state, it.get("HistorySummary")))
    return triggers


def _parse_ts(ts):
    """Parse a CloudWatch ISO8601 timestamp to a naive UTC datetime, or None."""
    if not ts:
        return None
    s = ts.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return _dt.datetime.strptime(s, fmt).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Summarize a CloudWatch alarm's trigger history (how chronic it is).")
    ap.add_argument("--alarm-name", required=True, help="exact alarm name (from the page or describe-alarms)")
    ap.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
    ap.add_argument("--days-back", type=int, default=14,
                    help="lookback window in days (default 14; CloudWatch retains ~14d max)")
    ap.add_argument("--max-items", type=int, default=200, help="max history items to fetch")
    args = ap.parse_args(argv)

    end = _dt.datetime.utcnow()
    start = end - _dt.timedelta(days=args.days_back)
    data = _aws(["cloudwatch", "describe-alarm-history", "--region", args.region,
                 "--alarm-name", args.alarm_name, "--history-item-type", "StateUpdate",
                 "--start-date", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "--end-date", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "--max-items", str(args.max_items), "--output", "json"])

    print(f"alarm={args.alarm_name}")
    print(f"region={args.region}  lookback={args.days_back}d "
          f"(CloudWatch retains ~14d — older triggers not shown)\n")
    if data is None:
        print("(could not read alarm history — check alarm name / region / permissions)")
        return 0
    triggers = _alarm_transitions(data.get("AlarmHistoryItems"))
    if not triggers:
        print("No transitions INTO ALARM in the lookback window — alarm did not fire "
              "recently (within retention).")
        return 0

    print(f"{len(triggers)} transition(s) into ALARM (newest first):")
    print(f"{'timestamp (UTC)':28s} {'from':10s} summary")
    print("-" * 80)
    for ts, old, summary in triggers:
        print(f"{str(ts):28s} {str(old or '?'):10s} {str(summary or '')[:40]}")
    print("-" * 80)

    latest = _parse_ts(triggers[0][0])
    print(f"\nmost recent trigger (this incident's onset): {triggers[0][0]}")
    if len(triggers) >= 2:
        prior = _parse_ts(triggers[1][0])
        print(f"prior trigger: {triggers[1][0]}")
        if latest and prior:
            gap = latest - prior
            print(f"gap since prior trigger: {gap.days}d {gap.seconds // 3600}h "
                  f"({'flapping' if gap.total_seconds() < 86400 else 'spaced out'})")
    else:
        print("only one trigger in the retention window — if this is the alarm's first page "
              "in a long time, a prior trigger may predate the ~14d retention "
              "(confirm via PagerDuty).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
