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
