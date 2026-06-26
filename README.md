# Hebb

Hebb compiles the experience of software-engineering agents into durable, interlinked artifacts — a wiki and a skill library — so future agents query the compiled result instead of re-deriving knowledge from raw sources every time. It extends Karpathy's LLM-Wiki pattern to SE agent workflows.

## The idea

A Claude Code SE agent does real work against a codebase. As it works, it writes an observations-only **session-doc** (witness log) into `inputs/`. The Hebb maintainer then **compiles** that log once into:

- **`learned/wiki/`** — "what is true": entity and concept pages, interlinked, covering the codebase, infra, and process.
- **`learned/skills/`** — "how to do X": reusable skill files that future agents load to avoid repeating manual steps.
- **`learned/agents/`** — roles: custom agent definitions (rare; created on demand, only when a skill isn't enough).

The compile is a pipeline: `task-analyser` → `wiki-writer` + `skill-writer`. Everything in `inputs/` is immutable history; everything under `learned/` is compiled output that the maintainer owns and keeps current.

## Repo layout

```
core/                   # The engine — maintainer instructions, core skills, tools
  CLAUDE.md             # Project guide: understanding + command directives (role-neutral)
  agents/               # hebb (SE agent) and hebb_injector (maintainer) agent defs
  skills/
    maintainer/         # task-analyser, wiki-writer, skill-writer
    hebb/               # Skills available to the SE agent (task-executer, log-appender, …)
    common/             # Shared skills (wiki-reader, used by both roles)
  tools/
    publish.py          # Regenerates .claude/ symlinks + the wiki Skills catalog
    bash_exec_policy.py # Gate: auto-allows bundled skill scripts, prompts others
    lint.py             # Structural checker for the injector's self-correction loop
    inject_wiki_index.py   # SessionStart hook: injects learned/wiki/index.md as context
    log_cadence_check.py   # Stop hook: nudges the SE agent to keep its log current
    intervention_report.py # Cross-doc intervention tabulation + rate over time

learned/                # Compiled output of the engine
  skills/               # Learned skills (output of skill-writer)
  wiki/                 # Wiki pages (output of wiki-writer); learned/wiki/skills/index.md is the generated Skills catalog
  utils/                # Shared utilities (deterministic logic shared by skills)
  agents/               # Learned agents — created on demand (rare)
inputs/                 # Immutable witness logs from SE agent sessions
.claude/                # Runtime symlinks into core/ and learned/ — don't hand-edit
```

## Two agents

| Agent | Role | How to invoke |
|---|---|---|
| **`hebb`** | SE agent — does work, writes the witness log | Start a Claude Code session in this repo; the `hebb` agent is loaded automatically |
| **`hebb_injector`** | Maintainer — compiles one session-doc into wiki + skills, then opens a PR | `@hebb_injector inputs/<session-doc>.md` |

## The compile loop

1. The SE agent works on a task, appending observations to a session-doc in `inputs/` via `log-appender` — each step carrying a `proof:` vscode link, the full scratch script inline, and an `effort:` note, plus an `[INTERVENTION]` entry every time a human steps in.
2. A human invokes the injector **manually** with that doc path (the SE agent never auto-triggers it).
3. **`task-analyser`** reads the doc, mines every intervention into a requirement, weights by effort, flags wiki/code conflicts, and emits a knowledge writeup + skill requirements.
4. **`wiki-writer`** compiles the knowledge into `learned/wiki/` (checks existing pages first), adds explicit loadable skill mentions + `## Related skills`, and reconciles conflicts against the live code (current code wins).
5. **`skill-writer`** handles each skill requirement per Rule A4: reuse/extend/compose/create, reusable-by-default with required/optional knowledge, extracting shared logic to `learned/hebb_utils/`.
6. `core/tools/publish.py` regenerates the `.claude/` symlinks and the wiki Skills catalog.
7. `core/tools/lint.py` runs as the checker; the injector loops fix→publish→lint until clean.
8. The injector then **stops and reports**; it opens a PR only when you ask (never automatically).

## Environment variables (for skills that run against the codebase)

| Var | What it points at |
|---|---|
| `CODE_BASE` | Root of the `EightfoldAI/vscode` repo |
| `VSCODE_PYTHON` | Python interpreter whose venv has vscode's dependencies |

Skills that run Python against the codebase use the canonical invocation:
```bash
PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
```

## More detail

- **Project guide (understanding + command directives)**: `core/CLAUDE.md`
- **Maintainer manual (injector loop, judgment rules A1–A8, fixing-at-source, learning loop, knowledge↔skill graph)**: `core/agents/hebb_injector.md`
- **SE agent rules (witness role, log cadence, write boundary)**: `core/agents/hebb.md`
- **Wiki**: start at `learned/wiki/index.md` and follow the wikilinks
- **Individual skill docs**: read the `SKILL.md` in each skill directory
