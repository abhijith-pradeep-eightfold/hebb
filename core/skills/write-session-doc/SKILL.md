---
name: write-session-doc
description: Use at the end of any Hebb SE task to record a session-doc witness account in inputs/. Captures skills_used plus a five-section, observations-only body for the maintainer to compile.
---

# Write a session-doc

After finishing a software-engineering task as a Hebb agent, record what happened so the maintainer can compile it into wiki pages and skills.

1. Create `inputs/YYYY-MM-DD-<short-slug>.md` (today's date; a 2–4 word slug of the task).
2. Copy the structure from `core/templates/session-doc.md`.
3. Fill the frontmatter `skills_used`: one entry per skill you invoked, each with the skill's stable `name` and a `note` of what you **observed** it do. If no skill fired, delete the list.
4. Fill the five body sections: **Task / What I did / Skills & scripts in play / What I learned / Friction & gaps**.

## The one rule: observations only
You are a witness. Record what you did and what you saw. Do **not**:
- judge whether a skill was the right one, or whether its result was success/failure,
- diagnose *why* something was missing,
- assign a domain or placement,
- suggest that something "should be a skill / agent / page".

All of that is the maintainer's job. Reporting "no skill fired" or "skill X returned empty" is correct and sufficient.
