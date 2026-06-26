# Shared script library (`scripts/tools/`)

Learned, domain-organized Python modules holding **deterministic logic shared by
more than one skill** — extracted here instead of duplicated. This is a *learned
artifact* (maintained by the injector's `skill-writer`), distinct from
`core/tools/`, which holds the engine's own tools (`publish.py`, `lint.py`, …).

## Layout

```
scripts/tools/<domain>/<module>.py
```

Group by domain (e.g. `data_warehouse`, `solr`, `aws`). One concern per module.
A domain folder that is imported must be a valid Python identifier — **use
underscores, not hyphens** (`data_warehouse`, not `data-warehouse`).

## How skills use it

A skill keeps its **invoked entry point** under its own dir so the bash execution
policy (`core/tools/bash_exec_policy.py`) lets it run unattended:

```bash
PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
```

`X.py` then *imports* the shared module — adding the repo's `scripts/` to
`sys.path` first so the import resolves without changing the run command:

```python
import os, sys
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), "scripts"))
from tools.data_warehouse import helper
```

The gate keys on the *invoked* path being anchored under a skill, which imports
don't affect — so importing shared logic keeps the skill running unattended.

## Rules

- Scripts are pure, deterministic transforms (Rule A2) — runtime judgment lives in the skill.
- Extract here when ≥2 skills need the same logic; duplicate only if sharing would couple skills that must stay independent.
- Never hardcode session-observed values (hosts, IDs, assignments); fetch live state at runtime.
