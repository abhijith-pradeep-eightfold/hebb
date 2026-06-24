---
name: wiki-writer
description: File analysed knowledge into the Hebb wiki the way Karpathy's LLM-Wiki prescribes — check existing pages first, write or update entity and concept pages, cross-link them with wikilinks, and keep the single top-level index current. Use after task-analyser to compile a knowledge writeup into wiki/.
---

# Write the wiki

You are the injector's **knowledge stage**. Take the knowledge writeup from `task-analyser` and compile it into `wiki/` — Hebb's compiled, interlinked "what is true." This is Karpathy's LLM-Wiki pattern: raw sources (`inputs/`) are immutable; the wiki is yours to own and keep consistent; the schema (`core/CLAUDE.md`) is the convention. Compile once so future agents query the artifact, not the raw log.

## Steps
1. **Read the existing wiki first.** Use the `wiki-reader` skill: start at `wiki/index.md` and follow wikilinks to whatever this knowledge touches. The point is to **update existing pages, not spawn parallel ones** — find what already covers the topic before creating anything. (If `wiki/index.md` doesn't exist yet, this is the first page; create the index.)
2. **Choose granularity per fact.** The LLM picks the right shape for the domain:
   - **Entity page** — a concrete thing: a table, a service, a component, an env var, an adapter.
   - **Concept page** — a method, a pattern, a control-flow, a "how X works."
   - **Comparison / synthesis** — when several entities relate (e.g. one logical table across three warehouses).
   Prefer one well-linked page per entity/concept over a sprawling dump or many tiny stubs.
3. **Write/update each page** with: a **title**, a one-line **summary**, a factual **body** (carry the source anchors `task-analyser` gave — file path, symbol, line — so claims are checkable), and **wikilinks** to related pages. Group pages into `wiki/<domain>/` subfolders as an organizing aid (the domain is your grouping, not a structural requirement; subfolders get **no** index of their own).
4. **Update the single top-level index** (`wiki/index.md`) — the only navigation root. Add each new page as a link + one-line summary, organized by category. **Every new page must be reachable from the index and from at least one related page** — no orphans.
5. **Cross-reference both ways.** A single source can touch many pages; when page A cites B, make sure B links back. Add the wikilinks the new knowledge implies between *existing* pages too.
6. **Reconcile.** If the new facts supersede or contradict an existing page, **update the page** (correct the stale claim, note the supersession) rather than appending a second truth. The wiki must not hold two contradictory answers.
7. **Self-lint the pass** before finishing (Karpathy's lint set): no orphan pages, no dangling wikilinks, no contradictions left standing, no missing cross-references, no important concept mentioned-but-unlinked.

## Boundaries
- Write **only** under `wiki/`. Skills are `skill-writer`'s job; never write `skills/` or `core/`.
- Facts only — observations compiled into durable knowledge. Don't record "this should be a skill / page / agent"; that judgment already happened upstream.
