# Hebb v2 — Finalized Decisions & Implementation Plan

**Status:** all §12 open questions resolved. Design is locked for a first end-to-end build.
**Companion to:** the Hebb v2 Design & Handoff Document (the source-of-truth design narrative).
**This document:** (Part A) the finalized resolutions that supersede/extend the open items, and (Part B) a phased, step-by-step implementation plan.

---

## Part A — Finalized resolutions (closing §12)

### A1. Skill-vs-agent boundary  *(was §12.1 — LOCKED)*

**Rule — the boundary is context isolation and stance, not amount of judgment.**

- A **skill** augments the *current* reasoner: name+description always loaded, body injected on demand into the caller's context. No separate context window, no separate persona, no tool restriction.
- An **agent** is a *separate context window* with its own system prompt and tool policy, **delegated** a job and returning a result.

> **Skill by default. Promote to agent only when at least one holds *and recurs across docs*:**
> (a) the job needs its **own context window** — long-horizon, high intermediate-state, or repeated over many items (would pollute/overflow the caller); OR
> (b) it needs a **different stance or tool policy** than its caller — read-only explorer, propose-don't-execute, reviewer; OR
> (c) it's naturally **delegated** ("do it all, report back") rather than consulted ("tell me how").
>
> **Size alone never promotes; context-isolation need does.** Judgment is not the discriminator (skills encode judgment too).

Solr calibration: `find-shard-owner` → script; `replace-shard`, `reindex-single-core` → skills; `sequence-the-cross-core-migration` → agent (long-horizon, high intermediate-state, planning stance, recurs as a role).

### A2. Skill-vs-script rule  *(was §12.2 — LOCKED)*

**The artifact boundary follows the judgment boundary, not the code boundary.**

- **Script** = deterministic transform: same inputs → same outputs, no runtime choice. A skill may *call* scripts.
- **Skill** = a unit where the *model must decide at runtime* using context that cannot be pre-baked.

**Corollary (resolves "`requires:` edge vs. bundled script"):**
> `requires:` edges only ever point **skill→skill** (judgment→judgment). Deterministic sharing is *never* a `requires:` edge — it lives on the script/tool axis (default: duplicate-and-bundle per §9; graduate to `tool` only when it bites / needs logic).

**Orthogonality note:** *deterministic ≠ stable.* A script can be a pure function of its inputs (so: script, not skill) yet import vscode and break when vscode moves (the §10.5 case). "Is it a script?" (judgment test) and "does it carry `requires_env`?" (dependency declaration) are independent axes.

### A3. Session-doc body format  *(was §12.3 — LOCKED)*

Frontmatter unchanged (`skills_used` with per-invocation `note`). Body = **witness account, observations only — no diagnosis, no verdict, no domain, no suggestions** (strict §3.1 purity). Five sections:

1. **Task** — what the user asked, in the agent's words.
2. **What I did** — the actual steps in order, with user directions inline. The narrative spine.
3. **Skills & scripts in play** — prose evidence behind each `skills_used` note: how each skill behaved *in context*; any **scratch scripts** written (code or pointer, marked scratch), incl. ones written against `$CODE_BASE`.
4. **What I learned** — facts discovered about the system (raw material for wiki pages).
5. **Friction & gaps** — **symptoms only**: where it got stuck, what was missing, "no skill fired," "skill X returned empty." No diagnosis ("Hebb is missing a feedback-index skill") and **no suggestions** — that is exclusively the maintainer's job.

### A4. Maintainer skill-search step  *(was §12.4 — DESIGNED; detail in Phase 3)*

Skill-search is **coverage analysis over the skill graph**, not description-matching. Given a candidate procedure, it emits exactly one verdict, each mapping onto a §7 branch:

| Verdict | Meaning | §7 action |
|---|---|---|
| **No coverage** | nothing matches semantically | create new skill |
| **Covered but undiscovered** | a skill matches but the doc shows it wasn't found | fix **description / `paths`** (discovery fix) — *not* the script |
| **Partially covered** | picked up but fell short | fix **script / steps** (capability fix) |
| **Covered by composition** | pieces (A+B) exist, glue missing | create a composition skill, or an agent if it needs its own context/stance (A1) |

Mechanism: CC loads every skill's name+description at startup, so the maintainer holds the full skill index cheaply; the `requires:` edges supply the composition graph for free (this is *why* declared edges matter). Implemented inline in the maintainer loop first (self-consistency rule); promote to its own `skill-coverage-search` skill only if it bites.

### A5. Source → runtime sync placement  *(was §12.5 — LOCKED)*

**Inside the maintainer loop**, as a **deterministic generated artifact**, committed in a **separate commit** within the same PR. Review reads the domain-tree diff as the substantive change; the flat `.claude/skills/` tree is mechanical output. The sync is itself a **script** (pure transform: domain path → flat domain-qualified name; sets `paths` from domain) — consistent with A2.

### A6. `$CODE_BASE` invocation fact  *(was §12.6 — RESOLVED EMPIRICALLY; corrects §10.1–10.2)*

Verified against the real repo at `/home/ec2-user/vscode`:

- **No `.venv` in the repo.** The interpreter is a *sibling* venv (here `/home/ec2-user/py3.13-virt/bin/python`, Python 3.13.1) with all deps installed. The path is **machine/CI-specific**.
- **Not editable-installed** (no `.pth`, repo root not on `sys.path`). `import www` fails bare.
- **`import www` works only with `PYTHONPATH=$CODE_BASE`** — `www/` has no `__init__.py`, so it resolves as a PEP 420 namespace package.

**Corrected canonical invocation (replaces §10.1):**
```bash
PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
```
**Two env contracts, not one → `requires_env: [CODE_BASE, VSCODE_PYTHON]`.** `CODE_BASE` = repo root (for `PYTHONPATH` + `--add-dir`); `VSCODE_PYTHON` = the deps interpreter (env-specific → must be a var, never schema-hardcoded).

### A7. `tool` as a 4th artifact type  *(was §12.7 — NO, with a sharper trigger)*

Stays three types. #6 changes the promotion trigger: the venv prefix is now a *two-variable static string* — still zero shared code, still a CLAUDE.md convention. Promote to a `tool` only when the prefix needs **logic** (find-the-venv, region fallback, set N vars), not mere reuse.

---

## Part B — Phased implementation plan

Philosophy: **walking skeleton first.** Get the thinnest ingest→PR path working, *then* add the discriminating intelligence (§7), *then* lint, *then* `$CODE_BASE`. Honor the self-consistency rule throughout: one maintainer, inline logic, no creator sub-agents until forced.

### Phase 0 — Contracts & substrate verification  *(no maintainer intelligence)*

- **0.1 Verify the load-bearing CC substrate claims** (§8.2) before building on them. Specifically confirm: (i) `paths` frontmatter scopes *auto-activation* without scoping *discovery*; (ii) `--add-dir` loads a nested `.claude/skills/` but not CLAUDE.md by default; (iii) `${CLAUDE_SKILL_DIR}` resolves to the running skill's own dir. *(Use the claude-code-guide agent.)*
- **0.2 Lock the repo layout.** Source tree: `sessions/` (immutable input), `wiki/<domain>/…`, `skills/<domain>/<name>/{SKILL.md,scripts/}`, `agents/`. Generated: `.claude/skills/<domain-qualified-name>/`. Plus `CLAUDE.md` (the schema) and a `lint/` + `tools/` area.
- **0.3 Write the session-doc spec + template** (A3): frontmatter schema (`skills_used`) + the 5 body sections, with the observations-only boundary stated in the template itself.
- **0.4 Write the declared-edge vocabulary spec** (§4): `wiki_context` (advisory/flag), `requires` (skill→skill, load-bearing/fault), `requires_env` (fault), `paths` (activation scope). One short doc the maintainer and lint both read.
- **0.5 Write the env-contract doc** (A6): `CODE_BASE` + `VSCODE_PYTHON`, the canonical invocation prefix, and the namespace-pollution watch-out for vscode's own `.claude/`.

**DoD:** a fresh agent can read 0.2–0.5 and produce a valid (empty-but-well-formed) session-doc and skill directory by hand.

### Phase 1 — Deterministic substrate tools  *(no LLM)*

- **1.1 Sync script** (A5): domain `skills/<domain>/<name>/` → flat `.claude/skills/<domain-qualified-name>/`; sets `paths` from domain; idempotent; pure transform.
- **1.2 Wiki tool** (§4.1): `get <path> --depth N` (entry full + N-hop neighbor *summaries only*, default N=2) and `get <path...>` (full). Reads the link graph from wiki-links/frontmatter.
- **1.3 Lint v0 — structural checks only:** broken `requires` (fault), broken wiki-link (flag), orphan page (flag), `requires_env` declared-vs-used mismatch (defect: grep scripts for `$CODE_BASE`/`$VSCODE_PYTHON`), near-duplicate scripts (flag). **Defer** the judgment-based "unreachable skill" check to Phase 3.

**DoD:** given a hand-built source tree, `sync` emits a runnable `.claude/skills/`, `wiki get` returns correct shapes, and `lint` flags a deliberately-broken fixture.

### Phase 2 — The maintainer, walking skeleton  *(§14's first milestone)*

- **2.1 Write the schema (`CLAUDE.md`)** — the maintainer's operating manual: the loop (§6), classification rules (A1, A2), the `$CODE_BASE` convention (A6), the declared-edge discipline. This is the config that turns a generic agent into the maintainer.
- **2.2 End-to-end on ONE hand-written session-doc** that yields exactly **one wiki page + one new skill**. Output = a branch/PR diff across `wiki/`, `skills/`, + the synced `.claude/skills/`.
- **2.3 PR/diff packaging:** branch naming; commit split = substantive (`wiki/`+`skills/`) vs. generated (`.claude/skills/` sync, separate commit per A5); every diff hunk traceable to the session-doc (§5).

**DoD:** `ingest one session-doc → wiki page + one skill + flat build target → open PR`, fully traceable.

### Phase 3 — The discriminating maintainer  *(the heart, §7)*

- **3.1 Skill-search / coverage analysis** (A4): the 4-verdict step over the skill index + `requires` graph, with branch logic (create / fix-description / fix-script / compose-or-agentize).
- **3.2 Lint — "unreachable skill" category:** description doesn't match the situations it's for; surfaced as its *own* category, distinct from a broken skill (the skill-layer analog of the orphan-page lint).
- **3.3 Agent-creation decision** (A1): the context/stance test + the recurrence-across-docs gate + the high bar.

**DoD:** feed three session-docs that each exercise a different §7 branch (new / undiscovered / partial); maintainer routes each correctly and the PR shows the right kind of change (description vs. script vs. new artifact).

### Phase 4 — `$CODE_BASE` integration

- **4.1 Resolve namespace pollution** (§10.3): confirm whether vscode's `.claude/` ships `skills/`; if so, decide mitigation before relying on `--add-dir`.
- **4.2 Authoring-against-vscode pattern:** `claude --add-dir $CODE_BASE` + the read-only Explore-fork for traversal ("go find what to call").
- **4.3 Scratch-script promotion path** (§10.4): agent writes ephemeral script → records it as evidence in the session-doc → maintainer decides promotion via PR. No new concept.
- **4.4 Lint — `requires_env` drift category** (§10.5): enumerate skills coupled to `$CODE_BASE` so they can be re-verified after a vscode refactor.

**DoD:** a session-doc containing a scratch script written against `$CODE_BASE` is ingested; maintainer promotes it into a skill with the correct invocation prefix and `requires_env: [CODE_BASE, VSCODE_PYTHON]`; lint can list all `$CODE_BASE`-coupled skills.

### Phase 5 — Hardening & iteration

Multi-skill session-docs; multi-page compiles with cross-references; agent composition; co-evolving the schema (`CLAUDE.md`) as patterns recur. Revisit `tool` only if the §A7 trigger fires.

---

## Appendix — Verified environment facts (for grounding)

- vscode repo: `/home/ec2-user/vscode` (EightfoldAI talent platform monorepo; source under `www/`, no `www/__init__.py`).
- Deps interpreter: `/home/ec2-user/py3.13-virt/bin/python` (Python 3.13.1; flask 2.2.5, gevent, … present). Repo not editable-installed.
- `import www` requires `PYTHONPATH=/home/ec2-user/vscode`.
- Deps installed via `pip install -r production/docker_configs/requirements.3.13.txt`; lint/format via `ruff`.
- vscode ships its own `.claude/` and `CLAUDE.md` (pollution risk per §10.3 — verify `.claude/skills/` in Phase 4.1).
