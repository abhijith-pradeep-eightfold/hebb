"""Resolve EC2 DNS hostnames to InstanceIds via read-only `aws ec2 describe-instances`.

Shared by `solr-shard-dns-lookup` and `solr-shard-cpu`. This module is www-free
(no `$CODE_BASE` import), but it lives in `hebb_utils` alongside the
vscode-dependent modules so the whole shared library has one collision-free
import root (`hebb_utils`, never `utils`).
"""
import json
import subprocess
import sys


def resolve_instance_id(dns_hostname, region):
    """Resolve a DNS hostname to an EC2 InstanceId via describe-instances.

    Returns the InstanceId string, or "UNKNOWN" if the call fails or returns no
    results. Never raises — errors are reported as "UNKNOWN" (with a warning on
    stderr) so callers can continue with partial results.
    """
    try:
        result = subprocess.run(
            [
                "aws", "ec2", "describe-instances",
                "--region", region,
                "--filters", f"Name=dns-name,Values={dns_hostname}",
                "--query", "Reservations[*].Instances[*].InstanceId",
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"  warning: describe-instances failed for {dns_hostname}: {result.stderr.strip()}",
                file=sys.stderr,
            )
            return "UNKNOWN"
        ids = json.loads(result.stdout)
        # Response is [[id, ...], ...] — flatten one level.
        flat = [iid for group in ids for iid in group]
        if not flat:
            print(
                f"  warning: no InstanceId found for dns-name={dns_hostname}",
                file=sys.stderr,
            )
            return "UNKNOWN"
        return flat[0]
    except Exception as exc:  # noqa: BLE001 — any failure degrades to UNKNOWN, never fatal
        print(
            f"  warning: could not resolve InstanceId for {dns_hostname}: {exc}",
            file=sys.stderr,
        )
        return "UNKNOWN"
