---
name: log-appender
description: Append a structured, observations-only entry to the session log in inputs/. Use after every step you take and every skill you invoke during a Hebb SE task to record what was observed, the full scratch scripts written, vscode proof links, the effort it took, and every human intervention. Creates the log file on first use.
---

# Append to the session log

As a Hebb SE task progresses, keep a running log in `inputs/` that the maintainer later compiles into wiki pages and skills. You append to this log **continuously — after each step you take and after each skill you invoke**, before you reply to the user or start the next step. Never batch several steps into one entry at the end.

## Where it lives
One file per task: `inputs/YYYY-MM-DD-<short-slug>.md` (today's date + a 2–4 word slug). Reuse the same file for every append in the session. Get the time for an entry with `date +%H:%M`.

## Create the file on first append
On the first append, create the file with this header — a thin YAML frontmatter (a convenience index; the body is the source of truth) followed by the task line and the `## Log` heading:

```markdown
---
task: <one-line, what the user asked, in your words>
date: <YYYY-MM-DD>
skills_used: []        # as each skill fires, add `- {name: <skill>, note: <what you observed>}`
interventions: 0       # bump by one each time you log an [INTERVENTION] entry
---

# <Task title>

**Task:** <what the user asked, in your words>

## Log
```

Keep the frontmatter current as you go: append to `skills_used` when a skill fires, and bump `interventions` when you log an intervention. The frontmatter is only an index — if it ever drifts, the body wins.

## Per-step entry
After each step, append a block:

```markdown
### [HH:MM] <skill-name | step label>
- **observed:** <what you did and what you saw>
- **proof:** <a vscode repo link (repo-relative `path:line`, e.g. `www/db/db_type.py:21`) for each claim you make about code; cite what you already read — never open a file just to fill this; omit only if the step made no code claim>
- **script:** <the FULL script source, inline in a fenced code block, marked `scratch`, plus how you invoked it — never just a path pointer, so the maintainer reads the complete code and can promote it; omit if none>
- **effort:** <an observational note of what reaching this result took — breadth/depth of $CODE_BASE exploration, dead-ends and trial-and-error, external lookups, whether it had to be derived from scratch vs. already in the wiki; this is NOT a count of how many times a script ran; include it on any substantive step, omit only when trivial (e.g. a single wiki read)>
- **user input:** <directions or answers the user gave, near-verbatim; omit if none>
```

Tag the entry with the skill's stable `name` when you invoke a skill; use a short step label otherwise.

## Intervention entry — log it the moment a human steps in
Any time a human has to step in — a correction, a direction, an approval, a clarification, or a "no, do it this way" — append a dedicated entry **before you act on it**:

```markdown
### [HH:MM] [INTERVENTION] <one-line: what the human supplied>
- **observed:** <what you were doing or had produced when the human stepped in>
- **human supplied:** <near-verbatim, what they said / corrected / approved / clarified>
- **type:** correction | direction | approval | clarification | rejection
- **source:** actual-user | coordinator-relayed
- **what was missing:** <state the ABSENCE factually — "no wiki page named the config key", "no DNS-lookup skill fired" — never prescribe a fix>
```

Then bump the `interventions` counter in the frontmatter. `type` and `source` are fixed enumerations — pick one, don't editorialize. `source: coordinator-relayed` is context relayed through an orchestrating agent; `actual-user` is the user's own message.

## The one rule: observations only
Record what you did and what you saw. Do **not** judge whether a skill was the right one, whether a result was success or failure, *why* something was missing, what domain it belongs to, or that something "should be a skill / page / agent." Stating an **absence** is allowed and required — "no skill fired", "the wiki had no page for X", "the user had to correct the collection name" are observations, not judgments. The maintainer derives every fix from them.

Two things are now first-class observations, not extra judgment:
- **Every human step-in** gets its own `[INTERVENTION]` entry the moment it happens. Flagging *that* a human stepped in is observation; deciding *what capability would have prevented it* is the maintainer's job.
- **A `proof:` vscode link** for each code claim, and the **full scratch script inline**. You are citing what you saw and preserving what you wrote — not judging it.
