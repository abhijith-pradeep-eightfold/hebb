#!/usr/bin/env python3
"""Hebb structural lint — the checker for the injector's self-correction loop.

Checks the compiled wiki + learned skills for: dangling wikilinks, orphan pages,
unresolved conflict markers, broken skill run-commands, and knowledge<->skill
link symmetry. Prints findings; exits non-zero if any are found, 0 if clean.
Run as a distinct verification pass (maker/checker separation).
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIKI = os.path.join(ROOT, "wiki")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish import parse_frontmatter, collect_skills  # noqa: E402

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
RUNCMD = re.compile(r"\$\{CLAUDE_SKILL_DIR\}/(\S+?\.py)")


def _target(link):
    # drop the |alias and any #anchor; intra-page anchors (#sec) become empty
    return link.split("|")[0].split("#")[0].strip()


def _wiki_pages():
    pages = {}
    for p in glob.glob(os.path.join(WIKI, "**", "*.md"), recursive=True):
        pages[os.path.relpath(p, WIKI)[:-3]] = p  # key: relpath without .md
    return pages


def lint():
    problems = []
    pages = _wiki_pages()
    by_key = {}
    for rel in pages:
        by_key[rel] = rel
        by_key.setdefault(os.path.basename(rel), rel)

    linked_to = set()
    for rel, path in pages.items():
        text = open(path, encoding="utf-8").read()
        for raw in WIKILINK.findall(text):
            tgt = _target(raw)
            if not tgt:
                continue  # pure intra-page anchor like [[#section]]
            resolved = by_key.get(tgt) or by_key.get(os.path.basename(tgt))
            if resolved is None:
                problems.append(f"dangling wikilink [[{raw}]] in wiki/{rel}.md")
            else:
                linked_to.add(resolved)
        if "UNRESOLVED-CONFLICT" in text:
            problems.append(f"unresolved conflict marker in wiki/{rel}.md")

    for rel in pages:
        if rel != "index" and rel not in linked_to:
            problems.append(f"orphan page wiki/{rel}.md (not linked from any page)")

    skill_dirs = {}
    for root in (os.path.join(ROOT, "core", "skills"), os.path.join(ROOT, "skills")):
        skill_dirs.update(collect_skills(root))
    skill_names = set(skill_dirs)

    skill_pages = {}  # skill -> set(resolved page key) from knowledge_* frontmatter
    for name, d in skill_dirs.items():
        md = os.path.join(d, "SKILL.md")
        body = open(md, encoding="utf-8").read()
        for script in RUNCMD.findall(body):
            if "X.py" in script or "<" in script:
                continue  # documented placeholder in an instructional skill, not a real ref
            if not os.path.exists(os.path.join(d, script)):
                problems.append(f"skill '{name}' references missing script {script}")
        meta = parse_frontmatter(md)
        decl = []
        for key in ("knowledge_required", "knowledge_optional"):
            v = meta.get(key) or []
            decl += [v] if isinstance(v, str) else v
        keys = set()
        for item in decl:
            found = WIKILINK.findall(item)
            tgt = _target(found[0]) if found else item.strip()
            resolved = by_key.get(tgt) or by_key.get(os.path.basename(tgt))
            if resolved is None:
                problems.append(f"skill '{name}' knowledge link '{item}' does not resolve to a wiki page")
            else:
                keys.add(resolved)
        skill_pages[name] = keys

    page_skills = {}  # page key -> set(skill names) from its "## Related skills" section
    for rel, path in pages.items():
        parts = open(path, encoding="utf-8").read().split("## Related skills", 1)
        page_skills[rel] = (
            {b for b in re.findall(r"`([a-z0-9][a-z0-9-]*)`", parts[1]) if b in skill_names}
            if len(parts) == 2 else set()
        )

    for name, keys in skill_pages.items():
        for key in keys:
            if name not in page_skills.get(key, set()):
                problems.append(f"asymmetry: skill '{name}' lists wiki/{key}.md but that page's "
                                f"Related skills omits `{name}`")
    for rel, names in page_skills.items():
        for name in names:
            if rel not in skill_pages.get(name, set()):
                problems.append(f"asymmetry: wiki/{rel}.md lists `{name}` but that skill's "
                                f"knowledge_* omits this page")
    return problems


def main():
    problems = lint()
    if problems:
        print(f"lint: {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("lint: clean")


if __name__ == "__main__":
    main()
