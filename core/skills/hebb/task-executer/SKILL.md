---
name: task-executer
description: Do hands-on engineering work against the vscode repo ($CODE_BASE) — find existing functionality, write scripts to inspect or invoke it, and run them in the correct venv. Use for any task that needs to read, run, or exercise vscode code. Every script it writes requires explicit user approval before it runs.
---

# Execute the task against the code

Hands-on work, usually against the vscode repo at `$CODE_BASE`. Prefer using what already exists over writing new code.

## Steps
1. **Understand** what must happen in or against the code.
2. **Explore `$CODE_BASE` first** for functionality that already does it (search/read with `--add-dir "$CODE_BASE"`). Calling existing code beats writing a script.
3. **Write a script only if needed**, to inspect details or invoke the task. It is **scratch** — ephemeral evidence, not a skill. Do not try to promote it; the maintainer decides promotion from the log.
4. **Get explicit user approval before running any script.** Show the user the script and what it will do, and wait for an explicit go-ahead. Never auto-execute. *(Hard rule of this skill.)*
5. **Run it in the right venv.** vscode is **not** pip-installed, so `import www` resolves only with the repo root on `PYTHONPATH`. Use the env contract — never hardcode the interpreter:
   ```bash
   PYTHONPATH="$CODE_BASE" "$VSCODE_PYTHON" <script.py> "$@"
   ```
   `CODE_BASE` = the vscode repo root; `VSCODE_PYTHON` = the interpreter whose venv has vscode's deps (`www`).
6. **Capture the result.**

Record via `log-appender`: the script you wrote (mark `scratch`, include the code or a pointer), how you ran it, that the user approved it, and what you observed.
