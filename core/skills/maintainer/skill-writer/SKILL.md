---
name: skill-writer
description: Turn a skill requirement (from task-analyser) into the right change to the learned skills — first check whether an existing skill can be extended or modified without breaking it, or composed with another, and only create a new skill when nothing fits. Reuses existing capabilities (e.g. a query-runner) instead of duplicating them.
---

# Write the skills

You are the injector's **capability stage**. Take the skill requirements + script details from `task-analyser` and make the **smallest correct change** to the learned skills. The default is *not* "write a new skill" — it's "find what already covers this and reuse it."

**Compose with `skill-creator`, don't reinvent authoring.** For the mechanical work — scaffolding a `SKILL.md`, structuring its steps, and tuning its triggering `description` (and, for a substantial new skill, running its eval loop) — use the generic **`skill-creator`** skill (named here so the model loads it). `skill-writer` supplies the judgment `skill-creator` can't: *which* change to make per Rule A4, *which* existing skills to reuse, and the hebb conventions below (top-level `skills/` placement, bundled python that passes the gate, the `$CODE_BASE` env contract). You extend `skill-creator` with hebb's discipline; it does the boilerplate.

## Steps
1. **Survey what exists.** You already see every skill's `name` + `description` (Claude Code loads them). For each requirement, also read the candidates in full: learned skills under `skills/` and core skills under `core/skills/` (for reuse/composition targets — you may *call* a core skill from a learned one, but never *edit* `core/`).
2. **Pick the A4 branch** per requirement:
   - **No coverage** → create a new skill.
   - **Exists but was never picked up** → *discovery* problem: fix the existing skill's **description** (and/or `paths`) so it surfaces — **not** its script.
   - **Picked up but fell short** → *capability* problem: fix its **script or steps**.
   - **Covered by composition** (A + B together, glue missing) → write a **thin** skill that **names** A and B in its body so the model loads them; promote to an agent only if Rule A1 holds and the role recurs (rare).
3. **Reuse before duplicating.** Decompose each requirement into the capabilities it needs and map each to an existing skill. *Example:* a "specific-table-search" need = **table-schema knowledge** + **running a query**. If a query-running skill already exists, **reuse it** (name it in the new skill's body) and add only the schema-specific part — do not re-implement query execution. Bundle a duplicated script only when sharing by reference would couple two skills that should stay independent.
4. **Extend without breaking.** When you modify an existing skill, changes are **additive** — preserve its current steps, scripts, and contract so existing callers keep working. If a change would alter existing behavior, prefer a new skill over a breaking edit.
5. **Write it — author with `skill-creator`, bundle every python script as its own file.** Use `skill-creator` to scaffold and word the `SKILL.md` (structure, steps, a `description` that says *when* to use it so it triggers). Place the result under top-level `skills/<domain>/<name>/`: the `SKILL.md` and, for anything that runs python, a **`scripts/` directory holding the actual `.py` file(s)** — nest them in subfolders however you like. Always add the python **separately** as a committed, bundled artifact — never inline python as a code block in `SKILL.md`, and never leave it as a scratch script.
6. **Put only the run command in `SKILL.md`, in the gate-passing shape.** The body invokes the bundled script as a **single, clean** command that points at the skill's own directory (never hardcode the interpreter):
   ```bash
   PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
   ```
   This exact shape is what the python-execution gate (`core/tools/gate_python_exec.py`) recognizes as a **skill-bundled** script and lets run **without a prompt** — so the skill passes the gate every time it is used. Keep it to one command: a chained/compound invocation (`&&`, `;`, `|`, redirects, `$(...)`) is downgraded to a prompt, and any script reached by a non-skill path (a scratch/`/tmp` path, `python -c`) stays gated. Bundling the `.py` under `scripts/` and referencing it via `${CLAUDE_SKILL_DIR}` is exactly what turns a one-time, approval-gated scratch run into a capability that runs unattended next time. `${CLAUDE_SKILL_DIR}` is a **static anchor** to the running skill's own directory, so a reference beneath it passes the gate at **any nesting depth** — the gate keys on the script being anchored under the skill (`${CLAUDE_SKILL_DIR}` or a resolved `skills/` path), not on a flat layout. (It anchors the script's *location*; the run still executes with the shell's working directory, so have scripts resolve their own bundled files relative to `${CLAUDE_SKILL_DIR}` / `__file__`, not the cwd.)
7. **Promote a scratch script** from the log's evidence into such a bundled script only when its capability is repeatable; keep it deterministic (a script is a pure transform — the skill carries the runtime judgment; Rule A2).

## Boundaries
- Write **only** under top-level `skills/` (and `agents/` if A1 forces an agent). Never edit `core/` or `inputs/`; you may *reference* a core skill but not change it.
- One change per requirement, each traceable to the session-doc. Knowledge belongs to `wiki-writer`, not here.
