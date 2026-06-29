"""Comparative per-window 'lift' over keyed count breakdowns — a generic transform.

Given the *same* grouped-count breakdown (e.g. `operation0 x group_id` dispatch counts)
fetched over several time windows of possibly *different* lengths, normalise each window
to a per-hour rate (so unequal windows are comparable) and rank every key by

    lift = spike_rate / baseline_rate

to separate a **spike-specific** driver (high lift; often zero outside the spike) from a
**high-baseline** driver (flat lift ~1; heavy but steady, not the cause). This is the
"absolute count != spike driver" discipline from the inflow branch of
learned/wiki/oncall/queue-backed-up (the comparative-window lift) — the same shape
query-solr-load uses for Solr load (spike vs baseline, normalised per-minute).

Pure transform: no I/O, no vscode/www dependency. The caller fetches each window's
breakdown (e.g. via hebb_utils.processor.event_log.count_events) and passes the rows in,
so this module is reusable across domains (processor queues, Solr load, anything keyed).
"""
from __future__ import absolute_import

# Sentinel lift for a key absent from the baseline window but present in the spike
# (division by zero). Large finite value so it sorts above any real ratio yet stays
# JSON-safe (unlike float('inf')).
ZERO_BASELINE_LIFT = 999.0


def _rates_per_hour(rows, minutes, key_cols, count_col):
    """{key_tuple: count/minutes*60} for one window's grouped rows."""
    m = float(minutes) if minutes and float(minutes) > 0 else 1.0
    out = {}
    for r in rows:
        key = tuple(r.get(c) for c in key_cols)
        out[key] = out.get(key, 0.0) + float(r.get(count_col) or 0) / m * 60.0
    return out


def compute_lift(windows, baseline, spike, key_cols, count_col="cnt",
                 zero_baseline_lift=ZERO_BASELINE_LIFT):
    """Rank keys by spike-vs-baseline lift across normalised per-window rates.

    ``windows``: ordered list of ``(name, minutes, rows)`` — ``name`` a label,
    ``minutes`` the window length (for normalisation), ``rows`` a list of dicts each
    carrying every column in ``key_cols`` plus ``count_col``. ``baseline`` / ``spike``
    each name one of the windows (the lift denominator / numerator-and-sort-key).

    Returns a list of dicts, one per distinct key, each holding the ``key_cols`` values,
    a ``<name>_per_hr`` rate for *every* window, and ``lift`` (= spike_per_hr /
    baseline_per_hr; ``zero_baseline_lift`` when the baseline rate is 0 but the spike
    rate is positive; 0.0 when both are 0), sorted by the spike rate descending.
    """
    names = [w[0] for w in windows]
    if baseline not in names or spike not in names:
        raise ValueError(f"baseline {baseline!r} / spike {spike!r} must each name a "
                         f"window (have {names})")
    rates = {name: _rates_per_hour(rows, minutes, key_cols, count_col)
             for name, minutes, rows in windows}
    all_keys = set()
    for d in rates.values():
        all_keys.update(d.keys())
    spike_col = f"{spike}_per_hr"
    out = []
    for key in all_keys:
        row = {c: key[i] for i, c in enumerate(key_cols)}
        for name in names:
            row[f"{name}_per_hr"] = round(rates[name].get(key, 0.0), 1)
        b = rates[baseline].get(key, 0.0)
        s = rates[spike].get(key, 0.0)
        row["lift"] = round(s / b, 2) if b > 0 else (zero_baseline_lift if s > 0 else 0.0)
        out.append(row)
    out.sort(key=lambda r: r.get(spike_col, 0.0), reverse=True)
    return out
