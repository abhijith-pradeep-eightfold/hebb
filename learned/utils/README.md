# Shared utilities (`learned/utils/`)

Learned, domain-organized Python modules holding **deterministic logic shared by
more than one skill** — extracted here instead of duplicated. This is a *learned
artifact* (maintained by the injector's `skill-writer`), distinct from
`core/tools/`, which holds the engine's own tools (`publish.py`, `lint.py`, …).

## Layout

```
learned/utils/<domain>/<module>.py
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

`X.py` then *imports* the shared module — walking up to the dir that contains
`utils/` (i.e. `learned/`) and putting it on `sys.path` first (no hardcoded
nesting depth) so the import resolves without changing the run command:

```python
import os, sys
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "utils")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
from utils.data_warehouse import helper
```

The gate keys on the *invoked* path being anchored under a skill, which imports
don't affect — so importing shared logic keeps the skill running unattended.

## Rules

- Utilities are pure, deterministic transforms (Rule A2) — runtime judgment lives in the skill.
- Extract here when ≥2 skills need the same logic; duplicate only if sharing would couple skills that must stay independent.
- Never hardcode session-observed values (hosts, IDs, assignments); fetch live state at runtime.
- **`learned/utils` is for www-free logic.** vscode ships its own top-level `utils` package (`$CODE_BASE/www/utils`), so a script that puts `$CODE_BASE/www` on its path **cannot also import `learned/utils`** — `import utils` binds to whichever is first on `sys.path`, and once bound the other's submodules are unreachable. Keep importers of `learned/utils` www-free (run them with `PYTHONPATH="$CODE_BASE"`, not `$CODE_BASE/www`). When a combined skill needs a **www-coupled** stage, reach it via a **subprocess** that sets `PYTHONPATH=$CODE_BASE/www` (so the vscode import happens in its own process) rather than importing it in-process — see `solr-shard-cpu`'s `shard_cpu.py`, which subprocesses `solr-shard-dns-lookup`'s `get_shard_hosts.py` for exactly this reason.
