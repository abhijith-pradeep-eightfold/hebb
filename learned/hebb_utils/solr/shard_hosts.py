"""Resolve a Solr shard's replica DNS hostnames from the live `$CODE_BASE` search_config.

Shared logic for the `solr-shard-dns-lookup` and `solr-shard-cpu` skills. This is
**vscode-dependent**: it imports `www` packages (config / search), so a caller must
run with `PYTHONPATH=$CODE_BASE/www` (see
learned/wiki/vscode-repo/python-import-root).

This lives in `hebb_utils` (not `utils`) precisely so it can be imported in the
same process as vscode code: vscode ships its own top-level `utils` package
(`www/utils`), and two different top-level `utils` packages cannot coexist on one
`sys.path`. The `hebb_` prefix keeps the shared library collision-free, which
matters because most shared logic here will be vscode-dependent.

The collection's `hosts_key` is derived from `SEARCH_INDEX_SETTINGS_REGISTRY`
(default pattern `{tablename}_shard_hosts`; `profiles` and `positions` are
overridden to `shard_hosts` and `position_shard_hosts`). Shard IDs are not
contiguous — `available_shards` enumerates the ones that actually exist.
"""
import os


class ShardLookupError(Exception):
    """A collection/shard could not be resolved; the message is user-facing.

    The message is everything after the conventional ``error: `` prefix, so a CLI
    caller can print ``f"error: {exc}"`` and reproduce the original wording.
    """


def resolve_shard_hosts(collection, shard_id, region=None):
    """Resolve the replica DNS hostnames for a collection + shard in a region.

    Returns ``(region, available_shards, replica_dns_list)``:
      - ``region``: the region actually used (the arg, or ``EF_DEFAULT_REGION``);
      - ``available_shards``: sorted ``list[str]`` of shard IDs that exist;
      - ``replica_dns_list``: ``list[str]`` of replica DNS hostnames for the shard.

    Raises ``ShardLookupError`` (user-facing message) when the region is unset, an
    import fails (wrong PYTHONPATH), the collection is not in the registry, the
    config key is missing, or the shard does not exist (message lists the
    available shard IDs).
    """
    region = region or os.getenv("EF_DEFAULT_REGION")
    if not region:
        raise ShardLookupError("--region not specified and EF_DEFAULT_REGION is not set")

    # vscode imports — require PYTHONPATH=$CODE_BASE/www.
    try:
        from config import config as cfg_module
        from search import search_constants
        from search.search_index_settings import SEARCH_INDEX_SETTINGS_REGISTRY
        from utils.os_constants import EF_DEFAULT_REGION  # noqa: F401 (confirms path resolves)
    except ImportError as exc:
        raise ShardLookupError(
            f"import failed — is PYTHONPATH set to $CODE_BASE/www?\n  {exc}") from exc

    if collection not in SEARCH_INDEX_SETTINGS_REGISTRY:
        valid = sorted(SEARCH_INDEX_SETTINGS_REGISTRY.keys())
        raise ShardLookupError(
            f"'{collection}' is not in SEARCH_INDEX_SETTINGS_REGISTRY.\n"
            f"  Valid collection names: {', '.join(valid)}")

    hosts_key = SEARCH_INDEX_SETTINGS_REGISTRY[collection].hosts_key

    try:
        search_cfg = cfg_module.get(search_constants.SEARCH_CONFIG, region=region)
    except Exception as exc:  # noqa: BLE001 — surface any config-read failure uniformly
        raise ShardLookupError(
            f"config.get('{search_constants.SEARCH_CONFIG}', region='{region}') failed:\n  {exc}"
        ) from exc

    if hosts_key not in search_cfg:
        raise ShardLookupError(
            f"key '{hosts_key}' not found in search_config for region '{region}'.\n"
            f"  Available top-level keys: {sorted(search_cfg.keys())}")

    shard_hosts = search_cfg[hosts_key]
    available_shards = sorted(shard_hosts.keys(), key=lambda x: int(x))
    shard_key = str(shard_id)
    if shard_key not in shard_hosts:
        raise ShardLookupError(
            f"shard {shard_id} does not exist for collection '{collection}' in region '{region}'.\n"
            f"  Available shard IDs: {', '.join(available_shards)}")

    replicas = shard_hosts[shard_key]
    # replicas may be a list of DNS strings, a dict keyed by replica index, or a scalar.
    if isinstance(replicas, dict):
        replica_list = [replicas[str(i)] for i in sorted(int(k) for k in replicas.keys())]
    elif isinstance(replicas, list):
        replica_list = replicas
    else:
        replica_list = [str(replicas)]

    return region, available_shards, replica_list
