#!/usr/bin/env python3
"""Publish Hebb skills and agents into .claude/ for Claude Code discovery.

Source of truth stays separated (core vs learned); runtime discovery is unified
via relative symlinks:

    core/skills/<name>/SKILL.md   (core, human-authored) ─┐
    skills/<name>/SKILL.md        (learned, maintainer)   ├─> .claude/skills/<name>
    core/agents/<name>.md         (core agent defs)       ─┐
    agents/<name>.md              (learned roles)          ├─> .claude/agents/<name>.md

Core shadows learned on a name collision (core is authoritative). Idempotent;
prunes stale symlinks. Run from anywhere; paths are resolved from this file.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # core/tools/ -> hebb/


def rel_symlink(target, linkpath):
    rel = os.path.relpath(target, os.path.dirname(linkpath))
    if os.path.islink(linkpath):
        if os.readlink(linkpath) == rel:
            return "ok"
        os.unlink(linkpath)
    elif os.path.exists(linkpath):
        return f"SKIP (real path exists, not a symlink): {linkpath}"
    os.symlink(rel, linkpath)
    return "linked"


def collect_skills(srcdir):
    out = {}
    if os.path.isdir(srcdir):
        for name in sorted(os.listdir(srcdir)):
            p = os.path.join(srcdir, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "SKILL.md")):
                out[name] = p
    return out


def collect_agents(srcdir):
    out = {}
    if os.path.isdir(srcdir):
        for name in sorted(os.listdir(srcdir)):
            if name.endswith(".md") and os.path.isfile(os.path.join(srcdir, name)):
                out[name] = os.path.join(srcdir, name)
    return out


def publish(kind, sources, runtime_dir, collector):
    os.makedirs(runtime_dir, exist_ok=True)
    published = {}
    print(f"{kind}s:")
    for label, srcdir in sources:
        for name, path in collector(srcdir).items():
            if name in published:
                print(f"  WARN collision: {kind} '{name}' in '{label}' shadowed by '{published[name][0]}'")
                continue
            published[name] = (label, path)
            print(f"  [{label}] {name}: {rel_symlink(path, os.path.join(runtime_dir, name))}")
    for name in sorted(os.listdir(runtime_dir)):
        link = os.path.join(runtime_dir, name)
        if os.path.islink(link) and name not in published:
            os.unlink(link)
            print(f"  pruned stale: {name}")
    return published


def main():
    publish("skill",
            [("core", os.path.join(ROOT, "core", "skills")),
             ("learned", os.path.join(ROOT, "skills"))],
            os.path.join(ROOT, ".claude", "skills"), collect_skills)
    publish("agent",
            [("core", os.path.join(ROOT, "core", "agents")),
             ("learned", os.path.join(ROOT, "agents"))],
            os.path.join(ROOT, ".claude", "agents"), collect_agents)


if __name__ == "__main__":
    main()
