# op_registry — operation name → source file

**Summary:** `www/processor/op_registry.py` holds the central map from a processor **operation name** (the same string stored as `operation0` in [[processor-event-log|processor_event_log]]) to the Python class that implements it. Use it to go from an op seen in the warehouse to its "operation core file" in `$CODE_BASE`.

## The map

`OP_REGISTRY_MAP` (`www/processor/op_registry.py:9`) is a dict: **operation name → `(module_path, ClassName)`**.

| operation name (`operation0`) | module_path | class | source file |
|---|---|---|---|
| `sync_ats` | `processor.sync_ats_operation` | `SyncAtsOperation` | `www/processor/sync_ats_operation.py` (`op_registry.py:42`) |
| `ai_interview_competency_generation_operation` | `processor.ai_interview_competency_generation_operation` | `AIInterviewCompetencyGenerationOperation` | `www/processor/ai_interview_competency_generation_operation.py` (`op_registry.py:230`) |

The registry key **is** the `operation0` value — so a row's op name resolves directly. The `module_path` `processor.X` maps to the file **`www/processor/X.py`** (the package root is `$CODE_BASE/www` — see [[../vscode-repo/python-import-root|Python import root]]).

## How to resolve an op → file

Grep the op name in the registry, read the `(module_path, ClassName)` tuple, convert `processor.X` → `www/processor/X.py`:

```bash
rg -n --no-heading -S "'<operation0>'" "$CODE_BASE/www/processor/op_registry.py"
```

Once you have the file, resolve **who owns it** via [[../repo/codeowners-ownership|CODEOWNERS ownership]] (with a git-authorship fallback).

## Related skills

- `codeowners-owner` — use it to go from an `operation0` name to its source file (via this registry) and on to the file's owning team/person.

## Related

- [[processor-event-log|processor_event_log table]] — where the `operation0` value comes from.
- [[../repo/codeowners-ownership|CODEOWNERS ownership]] — map the resolved source file to its owning team / authors.
- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — uses op→file→owner to route a backed-up-queue incident to a team.
