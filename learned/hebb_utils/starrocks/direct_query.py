"""Direct cross-region StarRocks read-only access via AWS CLI + pymysql.

Bypasses the vscode Python SDK stack, which calls STS GetCallerIdentity at
module import time and fails for non-us-west-2 regions (SignatureDoesNotMatch).
Uses only:
  - AWS CLI subprocess for credential fetching (no boto STS restriction)
  - Hardcoded public NLB endpoints discovered per region via elbv2
  - pymysql 1.4.6 MySQL wire-protocol connection to StarRocks on port 9030

Public API:
  SUPPORTED_REGIONS  -- tuple of region strings where StarRocks is deployed
  run_select(query, region, cache_ttl_secs=None) -> list[dict]
  get_credentials(region) -> (username, password)

No vscode imports — zero cross-region STS dependency.
Credentials are cached in memory per region (15-minute TTL).
"""
import json
import subprocess
import time

try:
    import pymysql
    import pymysql.cursors
    _PYMYSQL_AVAILABLE = True
except ImportError:
    _PYMYSQL_AVAILABLE = False

# AWS regions where StarRocks is deployed (westus2 is Azure/Databricks — not StarRocks).
SUPPORTED_REGIONS = ("us-west-2", "eu-central-1", "ca-central-1", "ap-southeast-2")

# Internet-facing NLB endpoints (port 9030) per region.
# Discovered via: aws elbv2 describe-load-balancers --region <r>
#   --query 'LoadBalancers[?contains(LoadBalancerName,`celerdata`)&&Scheme==`internet-facing`].DNSName'
_PUBLIC_NLB = {
    "us-west-2":      "celerdata-public-nlb-vE7BpCHk-c78b4bf6078c45fc.elb.us-west-2.amazonaws.com",
    "eu-central-1":   "celerdata-public-nlb-eUqYqKhc-88aae864aab9a170.elb.eu-central-1.amazonaws.com",
    "ca-central-1":   "celerdata-public-nlb-N21Ht9MG-9cba2550b3b5159b.elb.ca-central-1.amazonaws.com",
    "ap-southeast-2": "celerdata-public-nlb-Zju7g62P-6c92f69c749eb061.elb.ap-southeast-2.amazonaws.com",
}

_SECRET_ID = "STARROCKS-CLUSTER-RO"
_PORT = 9030
_DATABASE = "log"
_CRED_TTL_SECS = 900  # 15 minutes

# region -> (username, password, expires_at)
_cred_cache: dict = {}
# (query_hash, region) -> (rows, expires_at)
_result_cache: dict = {}


class DirectQueryError(Exception):
    """A direct StarRocks query could not be performed; message is user-facing."""


def get_credentials(region: str):
    """Return (username, password) for the StarRocks RO cluster in region.

    Fetches from Secrets Manager via AWS CLI subprocess; cached for 15 minutes.
    Raises DirectQueryError on failure.
    """
    if region not in SUPPORTED_REGIONS:
        raise DirectQueryError(
            f"region {region!r} is not a StarRocks region "
            f"(supported: {', '.join(SUPPORTED_REGIONS)})")

    now = time.time()
    entry = _cred_cache.get(region)
    if entry and now < entry[2]:
        return entry[0], entry[1]

    try:
        proc = subprocess.run(
            ["aws", "secretsmanager", "get-secret-value",
             "--secret-id", _SECRET_ID, "--region", region,
             "--query", "SecretString", "--output", "text"],
            capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as exc:
        raise DirectQueryError(
            f"aws secretsmanager get-secret-value failed for {region!r}: "
            f"{exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired:
        raise DirectQueryError(
            f"aws secretsmanager timed out for region {region!r}")

    try:
        secret = json.loads(proc.stdout.strip())
        username = secret["username"]
        password = secret["password"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise DirectQueryError(
            f"unexpected secret format from {_SECRET_ID!r} in {region!r}: {exc}") from exc

    _cred_cache[region] = (username, password, now + _CRED_TTL_SECS)
    return username, password


def run_select(query: str, region: str = None, cache_ttl_secs=None) -> list:
    """Execute a read-only SELECT against StarRocks in region; returns list[dict].

    Connects to the region's internet-facing NLB via pymysql (port 9030).
    Credentials are fetched from Secrets Manager via AWS CLI.
    If cache_ttl_secs is set, results are cached in memory.
    """
    if region is None:
        import os
        region = os.environ.get("EF_DEFAULT_REGION", "us-west-2")
    if not _PYMYSQL_AVAILABLE:
        raise DirectQueryError(
            "pymysql is not installed; run: pip install pymysql")
    if region not in SUPPORTED_REGIONS:
        raise DirectQueryError(
            f"region {region!r} is not a StarRocks region "
            f"(supported: {', '.join(SUPPORTED_REGIONS)})")

    now = time.time()
    if cache_ttl_secs is not None:
        cache_key = (hash(query), region)
        entry = _result_cache.get(cache_key)
        if entry and now < entry[1]:
            return entry[0]

    host = _PUBLIC_NLB[region]
    username, password = get_credentials(region)

    try:
        conn = pymysql.connect(
            host=host, port=_PORT,
            user=username, password=password,
            database=_DATABASE,
            connect_timeout=15,
            cursorclass=pymysql.cursors.DictCursor)
    except pymysql.Error as exc:
        raise DirectQueryError(
            f"could not connect to StarRocks in {region!r} "
            f"(host={host}, port={_PORT}): {exc}") from exc

    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = list(cursor.fetchall() or [])
    except pymysql.Error as exc:
        raise DirectQueryError(
            f"query failed in StarRocks {region!r}: {exc}") from exc
    finally:
        conn.close()

    if cache_ttl_secs is not None:
        _result_cache[cache_key] = (rows, now + cache_ttl_secs)
    return rows
