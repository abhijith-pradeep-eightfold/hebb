#!/usr/bin/env python3
"""PreToolUse(Bash) hook: Hebb's bash execution policy.

One decision per command, two jobs:

1. Auto-allow read-only exploration — including compound / piped / `2>&1`
   commands that static allow-rules can't clear (a `>` anywhere makes the static
   matcher fall back to a prompt, even for a harmless `2>&1`). If *every*
   simple-command in the line is a known read-only tool — and there's no
   file-writing redirect, command substitution, or `find -exec`/`-delete` — we
   allow it outright.

2. Gate python by origin: a run of a vetted Hebb script — a skill-bundled
   script (under a skills/ dir or ${CLAUDE_SKILL_DIR}) or an engine tool under
   core/tools/, any nesting depth -> allow. A chained/compound command is allowed
   too **when every segment is trusted** (a vetted python script or a read-only
   tool) and there's no command substitution. A write redirect (`>`/`>>`) into
   the session scratchpad is fine — that's sanctioned scratch output; a write to
   any other real file, or an input `<` redirect, still -> ask. Generated /
   scratch python (/tmp, scratchpad, `-c`/`-m`, or any untrusted segment) -> ask.

Anything else -> no opinion (normal permission flow). On any parse trouble we
emit no decision, so the safe default (prompt) always wins. We only ever emit
`allow` for something we can fully vouch for; when in doubt we stay silent.

I/O: read the PreToolUse JSON on stdin; print a hookSpecificOutput decision on
stdout (or nothing); always exit 0.
"""
import json
import re
import shlex
import sys

_PY_INTERP = re.compile(r"(^|/)python(3(\.\d+)?)?$")
_SKILL_PATH = re.compile(r"(^|/)(\.claude/)?(core/)?skills/")
# Engine tools live here; a clean run of one is a vetted maintainer artifact.
_TOOLS_PATH = re.compile(r"(^|/)core/tools/")
# Operator split into simple-commands (over-splitting only pushes toward `ask`).
_SPLIT = re.compile(r"&&|\|\||[;|&\n]")

# Genuinely read-only inspection tools. Deliberately excludes anything that can
# write (sed/awk/tee) or run another program given as an argument
# (env/command/xargs/time/sudo/...). `find` is allowed but screened for -exec.
_READONLY = {
    "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "find", "fd",
    "tree", "cd", "pwd", "echo", "printf", "wc", "sort", "uniq", "cut", "diff",
    "file", "stat", "realpath", "dirname", "basename", "which", "type", "column",
    "nl", "date",
}
_FIND_EXEC = {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
# fd-dup and /dev/null redirects are harmless; strip them before checking writes.
_SAFE_REDIR = re.compile(r"\d*>&\d+|&?>>?\s*/dev/null|\d*>>?\s*/dev/null")
# Write redirects: `>`, `>>`, `2>`, `&>`, ... and the file they target.
_WRITE_REDIR = re.compile(r"(?:&|\d*)>>?\s*(\S+)")


def _decision(kind, reason):
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": kind,
        "permissionDecisionReason": reason,
    }})


def _is_interp(tok):
    return "VSCODE_PYTHON" in tok or bool(_PY_INTERP.search(tok))


def _seg_tokens(seg):
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


def _trusted_python(args):
    """True if a python arg list runs a vetted Hebb script (not -c/-m, not scratch)."""
    if any(a in ("-c", "-m") for a in args):
        return False
    script = next((a for a in args if a.endswith(".py")), None) \
        or next((a for a in args if not a.startswith("-")), None)
    if not script:
        return False
    return "CLAUDE_SKILL_DIR" in script or (
        (_SKILL_PATH.search(script) or _TOOLS_PATH.search(script))
        and "/scratchpad/" not in script and not script.startswith("/tmp/")
    )


def _redirects_ok(clean):
    """No input redirect, and every write redirect targets the session scratchpad.

    Harmless 2>&1 / /dev/null forms are already stripped by _SAFE_REDIR before we
    get here. Writing into the scratchpad is sanctioned (see CLAUDE.md); a write
    to any other real file, or any input `<` redirect, still requires approval.
    """
    for target in _WRITE_REDIR.findall(clean):
        if "/scratchpad/" not in target.strip("'\""):
            return False
    # Anything still bearing a redirect operator we didn't clear (e.g. input `<`).
    leftover = _WRITE_REDIR.sub(" ", clean)
    return "<" not in leftover and ">" not in leftover


def _python_verdict(redir_ok, seg_tokens):
    # A real file redirect (after harmless 2>&1 / /dev/null stripping) is unsafe
    # unless it writes into the session scratchpad; command substitution was
    # already screened out in classify().
    if not redir_ok:
        return ("ask", "python command with a file redirect outside scratchpad — requires approval.")
    # Allow even a chain, as long as EVERY segment is trusted: a vetted python
    # script, or a read-only inspection tool. One untrusted segment -> ask.
    for toks in seg_tokens:
        if _is_interp(toks[0]):
            if not _trusted_python(toks[1:]):
                return ("ask", "Untrusted/scratch python in the command — requires approval.")
        else:
            name = toks[0].rsplit("/", 1)[-1]
            if name not in _READONLY or (name == "find" and any(a in _FIND_EXEC for a in toks)):
                return ("ask", "A non-trusted command is chained with python — requires approval.")
    return ("allow", "All chained commands are vetted Hebb scripts / read-only tools.")


def _readonly_ok(redir_ok, seg_tokens):
    # A surviving redirect must be a write into the scratchpad (redir_ok), else
    # it's a real file write (or an input redirect) and we bail.
    if not redir_ok:
        return False
    for toks in seg_tokens:
        name = toks[0].rsplit("/", 1)[-1]
        if name not in _READONLY:
            return False
        if name == "find" and any(a in _FIND_EXEC for a in toks):
            return False
    return True


def classify(cmd):
    """('allow'|'ask', reason) for a command we have a policy on, else None."""
    if "`" in cmd or "$(" in cmd:
        return None  # command substitution could run anything -> stay silent
    # Strip harmless 2>&1 / >/dev/null FIRST, so the bare `&` in `2>&1` is never
    # mistaken for a command separator when we split into simple-commands.
    clean = _SAFE_REDIR.sub(" ", cmd)
    # Settle redirect safety on the full line, then strip write-redirects so their
    # target paths can't contaminate command tokenization (e.g. `script.py>>path`).
    redir_ok = _redirects_ok(clean)
    stripped = _WRITE_REDIR.sub(" ", clean)
    segments = [s for s in _SPLIT.split(stripped) if s.strip()]
    seg_tokens = [t for t in (_seg_tokens(s) for s in segments) if t]
    if not seg_tokens:
        return None
    if any(_is_interp(toks[0]) for toks in seg_tokens):
        return _python_verdict(redir_ok, seg_tokens)
    if _readonly_ok(redir_ok, seg_tokens):
        return ("allow", "Read-only exploration command(s).")
    return None


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
