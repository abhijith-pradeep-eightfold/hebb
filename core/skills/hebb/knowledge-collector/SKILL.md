---
name: knowledge-collector
description: Use when the task is to gain knowledge rather than change code — researching or documenting how something works, or capturing facts the user teaches you. Gathers the knowledge, arranges it by topic, and hands it to log-appender as raw material for the maintainer to compile into the wiki.
---

# Collect knowledge

Some tasks are not "change the code" but "learn / document X" or "here are facts you should capture." This skill gathers that knowledge and arranges it so the maintainer can compile it into the wiki.

## Steps
1. **Recognize the mode.** The user asks you to learn / understand / document something, or directly tells you facts to remember.
2. **Gather.** Ask the user focused clarifying questions when they are the source. When you must research, compose with other skills: `wiki-reader` for what Hebb already knows, `external-context-puller` for ticket/thread context, `task-executer` to inspect `$CODE_BASE`.
3. **Arrange it.** Group related facts under clear topic / component headings; note relationships between them. Keep it **factual** — record what is true, not conclusions about which Hebb artifact it should become.
4. **Hand it off.** Write the arranged knowledge to the session log via `log-appender`, under a clear topic heading, so it becomes durable raw material for the maintainer → wiki.

Stay a witness: collect and organize facts; the maintainer decides domain and placement.
