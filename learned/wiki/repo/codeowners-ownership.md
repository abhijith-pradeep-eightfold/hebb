# CODEOWNERS ownership resolution

**Summary:** How to resolve **who owns a source file** in the `EightfoldAI/vscode` repo from `.github/CODEOWNERS`, the **last-matching-pattern-wins** rule, the fact that an unmatched file has **no formal owner**, and the git-authorship fallback. Used to route an incident (e.g. a backed-up queue traced to an op file) to a responsible team or person.

## The file and the rule

- `.github/CODEOWNERS` (~1300 lines) maps glob patterns → owners (`@org/team` handles and/or `user@…` emails).
- **Last matching pattern wins** (GitHub/gitignore semantics): a file's owner is the owners of the *last* line whose pattern matches the path — not the most specific by depth, but the last by file order. Patterns use gitignore globbing: a leading or internal `/` anchors to repo root; a pattern with no `/` floats to any depth; a trailing `/` matches directory contents; `*` matches within a path segment, `**` across segments.
- **No global `*` default in this repo** — so a file that matches *no* pattern has **no formal owner**. (Confirmed: there is no `*`-default line.)

Example: `/www/processor/sync_ats_operation.py @EightfoldAI/dp-integrations` (`.github/CODEOWNERS:361`). By contrast `www/processor/ai_interview_competency_generation_operation.py` matches no pattern → no CODEOWNERS owner.

## Resolving a path

To apply last-match-wins correctly (rather than grepping and eyeballing), **use the `codeowners-owner` skill** — it parses CODEOWNERS, applies gitignore-glob + last-match-wins, and prints the winning rule + owners (or "none").

## Git-authorship fallback

When a file has no CODEOWNERS owner, the **de-facto owner is its git author(s)**:

```bash
git log --format='%an' -- <file> | sort | uniq -c | sort -rn | head   # top authors
git log -3 --format='%ad %an — %s' --date=short -- <file>             # recent changes
```

A sole/dominant author is the practical owner. (In the witnessed incident, the unowned culprit op's sole author matched the engineer tagged in the incident thread.)

## Limitation — team handle → members

A CODEOWNERS team handle `@org/team` (e.g. `@EightfoldAI/dp-integrations`) is the **owner identifier**, but resolving it to the team's **display name / member roster** needs a GitHub token with **org-team read** permission. A fine-grained PAT lacking it returns **HTTP 403** on `gh api orgs/<org>/teams` (list) and **404** on `gh api orgs/<org>/teams/<team>[/members]` (named). Treat that as a credential-scope limitation, not a missing team — the handle/slug itself is the answer when the roster is inaccessible.

## Related skills

- `codeowners-owner` — use it to resolve a file's (or a processor op's) owning team/person via CODEOWNERS last-match-wins, with a git-authorship fallback.

## Related

- [[../processor/op-registry|op_registry]] — map an `operation0` to its source file first, then resolve that file's owner here.
- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — routes a traced root/culprit op to its owners via this page.
- [[../oncall/airflow-dag-failure|Airflow DAG Failure (oncall)]] — routes the failing DAG/deploy source files (`deploy_azure_server.py` → core-infrastructure + app-infra; the DAG → app-infra; `app_service_utils.py` → no rule) via this page.
- [[../vscode-repo/python-import-root|Python import root]] — repo layout (`$CODE_BASE/www`).
