---
name: plot-result-set
model: sonnet
description: Plot a result set — a JSON array of row objects — to a PNG bar or line chart, saved headlessly. Use when a task needs to chart, graph, or visualize query results (e.g. counts in time buckets) and save an image. Pairs with query-starrocks, whose `--json-out PATH` writes exactly the rows this skill consumes.
---

# Plot a result set to a PNG

Turn a set of rows into a chart image. This is a **generic** capability — plain
matplotlib with no `$CODE_BASE`/`www` coupling — so it is its own skill, not a
feature folded into whatever produced the data. Execution is a **bundled,
gate-passing** script — `scripts/plot_result_set.py` — that reads a JSON array
of row dicts and writes a PNG (matplotlib **Agg** backend, headless).

It is the natural downstream of any step that emits rows as JSON. In particular,
the **`query-starrocks`** skill's runner writes rows with `--json-out PATH` in
exactly the shape this skill reads — query in one skill, draw in this one.

## Steps

1. **Get the rows as a JSON array.** A file containing a JSON list of row objects,
   e.g. `[{"bucket_start": "2026-06-24 13:05:00", "n": 81234}, ...]`. If the data
   comes from StarRocks, run the **`query-starrocks`** skill with
   `--json-out /path/to/rows.json` to produce it.

2. **Decide x, y, and chart kind** (the runtime judgment): which column is the
   x-axis, which is the (numeric) y-axis, and `bar` vs. `line`. For time buckets,
   pass `--x-datetime` so the x values parse as datetimes and the time axis is
   formatted (and bars are sized to the bucket spacing).

3. **Run the bundled plotter.** No `PYTHONPATH` is needed — matplotlib has no
   `www` import dependency; just the venv interpreter (`matplotlib` 3.10.0 ships
   in `$VSCODE_PYTHON` — see [[../../../wiki/vscode-repo/python-import-root|Python import root]]):
   ```bash
   "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/plot_result_set.py" /path/to/rows.json --x bucket_start --y n --kind bar --x-datetime --out /path/to/chart.png
   ```
   - **Data by file path** keeps the command operator-free so the bash gate
     (`core/tools/bash_exec_policy.py`) auto-allows the run with no prompt.
   - Flags: `--x COL` / `--y COL` (required), `--out PATH` (required),
     `--kind {bar,line}` (default `bar`), `--x-datetime`, and optional
     `--title` / `--xlabel` / `--ylabel`.
   - **Keep `--title`/labels metachar-free** if you pass them inline — a `>`, `<`,
     `;`, `|`, `` ` `` or `$(` in any argument trips the gate and forces a prompt
     (same caveat as query-starrocks's `--sql`). They default to the column names,
     so you can usually omit them.

4. **Confirm the output.** The script prints the PNG path and point count to
   stderr and exits `0`; it exits `2` (with a clear message) if the file is
   missing/empty, the x/y column is absent, or a y value isn't numeric.

## Notes

- **Pure transform, no external system.** It reads only the JSON file you name
  and writes only the `--out` PNG, so — unlike a query runner — it carries no
  read-only/write allowlist; there is nothing external to guard (Rule A2/A8).
  Its safety is input validation: it fails fast rather than drawing garbage.
- **Keep it separate from the data source.** Charting rows would serve any
  domain unchanged, so it stays generic here rather than being taught to any one
  query/fetch skill. Compose the two by naming both in a task that needs them.
