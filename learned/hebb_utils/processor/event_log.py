"""Read the processor_event_log warehouse table (SQS processor op events).

Shared logic for the `trace-processor-op` and `query-processor-event-log` skills —
the reusable "read processor_event_log" building block, separated from any one task
so other use-cases can call it too. Key facts: SMID == ``processor_msg_id``; the
parent edge is ``processor_parent_msg_id``; a row's op is ``operation0`` (full list
in ``operations_list``). See learned/wiki/processor/processor-event-log and
learned/wiki/processor/tracing-processor-op-lineage.

This is **vscode-dependent**: it imports ``db`` and ``cloud_interfaces`` (www-rooted),
so a caller must run with ``PYTHONPATH=$CODE_BASE/www`` (see
learned/wiki/vscode-repo/python-import-root). It lives in ``hebb_utils`` (not
``utils``) so it can be imported in the same process as vscode's own top-level
``utils`` package without a name collision.

The table is modelled by ``ProcessorLogEvent`` with a *logical* db_type
(``REDSHIFT_LOG``) that ``get_db_type_override`` resolves to the region's physical
warehouse (e.g. StarRocks ``log.processor_event_log``) — the adapter-factory read
path via ``dwh.get_list``, not ``starrocks_utils``.
"""
import re

# A processor_msg_id (SMID) is a SQS/processor UUID; restrict to the UUID charset so
# the value is safe to interpolate into a SELECT (defense-in-depth — reads only).
_SMID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
# group_id / operation names: conservative identifier charset for safe interpolation.
_IDENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

DEFAULT_COLS = (
    "processor_msg_id, processor_parent_msg_id, operation0, operations_list, "
    "event_type, group_id, system_id, queue_name, status, request_trace_id, "
    "DATE_TRUNC('second', t_create) AS t_create"
)


class ProcessorEventLogError(Exception):
    """A read could not be performed; the message is user-facing.

    The message is everything after the conventional ``error: `` prefix, so a CLI
    caller can print ``f"error: {exc}"`` and reproduce the original wording.
    """


def is_valid_smid(smid):
    """True if ``smid`` looks like a processor_msg_id (UUID charset)."""
    return bool(smid and _SMID_RE.match(smid))


def _imports():
    """Lazily import the vscode packages; raise a PYTHONPATH-aware error if they fail."""
    try:
        from db.base_log_event import ProcessorLogEvent
        from db.db_type import DBType
        from cloud_interfaces import datawarehouse as dwh
    except ImportError as exc:
        raise ProcessorEventLogError(
            f"import failed — is PYTHONPATH set to $CODE_BASE/www?\n  {exc}") from exc
    return ProcessorLogEvent, DBType, dwh


def resolve_db_type_and_table():
    """Resolve the logical ``REDSHIFT_LOG`` db_type to the region's warehouse + table.

    Returns ``(db_type, full_table_name)`` — e.g. ``('starrocks', 'log.processor_event_log')``.
    Nothing is hardcoded per region; the model performs the routing.
    """
    ProcessorLogEvent, DBType, dwh = _imports()
    db_type = dwh.get_db_type_override(DBType.REDSHIFT_LOG.value)
    table = ProcessorLogEvent.get_full_table_name(db_type=db_type)
    return db_type, table


def run_select(query, db_type):
    """Execute a read-only SELECT via the adapter-factory path; returns ``list[dict]``."""
    _, _, dwh = _imports()
    return dwh.get_list(query, db_type=db_type) or []


def fetch_rows_by_msg_id(smid, db_type=None, table=None, cols=DEFAULT_COLS):
    """All rows for one ``processor_msg_id`` (SMID), ordered by event time.

    One message emits several rows (one per ``event_type``). Resolves the
    warehouse/table itself unless ``db_type``/``table`` are supplied (pass them when
    looping to avoid re-resolving each hop).
    """
    if not is_valid_smid(smid):
        raise ProcessorEventLogError(
            f"invalid processor_msg_id (SMID): {smid!r} (expected UUID charset)")
    if db_type is None or table is None:
        db_type, table = resolve_db_type_and_table()
    q = f"SELECT {cols} FROM {table} WHERE processor_msg_id = '{smid}' ORDER BY t_create"
    return run_select(q, db_type)


# Timestamp literal (YYYY-MM-DD, optionally with HH:MM[:SS]) — safe to interpolate as a
# t_create bound. And the columns allowed in a GROUP BY / count breakdown.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
_GROUP_COLS = ("operation0", "group_id", "queue_name", "event_type", "status", "system_id")


def _where_clauses(processor_msg_id=None, processor_parent_msg_id=None, group_id=None,
                   operation0=None, queue_name=None, event_type=None,
                   since=None, until=None, since_hours=None):
    """Build a validated, AND-combined list of WHERE clauses (read-only).

    ``queue_name`` is matched on the **trimmed** column (the stored value may carry a
    trailing space). ``since``/``until`` are absolute ``t_create`` bounds. Every
    interpolated value is charset/format-validated (defense-in-depth — reads only).
    """
    where = []
    for col, val in (("processor_msg_id", processor_msg_id),
                     ("processor_parent_msg_id", processor_parent_msg_id)):
        if val:
            if not is_valid_smid(val):
                raise ProcessorEventLogError(f"invalid {col}: {val!r} (expected UUID charset)")
            where.append(f"{col} = '{val}'")
    for col, val in (("group_id", group_id), ("operation0", operation0),
                     ("event_type", event_type)):
        if val:
            if not _IDENT_RE.match(val):
                raise ProcessorEventLogError(f"invalid {col}: {val!r}")
            where.append(f"{col} = '{val}'")
    if queue_name:
        if not _IDENT_RE.match(queue_name):
            raise ProcessorEventLogError(f"invalid queue_name: {queue_name!r}")
        where.append(f"TRIM(queue_name) = '{queue_name}'")
    for sql_op, val in ((">=", since), ("<=", until)):
        if val:
            if not _TS_RE.match(val):
                raise ProcessorEventLogError(
                    f"invalid timestamp {val!r} (expected 'YYYY-MM-DD[ HH:MM[:SS]]')")
            where.append(f"t_create {sql_op} '{val}'")
    if since_hours is not None:
        where.append(f"t_create >= DATE_SUB(NOW(), INTERVAL {int(since_hours)} HOUR)")
    return where


def fetch_rows(processor_msg_id=None, processor_parent_msg_id=None, group_id=None,
               operation0=None, queue_name=None, event_type=None,
               since=None, until=None, since_hours=None, limit=200, cols=DEFAULT_COLS):
    """Filtered read of processor_event_log. Filters are optional and AND-combined.

    Every interpolated value is charset-validated. At least one filter is required so
    the scan stays bounded. ``queue_name`` matches the trimmed column; ``since``/``until``
    are absolute ``t_create`` bounds ('YYYY-MM-DD[ HH:MM[:SS]]'). Returns
    ``{"db_type", "table", "rows": list[dict]}`` (rows newest-first, capped by ``limit``).
    """
    db_type, table = resolve_db_type_and_table()
    where = _where_clauses(processor_msg_id, processor_parent_msg_id, group_id,
                           operation0, queue_name, event_type, since, until, since_hours)
    if not where:
        raise ProcessorEventLogError(
            "at least one filter is required (processor_msg_id, processor_parent_msg_id, "
            "group_id, operation0, queue_name, event_type, since/until, or since_hours)")
    q = (f"SELECT {cols} FROM {table} WHERE {' AND '.join(where)} "
         f"ORDER BY t_create DESC LIMIT {int(limit)}")
    return {"db_type": db_type, "table": table, "rows": run_select(q, db_type)}


def count_events(group_by, processor_parent_msg_id=None, group_id=None, operation0=None,
                 queue_name=None, event_type=None, since=None, until=None,
                 since_hours=None, limit=200):
    """COUNT(*) breakdown of processor_event_log rows grouped by ``group_by`` columns.

    ``group_by`` is a list from a fixed allowlist (operation0, group_id, queue_name,
    event_type, status, system_id). Same validated filters as ``fetch_rows``; at least
    one filter is required. Returns ``{"db_type", "table", "group_by", "rows"}`` with
    rows = ``[{<group cols>, "cnt": N}, ...]`` ordered by count descending — the
    "what flooded a queue" breakdown (e.g. group_by=[operation0, group_id]).
    """
    if not group_by:
        raise ProcessorEventLogError("group_by must list at least one column")
    bad = [c for c in group_by if c not in _GROUP_COLS]
    if bad:
        raise ProcessorEventLogError(
            f"invalid group_by column(s): {bad!r} (allowed: {', '.join(_GROUP_COLS)})")
    db_type, table = resolve_db_type_and_table()
    where = _where_clauses(None, processor_parent_msg_id, group_id, operation0,
                           queue_name, event_type, since, until, since_hours)
    if not where:
        raise ProcessorEventLogError("at least one filter is required for an aggregate read")
    cols = ", ".join(group_by)
    q = (f"SELECT {cols}, COUNT(*) AS cnt FROM {table} WHERE {' AND '.join(where)} "
         f"GROUP BY {cols} ORDER BY cnt DESC LIMIT {int(limit)}")
    return {"db_type": db_type, "table": table, "group_by": list(group_by),
            "rows": run_select(q, db_type)}


# Dims allowed in a latency breakdown's GROUP BY (besides an optional time bucket).
_LAT_DIMS = ("operation0", "group_id")


def _require_window(**named):
    """Validate that each named value is a t_create literal (YYYY-MM-DD[ HH:MM[:SS]])."""
    for label, val in named.items():
        if not (val and _TS_RE.match(val)):
            raise ProcessorEventLogError(
                f"invalid/missing {label} (expected 'YYYY-MM-DD[ HH:MM[:SS]]')")


def _require_queue(queue_name):
    if not (queue_name and _IDENT_RE.match(queue_name)):
        raise ProcessorEventLogError(f"invalid queue_name: {queue_name!r}")


def throughput_timeseries(queue_name, since, until, bucket_minutes=15):
    """Per-bucket inflow (``message_dispatched``) vs drain (``message_processed``) for a queue.

    The **stock/flow** diagnostic for a backed-up queue: depth is the running sum of
    ``dispatched_in - processed_out``, so this overlay reconstructs the CloudWatch
    depth curve and tells you which side moved (inflow surge vs drain dip). Returns
    ``{"db_type","table","queue","bucket_minutes","rows":[{bucket,dispatched_in,
    processed_out,net_delta}]}`` ordered by bucket. ``queue_name`` matches the trimmed
    column (trailing space tolerated).
    """
    _require_queue(queue_name)
    _require_window(since=since, until=until)
    bm = int(bucket_minutes)
    if bm <= 0:
        raise ProcessorEventLogError("bucket_minutes must be a positive integer")
    db_type, table = resolve_db_type_and_table()
    q = (f"SELECT TIME_SLICE(t_create, INTERVAL {bm} MINUTE) AS bucket, "
         f"SUM(CASE WHEN event_type='message_dispatched' THEN 1 ELSE 0 END) AS dispatched_in, "
         f"SUM(CASE WHEN event_type='message_processed' THEN 1 ELSE 0 END) AS processed_out "
         f"FROM {table} WHERE TRIM(queue_name) = '{queue_name}' "
         f"AND t_create >= '{since}' AND t_create <= '{until}' "
         f"GROUP BY TIME_SLICE(t_create, INTERVAL {bm} MINUTE) ORDER BY 1")
    rows = run_select(q, db_type)
    for r in rows:
        try:
            r["net_delta"] = int(r.get("dispatched_in") or 0) - int(r.get("processed_out") or 0)
        except (TypeError, ValueError):
            r["net_delta"] = None
    return {"db_type": db_type, "table": table, "queue": queue_name,
            "bucket_minutes": bm, "rows": rows}


def latency_breakdown(queue_name, since, until, bucket_minutes=None, by=None,
                      operation0=None, group_id=None, limit=200):
    """Latency + worker-cost breakdown of ``message_processed`` rows for a queue.

    Groups by an optional time bucket (``bucket_minutes``) and/or the dims in ``by``
    (subset of ``operation0``, ``group_id``) and reports, per group: ``processed_out``
    (count), ``p50_ms`` / ``p90_ms`` (``percentile_approx`` — robust to the
    multi-million-ms ``MAX(latency_milliseconds)`` tail), and ``total_proc_sec`` =
    ``SUM(latency_milliseconds)/1000`` (volume x per-message latency = the worker
    capacity consumed; **worker-equivalents = total_proc_sec / window_seconds**).
    ``latency_milliseconds`` is op *processing* latency (dequeue->done), not queue
    wait — so it is a genuine drain-rate driver. Ordered by bucket when time-bucketed,
    else by ``total_proc_sec`` desc. At least one of ``bucket_minutes``/``by`` required.
    """
    _require_queue(queue_name)
    _require_window(since=since, until=until)
    by = list(by or [])
    bad = [c for c in by if c not in _LAT_DIMS]
    if bad:
        raise ProcessorEventLogError(
            f"invalid by dim(s): {bad!r} (allowed: {', '.join(_LAT_DIMS)})")
    where = ["event_type = 'message_processed'", f"TRIM(queue_name) = '{queue_name}'",
             f"t_create >= '{since}'", f"t_create <= '{until}'"]
    for col, val in (("operation0", operation0), ("group_id", group_id)):
        if val:
            if not _IDENT_RE.match(val):
                raise ProcessorEventLogError(f"invalid {col}: {val!r}")
            where.append(f"{col} = '{val}'")
    select_exprs, group_exprs = [], []
    if bucket_minutes is not None:
        bm = int(bucket_minutes)
        if bm <= 0:
            raise ProcessorEventLogError("bucket_minutes must be a positive integer")
        select_exprs.append(f"TIME_SLICE(t_create, INTERVAL {bm} MINUTE) AS bucket")
        group_exprs.append(f"TIME_SLICE(t_create, INTERVAL {bm} MINUTE)")
    select_exprs.extend(by)
    group_exprs.extend(by)
    if not group_exprs:
        raise ProcessorEventLogError(
            "latency_breakdown needs bucket_minutes and/or at least one by dim")
    order = "1" if bucket_minutes is not None else "total_proc_sec DESC"
    db_type, table = resolve_db_type_and_table()
    q = (f"SELECT {', '.join(select_exprs)}, COUNT(*) AS processed_out, "
         f"ROUND(percentile_approx(latency_milliseconds, 0.5)) AS p50_ms, "
         f"ROUND(percentile_approx(latency_milliseconds, 0.9)) AS p90_ms, "
         f"ROUND(SUM(latency_milliseconds)/1000.0) AS total_proc_sec "
         f"FROM {table} WHERE {' AND '.join(where)} "
         f"GROUP BY {', '.join(group_exprs)} ORDER BY {order} LIMIT {int(limit)}")
    return {"db_type": db_type, "table": table, "queue": queue_name, "by": by,
            "bucket_minutes": bucket_minutes, "rows": run_select(q, db_type)}


def parent_attribution(queue_name, since, until, parent_since=None, parent_until=None,
                       limit=50):
    """Rank the **parent ops** that produced a queue's messages (the driver breakdown).

    The CORRECT parent metric: ``COUNT(DISTINCT processor_msg_id)`` over the parent
    set, with **NO ``event_type`` filter on the outer query**. Filtering the outer on
    ``message_dispatched`` undercounts scheduled/retry parents — a retry/re-seed
    message is dispatched with a backoff delay, so its dispatch row lands outside the
    window even though its fan-out into the queue lands inside. The inner set is the
    distinct ``processor_parent_msg_id`` of the queue's ``message_dispatched`` rows in
    ``[since, until]``; the outer parent window defaults to the same window but should
    be **widened earlier** (``parent_since``) to catch delayed parents. Returns
    ``{... "rows":[{operation0, distinct_msgs}]}`` ordered desc.
    """
    _require_queue(queue_name)
    parent_since = parent_since or since
    parent_until = parent_until or until
    _require_window(since=since, until=until,
                    parent_since=parent_since, parent_until=parent_until)
    db_type, table = resolve_db_type_and_table()
    q = (f"SELECT operation0, COUNT(DISTINCT processor_msg_id) AS distinct_msgs "
         f"FROM {table} "
         f"WHERE t_create >= '{parent_since}' AND t_create <= '{parent_until}' "
         f"AND processor_msg_id IN ("
         f"SELECT DISTINCT processor_parent_msg_id FROM {table} "
         f"WHERE event_type = 'message_dispatched' AND TRIM(queue_name) = '{queue_name}' "
         f"AND t_create >= '{since}' AND t_create <= '{until}' "
         f"AND processor_parent_msg_id IS NOT NULL) "
         f"GROUP BY operation0 ORDER BY distinct_msgs DESC LIMIT {int(limit)}")
    return {"db_type": db_type, "table": table, "queue": queue_name,
            "child_window": [since, until], "parent_window": [parent_since, parent_until],
            "rows": run_select(q, db_type)}


def hop_from_rows(smid, depth, rows):
    """Collapse one message's rows (one per event_type) into a single hop summary."""
    events = sorted({r.get("event_type") for r in rows if r.get("event_type")})
    r0 = rows[0]
    parent = (r0.get("processor_parent_msg_id") or "").strip()
    return {
        "depth": depth,
        "processor_msg_id": smid,
        "operation0": r0.get("operation0"),
        "operations_list": r0.get("operations_list"),
        "events": events,
        "group_id": r0.get("group_id"),
        "queue_name": r0.get("queue_name"),
        # status is populated on the message_processed row (PASS/FAIL or a reroute marker).
        "status": [r.get("status") for r in rows if r.get("event_type") == "message_processed"],
        "t_create": str(r0.get("t_create")),
        "parent": parent or None,
    }


def walk_parent_chain(target, max_depth=50):
    """Walk ``processor_parent_msg_id`` from ``target`` up to the parentless root.

    Returns ``{"db_type", "table", "chain": [hop, ...]}`` with ``chain[0]`` = target
    and ``chain[-1]`` = root (or a ``{"_note": "NO ROW FOUND"}`` terminal). Guards
    against cycles (visited set) and runaway depth (``max_depth``).
    """
    if not is_valid_smid(target):
        raise ProcessorEventLogError(
            f"invalid processor_msg_id (SMID): {target!r} (expected UUID charset)")
    db_type, table = resolve_db_type_and_table()
    chain, visited, smid, depth = [], set(), target, 0
    while smid and smid not in visited and depth < max_depth:
        visited.add(smid)
        rows = fetch_rows_by_msg_id(smid, db_type=db_type, table=table)
        if not rows:
            chain.append({"depth": depth, "processor_msg_id": smid, "_note": "NO ROW FOUND"})
            break
        hop = hop_from_rows(smid, depth, rows)
        chain.append(hop)
        smid = hop["parent"]
        depth += 1
    return {"db_type": db_type, "table": table, "chain": chain}
