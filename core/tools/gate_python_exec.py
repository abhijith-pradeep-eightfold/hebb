#!/usr/bin/env python3
"""PreToolUse(Bash) hook: gate ad-hoc python execution; trust skill scripts.

Hebb's task-executer rule is "explicit user approval before running any script."
But a python script that lives *inside a skill* is a vetted, maintainer-promoted
artifact — re-prompting for it every time is the friction that caused the
approval deadlock. So we split on *where the script lives*, which a static
permission rule can't see (the path is mid-command, not a prefix):

  - clean python run of a SKILL-bundled script  -> allow  (run directly)
  - any GENERATED / scratch python execution     -> ask    (require approval)
  - not a python execution                        -> no opinion (fall through)

"Skill-bundled" = the script path is under a skills/ dir (learned `skills/`,
`core/skills/`, the runtime `.claude/skills/`) or referenced via
${CLAUDE_SKILL_DIR}. Anything else — /tmp, a scratchpad, `python -c`, a bare
`.py` in the cwd — is treated as generated and gated.

Safety: we only ever `allow` a *single, clean* python invocation. The moment a
command chains (`&&`, `;`, `|`), redirects, or substitutes (`$(...)`, backticks),
we fall back to `ask` so an allowed skill script can't smuggle a second command.
On any parse trouble we emit no decision and let the normal prompt happen.

I/O contract: read the PreToolUse JSON on stdin; print a hookSpecificOutput
decision on stdout (or nothing for "no opinion"); always exit 0.
"""
import json
import re
import shlex
import sys

# A command token that names a python interpreter (basename or $VSCODE_PYTHON).
_PY_INTERP = re.compile(r"(^|/)python(3(\.\d+)?)?$")

# A skills/ component anywhere in a path: learned skills/, core/skills/, .claude/skills/.
_SKILL_PATH = re.compile(r"(^|/)(\.claude/)?(core/)?skills/")

# Shell features that make a single "allow" unsafe (chaining / redirection / subst).
_COMPLEX = re.compile(r"&&|\|\||[;|<>`]|\$\(")

# Operator split to count distinct simple-commands (over-splitting only ever
# pushes us toward the safe `ask` side, so quoting edge-cases are fine).
_SPLIT = re.compile(r"&&|\|\||[;|&\n]")


def _decision(kind, reason):
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    })


def _is_interp(tok):
    """Is this command token a python interpreter (python/python3/$VSCODE_PYTHON/path)?"""
    if "VSCODE_PYTHON" in tok:  # $VSCODE_PYTHON or ${VSCODE_PYTHON}
        return True
    return bool(_PY_INTERP.search(tok))


def _segment_command_tokens(seg):
    """Tokens of one segment with leading VAR=val assignments stripped, or None."""
    seg = seg.strip()
    if not seg:
        return None
    try:
        parts = shlex.split(seg, posix=True)
    except ValueError:
        parts = seg.split()
    i = 0
    while i < len(parts) and re.match(r"^[A-Za-z_]\w*=", parts[i]):
        i += 1
    return parts[i:] or None


def classify(cmd):
    """Return ('allow'|'ask', reason) for a python exec, or None for no opinion."""
    segments = [s for s in _SPLIT.split(cmd) if s.strip()]
    seg_tokens = [t for t in (_segment_command_tokens(s) for s in segments) if t]

    runs_python = any(toks and _is_interp(toks[0]) for toks in seg_tokens)
    if not runs_python:
        return None  # not a python execution -> let normal rules apply

    if _COMPLEX.search(cmd) or len(seg_tokens) != 1:
        return ("ask", "Chained/compound python command — requires explicit approval.")

    toks = seg_tokens[0]
    args = toks[1:]
    if any(a in ("-c", "-m") for a in args):
        return ("ask", "Inline python (-c/-m) — generated code, requires approval.")

    script = next((a for a in args if a.endswith(".py")), None)
    if not script:
        script = next((a for a in args if not a.startswith("-")), None)
    if not script:
        return ("ask", "python with no script file — requires approval.")

    if "CLAUDE_SKILL_DIR" in script or (
        _SKILL_PATH.search(script) and "/scratchpad/" not in script and not script.startswith("/tmp/")
    ):
        return ("allow", "Skill-bundled python script (vetted maintainer artifact).")
    return ("ask", "Generated/scratch python script — requires explicit approval.")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # unparseable -> no opinion (safe: normal prompt happens)
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not cmd.strip():
        return
    try:
        verdict = classify(cmd)
    except Exception:
        return  # never crash into a blocked tool call
    if verdict:
        print(_decision(*verdict))


if __name__ == "__main__":
    main()
