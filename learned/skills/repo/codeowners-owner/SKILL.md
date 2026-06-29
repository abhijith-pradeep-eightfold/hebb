---
name: codeowners-owner
model: sonnet
description: >-
  Resolve who owns a source file (or a processor operation) in the EightfoldAI/vscode repo
  from .github/CODEOWNERS, applying GitHub's last-matching-pattern-wins semantics, with a
  git-authorship fallback when a file matches no rule. Use whenever a task needs to route
  code to its owning team/person — "who owns this file", "which team owns operation X",
  "who do I ping for sync_ats_operation.py", "find the CODEOWNERS owner", or routing an
  incident (e.g. a traced processor op) to a team. For mapping an operation0 name to its
  source file first, this skill includes the op_registry lookup step.
knowledge_required:
  - "[[../../../wiki/repo/codeowners-ownership|CODEOWNERS ownership resolution]]"
knowledge_optional:
  - "[[../../../wiki/processor/op-registry|op_registry: operation name → source file]]"
  - "[[../../../wiki/oncall/queue-backed-up|Queue backed up (oncall)]]"
  - "[[../../../wiki/oncall/solr-cpu-high|Solr CPU too high (oncall)]]"
  - "[[../../../wiki/oncall/alarm-provisioning-failures|Alarm Provisioning Failures (oncall)]]"
---

# Resolve the owner of a file or processor op

Map a repo-relative path (or a processor `operation0` name) to its owning team/person. Owner resolution follows GitHub CODEOWNERS **last-matching-pattern-wins** semantics; an unmatched file has **no formal owner** (there is no global `*` default), in which case fall back to git authorship. See [[../../../wiki/repo/codeowners-ownership|CODEOWNERS ownership]].

## Steps

1. **(If starting from an op name) resolve the operation to its source file** via the [[../../../wiki/processor/op-registry|op_registry]] map — grep the op name and read the `(module_path, ClassName)` tuple; `processor.X` → `www/processor/X.py`:
   ```bash
   rg -n --no-heading -S "'<operation0>'" "$CODE_BASE/www/processor/op_registry.py"
   ```

2. **Resolve the file's CODEOWNERS owner(s)** with the bundled resolver (applies last-match-wins + gitignore-glob; defaults to `$CODE_BASE/.github/CODEOWNERS`):
   ```bash
   PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/codeowners_for.py" <path> [<path> ...]
   ```
   It prints, per path, the winning rule (+ line number) and owners, or `(none — no matching CODEOWNERS rule)`.

3. **If a file has no owner, fall back to git authorship** (the de-facto owner):
   ```bash
   git -C "$CODE_BASE" log --format='%an' -- <file> | sort | uniq -c | sort -rn | head
   ```
   A sole/dominant author is the practical owner to route to.

## Notes

- A CODEOWNERS team handle is `@org/team` (e.g. `@EightfoldAI/dp-integrations`). Resolving it to the team's **members** needs a GitHub token with org-team read; a fine-grained PAT without it returns 403/404 — the handle itself is the answer when the roster is inaccessible (see the wiki page).
- The resolver is pure stdlib and read-only; it parses CODEOWNERS and matches paths, touching no other repo state.
