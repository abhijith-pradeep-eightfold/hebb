"""Read the log.search_query_log warehouse table (the Solr per-query fact table).

Shared logic for the `query-solr-load` skill — the reusable "read search_query_log"
building block, separated from any one task so other use-cases can call it too. It
backs the two analytical reads a Solr-CPU investigation needs (see
learned/wiki/oncall/solr-cpu-high and learned/wiki/data-warehouse/search-query-log):

  split_timeseries  per-bucket indexing (callerid='index') vs query (all other
                    callerids) counts for a core+shard -> which work stream rose.
  driver_breakdown  callerid x group_id x env over a spike window vs a baseline
                    window, normalized per-minute (+ spike/baseline ratio, NEW flag)
                    -> which source drove the stream that rose.

Key column facts (see the wiki page): `t_create` is the per-query event time, stored
**UTC** (same clock as a CloudWatch CPU curve); `core` is the Solr core (= collection);
`shard_id` is an int; `callerid` is the calling feature/code path and `callerid='index'`
marks the indexing (write) stream; `group_id` is the tenant; `env` is the originating
service (e.g. github-ci, processor).

Reads go through `hebb_utils.starrocks.direct_query` (AWS CLI credentials + pymysql),
which works for all four AWS StarRocks regions without a vscode import or STS dependency.
Region defaults to ``EF_DEFAULT_REGION`` env var (fallback ``us-west-2``) when not passed.
"""
import datetime
import re

# The StarRocks physical table (the `log` schema OLAP table — see the wiki page).
TABLE = "log.search_query_log"

# Conservative identifier charset for safe interpolation (defense-in-depth — reads only).
# core / callerid / group_id / env values are matched against this before interpolation.
_IDENT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Timestamp literal (YYYY-MM-DD, optionally with HH:MM[:SS]) — safe as a t_create bound.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
# Columns allowed as driver-breakdown dimensions.
_DRIVER_DIMS = ("callerid", "group_id", "env")
# The reserved callerid value that marks the indexing (write) stream.
INDEX_CALLERID = "index"


class SearchQueryLogError(Exception):
    """A read could not be performed; the message is user-facing.

    The message is everything after the conventional ``error: `` prefix, so a CLI
    caller can print ``f"error: {exc}"`` and reproduce the original wording.
    """


def run_select(query, cache_ttl_secs=None, region=None):
    """Execute a read-only SELECT against StarRocks; returns ``list[dict]``.

    Routes through ``hebb_utils.starrocks.direct_query`` (AWS CLI + pymysql).
    ``region`` defaults to ``EF_DEFAULT_REGION`` env var (fallback ``us-west-2``).
    """
    try:
        from hebb_utils.starrocks.direct_query import run_select as _direct, DirectQueryError
    except ImportError as exc:
        raise SearchQueryLogError(
            f"could not import direct_query — is hebb_utils on sys.path?\n  {exc}"
        ) from exc
    try:
        return _direct(query, region=region, cache_ttl_secs=cache_ttl_secs)
    except DirectQueryError as exc:
        raise SearchQueryLogError(str(exc)) from exc


def _validate_ident(label, val):
    if not (val and _IDENT_RE.match(val)):
        raise SearchQueryLogError(f"invalid {label}: {val!r} (expected identifier charset)")
    return val


def _validate_ts(label, val):
    if not (val and _TS_RE.match(val)):
        raise SearchQueryLogError(
            f"invalid/missing {label}: {val!r} (expected 'YYYY-MM-DD[ HH:MM[:SS]]')")
    return val


def _validate_shard(shard_id):
    try:
        return int(shard_id)
    except (TypeError, ValueError):
        raise SearchQueryLogError(f"invalid shard_id: {shard_id!r} (expected an integer)")


def _scope_clause(core, shard_id):
    """The core+shard predicate shared by both reads (validated)."""
    return f"core = '{_validate_ident('core', core)}' AND shard_id = {_validate_shard(shard_id)}"


def _window_minutes(since, until):
    """Inclusive window length in minutes between two t_create literals (>= 1)."""
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M", "%Y-%m-%d")
    def _parse(v):
        for f in fmts:
            try:
                return datetime.datetime.strptime(v, f)
            except ValueError:
                continue
        raise SearchQueryLogError(f"could not parse timestamp {v!r}")
    delta = (_parse(until) - _parse(since)).total_seconds() / 60.0
    return delta if delta > 0 else 1.0


def split_timeseries(core, shard_id, since, until, bucket_minutes=15, cache_ttl_secs=None, region=None):
    """Per-bucket indexing-vs-query counts for one Solr core+shard over a window.

    Indexing = rows with ``callerid='index'`` (the write stream); query = every other
    callerid (the read stream). This is the **first** Solr-CPU diagnostic: which work
    stream rose with the CPU curve (a flow metric = indexing + query work). Returns
    ``{"table","core","shard_id","bucket_minutes","rows":[{bucket,indexing,query}]}``
    ordered by bucket (``t_create`` is UTC — same clock as the CloudWatch CPU curve).
    """
    scope = _scope_clause(core, shard_id)
    _validate_ts("since", since)
    _validate_ts("until", until)
    bm = int(bucket_minutes)
    if bm <= 0:
        raise SearchQueryLogError("bucket_minutes must be a positive integer")
    q = (f"SELECT TIME_SLICE(t_create, INTERVAL {bm} MINUTE) AS bucket, "
         f"SUM(CASE WHEN callerid = '{INDEX_CALLERID}' THEN 1 ELSE 0 END) AS indexing, "
         f"SUM(CASE WHEN callerid <> '{INDEX_CALLERID}' THEN 1 ELSE 0 END) AS query "
         f"FROM {TABLE} WHERE {scope} "
         f"AND t_create >= '{since}' AND t_create <= '{until}' "
         f"GROUP BY TIME_SLICE(t_create, INTERVAL {bm} MINUTE) ORDER BY 1")
    return {"table": TABLE, "core": core, "shard_id": int(shard_id),
            "bucket_minutes": bm, "rows": run_select(q, cache_ttl_secs, region=region)}


def driver_breakdown(core, shard_id, since, until, baseline_since, baseline_until,
                     dims=_DRIVER_DIMS, stream="query", limit=50, cache_ttl_secs=None,
                     region=None):
    """Per-source breakdown of a Solr core+shard's load: spike window vs baseline.

    Groups by ``dims`` (a subset of callerid, group_id, env) and counts rows in the
    spike window ``[since, until]`` and the baseline window
    ``[baseline_since, baseline_until]`` in one scan, then normalizes each to a
    **per-minute rate** (windows are usually unequal length) and computes the
    spike/baseline ratio (``None`` => a NEW source, zero in baseline). ``stream``
    selects which work stream to break down: ``query`` (callerid<>'index', the default —
    the read stream a CPU query-surge lives in), ``index`` (callerid='index'), or
    ``all``. Rows with zero spike count are dropped; ordered by spike count desc.

    Returns ``{"table","core","shard_id","dims","stream","spike_window","baseline_window",
    "rows":[{<dims>, spike_cnt, base_cnt, spike_per_min, base_per_min, ratio}]}``.
    """
    scope = _scope_clause(core, shard_id)
    _validate_ts("since", since)
    _validate_ts("until", until)
    _validate_ts("baseline_since", baseline_since)
    _validate_ts("baseline_until", baseline_until)
    dims = list(dims or [])
    bad = [d for d in dims if d not in _DRIVER_DIMS]
    if bad:
        raise SearchQueryLogError(
            f"invalid dim(s): {bad!r} (allowed: {', '.join(_DRIVER_DIMS)})")
    if not dims:
        raise SearchQueryLogError(f"need at least one dim ({', '.join(_DRIVER_DIMS)})")
    if stream == "query":
        stream_clause = f" AND callerid <> '{INDEX_CALLERID}'"
    elif stream == "index":
        stream_clause = f" AND callerid = '{INDEX_CALLERID}'"
    elif stream == "all":
        stream_clause = ""
    else:
        raise SearchQueryLogError(f"invalid stream: {stream!r} (query|index|all)")
    # Scan the full span covering both windows; the CASE sums bucket each row into its
    # own window (rows in any gap between the windows are counted in neither).
    overall_lo = min(since, baseline_since)
    overall_hi = max(until, baseline_until)
    cols = ", ".join(dims)
    q = (f"SELECT {cols}, "
         f"SUM(CASE WHEN t_create >= '{since}' AND t_create <= '{until}' "
         f"THEN 1 ELSE 0 END) AS spike_cnt, "
         f"SUM(CASE WHEN t_create >= '{baseline_since}' AND t_create <= '{baseline_until}' "
         f"THEN 1 ELSE 0 END) AS base_cnt "
         f"FROM {TABLE} WHERE {scope}{stream_clause} "
         f"AND t_create >= '{overall_lo}' AND t_create <= '{overall_hi}' "
         f"GROUP BY {cols} HAVING spike_cnt > 0 ORDER BY spike_cnt DESC LIMIT {int(limit)}")
    rows = run_select(q, cache_ttl_secs, region=region)
    spike_min = _window_minutes(since, until)
    base_min = _window_minutes(baseline_since, baseline_until)
    for r in rows:
        sc = float(r.get("spike_cnt") or 0)
        bc = float(r.get("base_cnt") or 0)
        # Divide the UNROUNDED rates and guard on the actual denominator. base_per_min is
        # round(bc / base_min, 2), which collapses a small-but-nonzero baseline count to
        # 0.0 over a long baseline window — so guarding on `bc > 0` (the raw count) and
        # then dividing by base_per_min raised ZeroDivisionError. Here ratio=None still
        # flags a NEW source (zero baseline); a tiny baseline yields a large finite ratio.
        spm = sc / spike_min
        bpm = bc / base_min
        r["spike_per_min"] = round(spm, 2)
        r["base_per_min"] = round(bpm, 2)
        r["ratio"] = round(spm / bpm, 2) if bpm > 0 else None
    return {"table": TABLE, "core": core, "shard_id": int(shard_id), "dims": dims,
            "stream": stream, "spike_window": [since, until],
            "baseline_window": [baseline_since, baseline_until], "rows": rows}


def processor_query_smids(core, shard_id, since, until, limit=500,
                          cache_ttl_secs=None, region=None):
    """Distinct processor SMIDs behind a Solr core+shard's *query* traffic.

    For one ``core`` + ``shard_id`` over ``[since, until]``, return the
    ``env='processor'`` query rows (``callerid <> 'index'`` — read traffic, not
    indexing) grouped by ``sequence_message_id`` (the processor SMID that issued each
    query — the join key to processor_event_log). Each row carries the query count that
    SMID produced plus its ``group_id`` / ``callerid``, highest-volume SMIDs first.
    Feed each ``sequence_message_id`` to
    ``hebb_utils.processor.event_log.walk_parent_chain`` to reach its root op — this is
    the env=processor -> sequence_message_id -> root-op bridge (see the wiki page
    learned/wiki/data-warehouse/search-query-log, "sequence_message_id").

    Returns ``{"table","core","shard_id","window":[since,until],
    "rows":[{sequence_message_id, group_id, callerid, query_cnt}]}`` (read-only;
    every interpolated value is charset/format-validated).
    """
    scope = _scope_clause(core, shard_id)
    _validate_ts("since", since)
    _validate_ts("until", until)
    q = (f"SELECT sequence_message_id, group_id, callerid, COUNT(*) AS query_cnt "
         f"FROM {TABLE} WHERE {scope} "
         f"AND t_create >= '{since}' AND t_create <= '{until}' "
         f"AND env = 'processor' AND callerid <> '{INDEX_CALLERID}' "
         f"AND sequence_message_id IS NOT NULL AND sequence_message_id <> '' "
         f"GROUP BY sequence_message_id, group_id, callerid "
         f"ORDER BY query_cnt DESC LIMIT {int(limit)}")
    return {"table": TABLE, "core": core, "shard_id": int(shard_id),
            "window": [since, until], "rows": run_select(q, cache_ttl_secs, region=region)}
