---
name: skill-writer
description: Turn a skill requirement (from task-analyser) into the right change to the learned skills — first check whether an existing skill can be extended or modified without breaking it, or composed with another, and only create a new skill when nothing fits. Reuses existing capabilities (e.g. a query-runner) instead of duplicating them.
---

# Write the skills

You are the injector's **capability stage**. Take the skill requirements + script details from `task-analyser` and make the **smallest correct change** to the learned skills. The default is *not* "write a new skill" — it's "find what already covers this and reuse it."

## Steps
1. **Survey what exists.** You already see every skill's `name` + `description` (Claude Code loads them). For each requirement, also read the candidates in full: learned skills under `skills/` and core skills under `core/skills/` (for reuse/composition targets — you may *call* a core skill from a learned one, but never *edit* `core/`).
2. **Pick the A4 branch** per requirement:
   - **No coverage** → create a new skill.
   - **Exists but was never picked up** → *discovery* problem: fix the existing skill's **description** (and/or `paths`) so it surfaces — **not** its script.
   - **Picked up but fell short** → *capability* problem: fix its **script or steps**.
   - **Covered by composition** (A + B together, glue missing) → write a **thin** skill that **names** A and B in its body so the model loads them; promote to an agent only if Rule A1 holds and the role recurs (rare).
3. **Reuse before duplicating.** Decompose each requirement into the capabilities it needs and map each to an existing skill. *Example:* a "specific-table-search" need = **table-schema knowledge** + **running a query**. If a query-running skill already exists, **reuse it** (name it in the new skill's body) and add only the schema-specific part — do not re-implement query execution. Bundle a duplicated script only when sharing by reference would couple two skills that should stay independent.
4. **Extend without breaking.** When you modify an existing skill, changes are **additive** — preserve its current steps, scripts, and contract so existing callers keep working. If a change would alter existing behavior, prefer a new skill over a breaking edit.
5. **Write it.** New/changed skills go under top-level `skills/<domain>/<name>/` — `SKILL.md` (clear `name` + a `description` that says *when* to use it, so it's discoverable) plus `scripts/` if needed. For scripts that hit the vscode repo, use the env-contract convention (never hardcode the interpreter):
   ```bash
   PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
   ```
6. **Promote a scratch script** from the log's evidence into a bundled script only when its capability is repeatable; keep it deterministic (a script is a pure transform — the skill carries the runtime judgment; Rule A2).

## Boundaries
- Write **only** under top-level `skills/` (and `agents/` if A1 forces an agent). Never edit `core/` or `inputs/`; you may *reference* a core skill but not change it.
- One change per requirement, each traceable to the session-doc. Knowledge belongs to `wiki-writer`, not here.
