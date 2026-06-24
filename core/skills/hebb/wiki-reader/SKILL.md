---
name: wiki-reader
description: Read the compiled Hebb wiki to understand a domain, component, functionality, or project before doing work. Use whenever you need to know how part of the system works — consult the wiki first instead of re-deriving from raw source. Reads natively: start at the top-level wiki index and follow wikilinks.
---

# Read the wiki

The wiki (`wiki/`) is Hebb's compiled knowledge — "what is true" about the system. Query it before re-reading raw sources. There is **no query tool**; you read it natively.

## Steps
1. **Read the top-level index** — the navigation root at `wiki/index.md`. It lists and links the pages in the wiki.
2. **Follow wikilinks** (`[[page-name]]`) from the index to the pages you need, and follow links between pages until you understand the part relevant to your task. Pages are grouped into `wiki/<domain>/` subfolders, but the domain is just how the maintainer organizes knowledge — navigate by the links, not by guessing folders.
3. **Synthesize** only what bears on the task at hand.

## When the wiki falls short
If `wiki/index.md` is missing, the index doesn't link your topic, or a wikilink is dead, treat it as a **gap** — note the symptom ("no wiki page for X", "index missing X") so `log-appender` records it. Do not invent an answer; fall back to other skills (`task-executer` to inspect `$CODE_BASE`, `external-context-puller` for ticket/thread context) and record what you actually found.

After reading, record via `log-appender` what you consulted and what you learned (or that coverage was missing).
