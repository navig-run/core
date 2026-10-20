"""Ideas — what a project might do, before anyone has planned it.

Plans say what WILL happen and todos what must be done; ideas are the step
before both: captured in a second, explored, parked, then promoted into a plan
or dropped. They live in the project, as markdown, in ``IDEAS.md`` next to the
other plan docs — readable by agents and git, editable by hand::

    # Ideas

    ## Inbox
    - Dark mode for the storefront #ux
      - brief: ideas/dark-mode-for-the-storefront.md

    ## Exploring
    ## Parked
    ## Promoted
    - Seller payout export → .navig/plans/TASKS.md
    ## Dropped

An idea is a top-level bullet under a stage heading; indented lines below it
are its notes. Moving an idea moves the whole block. Nothing is ever deleted —
"Dropped" is a stage. Every mutation carries the bullet's fingerprint, so an
idea that moved since it was loaded is refused (409), same as todos.

Briefs written by the AI analyst go to ``ideas/<slug>.md`` beside IDEAS.md, and
any existing ``ideas/`` folder (some projects already keep one) is listed as
idea documents.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from navig.projects import docs as _docs
from navig.projects import textio
from navig.projects.todos import _HEADING_RE, TodoError, line_fingerprint

__all__ = [
    "STAGES",
    "add_idea",
    "adopt_plan",
    "edit_idea",
    "ideas_path",
    "list_ideas",
    "move_idea",
    "promote_idea",
    "save_brief",
]

#: Stage id → heading written into IDEAS.md, in board order.
STAGES: dict[str, str] = {
    "inbox": "Inbox",
    "exploring": "Exploring",
    "parked": "Parked",
    "promoted": "Promoted",
    "dropped": "Dropped",
}
#: Headings people already use for the same stages.
_ALIASES: dict[str, str] = {
    "inbox": "inbox", "new": "inbox", "ideas": "inbox", "backlog": "inbox", "raw": "inbox",
    "exploring": "exploring", "exploration": "exploring", "considering": "exploring",
    "researching": "exploring", "in progress": "exploring",
    "parked": "parked", "later": "parked", "someday": "parked", "maybe": "parked",
    "promoted": "promoted", "planned": "promoted", "done": "promoted", "shipped": "promoted",
    "dropped": "dropped", "rejected": "dropped", "discarded": "dropped", "no": "dropped",
}
_BULLET = re.compile(r"^([-*+])\s+(?:\[[ xX~/]\]\s+)?(.+?)\s*$")
_TAG = re.compile(r"(?<![\w/])#([A-Za-z][\w-]{0,30})")
_BRIEF = re.compile(r"^\s+[-*+]\s+brief:\s*(\S+)\s*$", re.IGNORECASE)
_PROMOTED_TO = re.compile(r"\s+→\s+(\S+)\s*$")
_TEMPLATE = "# Ideas\n\nWhat this project might do — not planned yet. Promote one when it is ready.\n"


class IdeaError(TodoError):
    """Same shape (message + HTTP status) as todo errors, so routes map both alike."""


def ideas_path(root: Path) -> str:
    """Project-relative path of IDEAS.md — beside the project's plan docs."""
    from navig.spaces.plan_files import plans_dir  # noqa: PLC0415

    folder = plans_dir(Path(root))
    try:
        rel = folder.relative_to(Path(root)).as_posix()
    except ValueError:
        rel = ".navig/plans"
    return f"{rel}/IDEAS.md" if rel not in ("", ".") else "IDEAS.md"


def _stage_of(heading: str) -> str | None:
    key = re.sub(r"[^a-z ]+", "", heading.lower()).strip()
    return _ALIASES.get(key)


def _read(root: Path, rel: str) -> tuple[Path, str]:
    target = _docs.resolve_doc(root, rel)
    if not target.is_file():
        return target, ""
    try:
        return target, textio.read_exact(target)
    except textio.NotUtf8Error as exc:
        raise IdeaError(str(exc), 422) from exc


def _parse(text: str, rel: str) -> list[dict[str, Any]]:
    ideas: list[dict[str, Any]] = []
    stage: str | None = None
    current: dict[str, Any] | None = None
    in_fence = False
    for idx, raw in enumerate(text.splitlines()):
        line = raw.rstrip("\r\n")
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            current = None
            continue
        if in_fence:
            continue
        h = _HEADING_RE.match(line)
        if h:
            current = None
            if len(h.group(1)) >= 2:
                stage = _stage_of(h.group(2)) or "inbox"
            continue
        if stage is None:
            continue  # bullets above the first stage heading are the file's intro
        m = _BULLET.match(line)
        if m and not line[:1].isspace():
            text_ = m.group(2)
            promoted = _PROMOTED_TO.search(text_)
            current = {
                "id": f"{rel}:{idx}",
                "path": rel,
                "line": idx,
                "text": _PROMOTED_TO.sub("", text_).strip(),
                "stage": stage,
                "tags": _TAG.findall(text_),
                "notes": [],
                "brief": None,
                "promoted_to": promoted.group(1) if promoted else None,
                "fingerprint": line_fingerprint(line),
            }
            ideas.append(current)
            continue
        if current is not None and line.strip() and line[:1].isspace():
            b = _BRIEF.match(line)
            if b:
                current["brief"] = b.group(1)
            else:
                current["notes"].append(line.strip().lstrip("-*+ ").strip())
            continue
        if line.strip():
            current = None
    return ideas


def _brief_docs(root: Path, rel: str) -> list[dict[str, Any]]:
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return [d for d in _docs.index_docs(root)
            if d["group"] == "ideas" and d["path"] != rel
            and (not folder or d["path"].startswith(folder) or "/ideas/" in f"/{d['path']}")]


def list_ideas(root: Path) -> dict[str, Any]:
    rel = ideas_path(root)
    _target, text = _read(root, rel)
    ideas = _parse(text, rel)
    counts = dict.fromkeys(STAGES, 0)
    for i in ideas:
        counts[i["stage"]] += 1
    return {"path": rel, "exists": bool(text), "ideas": ideas, "counts": counts,
            "stages": [{"id": k, "title": v} for k, v in STAGES.items()],
            "docs": _brief_docs(root, rel)}


# ── mutations ───────────────────────────────────────────────────────────────


def _block_end(lines: list[str], start: int) -> int:
    """Index after the idea at *start* and its indented notes."""
    j = start + 1
    while j < len(lines) and lines[j].strip() and lines[j][:1].isspace():
        j += 1
    return j


def _section_insert_at(lines: list[str], stage: str, eol: str) -> tuple[list[str], int]:
    """Where a new idea goes in *stage* (end of its list), creating the heading if needed."""
    start = None
    for i, raw in enumerate(lines):
        h = _HEADING_RE.match(raw.rstrip("\r\n"))
        if h and len(h.group(1)) >= 2 and _stage_of(h.group(2)) == stage:
            start = i
            break
    if start is None:
        # Keep board order: put the new section before the first LATER stage present.
        order = list(STAGES)
        insert_before = len(lines)
        for i, raw in enumerate(lines):
            h = _HEADING_RE.match(raw.rstrip("\r\n"))
            st = _stage_of(h.group(2)) if h and len(h.group(1)) >= 2 else None
            if st and order.index(st) > order.index(stage):
                insert_before = i
                break
        block = [f"## {STAGES[stage]}{eol}", eol]
        if insert_before > 0 and lines[insert_before - 1].strip():
            block = [eol, *block]
        lines[insert_before:insert_before] = block
        return lines, insert_before + len(block)  # after "## Stage" + its blank line
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _HEADING_RE.match(lines[i].rstrip("\r\n")):
            end = i
            break
    j = end
    while j - 1 > start and not lines[j - 1].strip():
        j -= 1
    if j == start + 1:  # empty section: leave one blank line under the heading
        if j < len(lines) and not lines[j].strip():
            return lines, j + 1
        lines.insert(j, eol)
        return lines, j + 1
    return lines, j


def _load_lines(root: Path) -> tuple[Path, str, list[str], str]:
    rel = ideas_path(root)
    target, text = _read(root, rel)
    if not text:
        text = _TEMPLATE
    eol = textio.dominant_eol(text) if text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += eol
    return target, rel, lines, eol


def _checked(lines: list[str], line: int, expect: str | None) -> str:
    if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < len(lines):
        raise IdeaError("that idea moved — reload", 409)
    body = lines[line].rstrip("\r\n")
    if expect is not None and line_fingerprint(body) != expect:
        raise IdeaError("that idea moved or changed since you loaded it — reload", 409)
    if not _BULLET.match(body) or body[:1].isspace():
        raise IdeaError("that line is not an idea", 400)
    return body


def _write(target: Path, root: Path, lines: list[str], at: int) -> dict[str, Any]:
    textio.write_exact(target, "".join(lines))
    rel = target.relative_to(root.resolve()).as_posix()
    idea = next((i for i in _parse("".join(lines), rel) if i["line"] == at), None)
    return {"idea": idea}


def _clean(text: str) -> str:
    out = " ".join((text or "").split())
    if not out:
        raise IdeaError("an idea needs some text")
    if len(out) > 300:
        raise IdeaError("keep the idea to one line — put the detail in its notes or a brief")
    return out


def add_idea(root: Path, text: str, *, stage: str = "inbox", notes: list[str] | None = None) -> dict[str, Any]:
    if stage not in STAGES:
        raise IdeaError("unknown stage")
    clean = _clean(text)
    root = Path(root)
    target, _rel, lines, eol = _load_lines(root)
    lines, at = _section_insert_at(lines, stage, eol)
    block = [f"- {clean}{eol}"] + [f"  - {' '.join(n.split())}{eol}" for n in (notes or []) if n.strip()]
    lines[at:at] = block
    return _write(target, root, lines, at)


def edit_idea(root: Path, line: int, text: str, *, expect: str | None = None) -> dict[str, Any]:
    clean = _clean(text)
    root = Path(root)
    target, _rel, lines, _eol = _load_lines(root)
    body = _checked(lines, line, expect)
    raw = lines[line]
    ending = raw[len(raw.rstrip("\r\n")):]
    promoted = _PROMOTED_TO.search(body)
    lines[line] = f"{body[0]} {clean}{promoted.group(0) if promoted else ''}{ending}"
    return _write(target, root, lines, line)


def move_idea(root: Path, line: int, stage: str, *, expect: str | None = None, suffix: str = "") -> dict[str, Any]:
    """Move an idea (with its notes) to the end of *stage*."""
    if stage not in STAGES:
        raise IdeaError("unknown stage")
    root = Path(root)
    target, _rel, lines, eol = _load_lines(root)
    _checked(lines, line, expect)
    end = _block_end(lines, line)
    block = lines[line:end]
    if suffix:
        first = block[0].rstrip("\r\n")
        first = _PROMOTED_TO.sub("", first)
        block[0] = f"{first}{suffix}{block[0][len(block[0].rstrip(chr(13) + chr(10))):] or eol}"
    del lines[line:end]
    lines, at = _section_insert_at(lines, stage, eol)
    lines[at:at] = block
    return _write(target, root, lines, at)


def promote_idea(root: Path, line: int, into: str, *, heading: str | None = None,
                 expect: str | None = None) -> dict[str, Any]:
    """Turn an idea into a todo in *into*, then file it under Promoted with a pointer."""
    from navig.projects.todos import add_todo  # noqa: PLC0415

    root = Path(root)
    _target, _rel, lines, _eol = _load_lines(root)
    body = _checked(lines, line, expect)
    text = _PROMOTED_TO.sub("", _BULLET.match(body).group(2)).strip()
    todo = add_todo(root, into, text, heading=heading)
    moved = move_idea(root, line, "promoted", expect=line_fingerprint(body), suffix=f" → {into}")
    return {"idea": moved["idea"], "todo": todo["todo"]}


def adopt_plan(root: Path, markdown: str, *, title: str = "", line: int | None = None,
               expect: str | None = None) -> dict[str, Any]:
    """Save an accepted plan as a new plan doc; file its idea (if any) under Promoted.

    The name follows the project's own convention: a project whose plans are numbered
    (``00-MASTER-ROADMAP.md``, ``07-backlog.md`` …) gets the next number, others a slug.
    """
    from navig.spaces.plan_files import plans_dir  # noqa: PLC0415

    root = Path(root)
    body = (markdown or "").strip()
    if not body:
        raise IdeaError("the plan is empty")
    if not title:
        first = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        title = first or "plan"
    folder = plans_dir(root)
    try:
        rel_folder = folder.relative_to(root).as_posix()
    except ValueError:
        rel_folder = ".navig/plans"
    numbers = []
    try:
        numbers = [int(m.group(1)) for p in folder.glob("*.md")
                   if (m := re.match(r"^(\d{1,3})[-_ ]", p.name))]
    except OSError:
        pass
    name = _slug(title)
    if numbers:
        name = f"{max(numbers) + 1:02d}-{name}"
    rel = f"{rel_folder}/{name}.md" if rel_folder not in ("", ".") else f"{name}.md"
    n = 2
    while _docs.resolve_doc(root, rel).exists():
        rel = rel[: -3].rsplit("-v", 1)[0] + f"-v{n}.md"
        n += 1
    textio.write_exact(_docs.resolve_doc(root, rel), body + "\n")
    out: dict[str, Any] = {"path": rel, "idea": None}
    if line is not None:
        out["idea"] = move_idea(root, line, "promoted", expect=expect, suffix=f" → {rel}")["idea"]
    return out


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60].rstrip("-") or "idea"


def save_brief(root: Path, line: int, markdown: str, *, expect: str | None = None) -> dict[str, Any]:
    """Write an idea's brief to ``ideas/<slug>.md`` and link it from the idea."""
    root = Path(root)
    target, rel, lines, eol = _load_lines(root)
    body = _checked(lines, line, expect)
    text = _PROMOTED_TO.sub("", _BULLET.match(body).group(2)).strip()
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    base = f"{folder}/ideas" if folder else "ideas"
    name = _slug(_TAG.sub("", text))
    brief_rel = f"{base}/{name}.md"
    n = 2
    while _docs.resolve_doc(root, brief_rel).exists():
        brief_rel = f"{base}/{name}-{n}.md"
        n += 1
    header = f"# {text}\n\n_Idea brief · {date.today().isoformat()}_\n\n"
    textio.write_exact(_docs.resolve_doc(root, brief_rel), header + markdown.strip() + "\n")
    end = _block_end(lines, line)
    link = f"  - brief: {brief_rel[len(folder) + 1:] if folder else brief_rel}{eol}"
    lines[end:end] = [link]
    out = _write(target, root, lines, line)
    out["brief_path"] = brief_rel
    return out
