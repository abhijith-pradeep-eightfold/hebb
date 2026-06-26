# Shared utilities (`learned/hebb_utils/`)

Learned, domain-organized Python modules holding **deterministic logic shared by
more than one skill** — extracted here instead of duplicated. This is a *learned
artifact* (maintained by the injector's `skill-writer`), distinct from
`core/tools/`, which holds the engine's own tools (`publish.py`, `lint.py`, …).

## Why `hebb_utils`, not `utils`

The import root is **`hebb_utils`**, deliberately *not* `utils`. vscode ships its
own top-level `utils` package (`$CODE_BASE/www/utils`), and two different top-level
`utils` packages cannot coexist on one `sys.path` — `import utils` binds to whichever
is first, and the loser's submodules become unreachable. Since **most shared logic
here is vscode-dependent** (it imports `www` and therefore needs `$CODE_BASE/www` on
the path), the library must be importable *alongside* vscode code. The `hebb_`
prefix makes that collision-free: a script can `from hebb_utils.solr import …` and
the vscode code can `from utils.os_constants import …` in the same process.

## Layout

```
learned/hebb_utils/<domain>/<module>.py
```

Group by domain (e.g. `data_warehouse`, `solr`, `aws`). One concern per module.
A domain folder that is imported must be a valid Python identifier — **use
underscores, not hyphens** (`data_warehouse`, not `data-warehouse`).

## How skills use it

A skill keeps its **invoked entry point** under its own dir so the bash execution
policy (`core/tools/bash_exec_policy.py`) lets it run unattended. Most entry scripts
are vscode-dependent, so the canonical run shape puts `www` on the path:

```bash
PYTHONPATH="$CODE_BASE/www" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/X.py" "$@"
```

(`PYTHONPATH="$CODE_BASE"` is fine for a www-free script; either way `hebb_utils`
resolves via the walk-up below and never clashes with vscode's `utils`.)

`X.py` then *imports* the shared module — walking up to the dir that contains
`hebb_utils/` (i.e. `learned/`) and putting it on `sys.path` first (no hardcoded
nesting depth) so the import resolves without changing the run command:

```python
import os, sys
_d = os.path.dirname(os.path.realpath(__file__))
while not os.path.isdir(os.path.join(_d, "hebb_utils")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
from hebb_utils.solr.shard_hosts import resolve_shard_hosts
```

The gate keys on the *invoked* path being anchored under a skill, which imports
don't affect — so importing shared logic keeps the skill running unattended.

## Rules

- Utilities are pure, deterministic transforms (Rule A2) — runtime judgment lives in the skill.
- Extract here when ≥2 skills need the same logic; duplicate only if sharing would couple skills that must stay independent.
- Never hardcode session-observed values (hosts, IDs, assignments); fetch live state at runtime.
- **Import root is `hebb_utils`, never `utils`** — so vscode-dependent modules can be imported in the same process as vscode code (see *Why `hebb_utils`* above). vscode-dependent modules import `www` at call time and need their caller to run with `PYTHONPATH=$CODE_BASE/www`.
