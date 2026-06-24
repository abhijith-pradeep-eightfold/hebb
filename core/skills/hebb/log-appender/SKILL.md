---
name: log-appender
description: Append a structured, observations-only entry to the session log in inputs/. Use after every step you take and every skill you invoke during a Hebb SE task to record what was observed, scripts written, and user input. Creates the log file on first use. Replaces the old session-doc writer.
---

# Append to the session log

You are a **witness**. As a Hebb SE task progresses, keep a running log in `inputs/` so the maintainer can later compile it into wiki pages and skills. You append to this log continuously — after each step you take and after each skill you invoke — instead of writing one document at the end.

## The log file (one per session)
- Path: `inputs/YYYY-MM-DD-<short-slug>.md` — today's date (`date +%F`) plus a 2–4 word slug of the task.
- **Create it on the first append** of the session with this header, then reuse the same file for every later append:

  ```markdown
  # <Task title>

  **Task:** <what the user asked, in your words>

  ## Log
  ```
- One file per task. If you already opened a log this session, append to it — do not start a new one.

## Each append = one entry
Add one block under `## Log`, in order:

```markdown
### [HH:MM] <skill-name | step label>
- **observed:** <what you did and what you saw>
- **script:** <path or inline pointer, marked `scratch`; omit if none>
- **user input:** <directions/answers the user gave, near-verbatim; omit if none>
```

- Tag the entry with the **skill's stable name** when the step was a skill invocation (`wiki-reader`, `external-context-puller`, `task-executer`, `knowledge-collector`); otherwise use a short step label.
- Get the time with `date +%H:%M`.

## The one rule: observations only
Record what you did and what you saw. Do **not** judge whether a skill was the right one, whether its result was success or failure, *why* something was missing, what domain it belongs to, or that something "should be a skill / page / agent." All of that is the maintainer's job. "No skill fired", "wiki had no page for X", and "skill returned empty" are correct, sufficient observations.
