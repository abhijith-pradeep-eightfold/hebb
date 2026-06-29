#!/usr/bin/env python3
"""Read a value from the www global config via config.get.

The whole point of this script is its minimalism: a plain
`from config import config; config.get(config_name, field_name=...)`.
Config is BROADCAST to all regions, so a plain read from the box's default
environment (us-west-2 signing) reflects every region's value, using the box's
OWN credentials and the global config DB. Therefore this script:

  * does NOT set or honor EF_DEFAULT_REGION as a "read region X" lever,
  * does NOT do any boto3 / STS / assume-role / IAM handling.

Both of those are self-inflicted dead-ends (SignatureDoesNotMatch -> a bogus
AccessDenied on secrets-manager-ro). See learned/wiki/infra/config-get.md.

Run it (gate-passing shape; config lives under www, so root at $CODE_BASE/www):

    PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" \
        /home/ec2-user/hebb/.claude/skills/skill-writer/scripts/read_config.py \
        <config_name> [--field-name FIELD] [--has KEY]
"""
from __future__ import absolute_import

import argparse
import sys


def main():
    ap = argparse.ArgumentParser(description="Read a value from the www global config via config.get().")
    ap.add_argument("config_name", help="The config name, e.g. 'alarm_config'.")
    ap.add_argument("--field-name", "-f", default=None,
                    help="Optional field within the config (config.get(config_name, field_name=...)). "
                         "A missing field returns None.")
    ap.add_argument("--has", dest="has_key", default=None,
                    help="When the resolved value is a dict, also report whether this key is present.")
    args = ap.parse_args()

    # Plain import + read. No region override, no IAM/STS handling.
    from config import config

    if args.field_name is not None:
        val = config.get(args.config_name, field_name=args.field_name)
        print("config.get(%r, field_name=%r) =" % (args.config_name, args.field_name), repr(val))
        print("is None:", val is None)
    else:
        val = config.get(args.config_name)
        if isinstance(val, dict):
            keys = sorted(val.keys())
            preview = keys[:50]
            print("config.get(%r) -> dict with %d keys: %s%s"
                  % (args.config_name, len(keys), preview, " ..." if len(keys) > 50 else ""))
        else:
            print("config.get(%r) =" % (args.config_name,), repr(val))

    if args.has_key is not None:
        if isinstance(val, dict):
            print("key %r present:" % (args.has_key,), args.has_key in val)
        else:
            print("--has given but resolved value is not a dict (type=%s); cannot test membership."
                  % type(val).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
