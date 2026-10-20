"""Todos — the markdown checkboxes across a project's docs, as one list.

The markdown stays the source of truth: a todo IS a line in a doc, and every
mutation edits that line in place. Measured over the operator's projects: 501
open and 358 done ``- [ ]`` items spread over ``DEV_PLAN.md``, ``TASKS.md``,
``CURRENT_PHASE.md`` and friends — no separate database could keep up with
hand edits, so none is introduced.

Three states, in markdown that still renders everywhere:
``[ ]`` open · ``[x]``/``[X]`` done · ``[~]``/``[/]`` or a trailing
``*(in progress)*`` in progress.

Line numbers drift whenever a file is edited by hand, so every mutation carries
the line's **fingerprint** as read by the client. If the line at that index no
longer matches, the mutation is refused (409) instead of toggling whatever line
moved into its place — the old plans route toggled by index alone.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from navig.projects import docs as _docs
from navig.projects import textio

__all__ = [
    "TodoError",
    "add_todo",
    "edit_todo",
    "extract_todos",
    "line_fingerprint",
    "parse_todos",
    "set_state",
    "summarize",
]

CHECKBOX_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\[([ xX~/])\]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_IN_PROGRESS_MARK = re.compile(r"\*\(in progress\)\*", re.IGNORECASE)
_STATE_CHAR = {"open": " ", "done": "x", "in_progress": "~"}
_MAX_TODOS = 3000


class TodoError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def line_fingerprint(line: str) -> str:
    return hashlib.sha1(line.rstrip("\r\n").encode("utf-8")).hexdigest()[:12]


def _state(mark: str, text: str) -> str:
    if mark in ("x", "X"):
        return "done"
    if mark in ("~", "/") or _IN_PROGRESS_MARK.search(text):
        return "in_progress"
    return "open"


def parse_todos(text: str, rel: str) -> list[dict[str, Any]]:
    """Checkboxes in *text*, outside fenced code, with their heading path."""
    todos: list[dict[str, Any]] = []
    headings: list[tuple[int, str]] = []
    in_fence = False
    for idx, raw in enumerate(text.splitlines()):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        h = _HEADING_RE.match(raw)
        if h:
            level = len(h.group(1))
            headings = [x for x in headings if x[0] < level] + [(level, h.group(2).strip())]
            continue
        m = CHECKBOX_RE.match(raw)
        if not m:
            continue
        body = m.group(4).strip()
        todos.append({
            "id": f"{rel}:{idx}",
            "path": rel,
            "line": idx,
            "text": _IN_PROGRESS_MARK.sub("", body).strip(),
            "state": _state(m.group(3), body),
            "heading": " › ".join(t for _, t in headings),
            "indent": len(m.group(1).expandtabs(2)) // 2,
            "fingerprint": line_fingerprint(raw),
        })
    return todos


def extract_todos(root: Path, doc_index: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every todo across the project's markdown docs, in doc order."""
    index = doc_index if doc_index is not None else _docs.index_docs(root)
    out: list[dict[str, Any]] = []
    for d in index:
        # Archived plans are history and ideas are not commitments — neither holds todos.
        if not d["path"].lower().endswith(".md") or d.get("group") in ("archive", "ideas"):
            continue
        try:
            text = (root / d["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for t in parse_todos(text, d["path"]):
            t["doc_title"] = d.get("title", "")
            t["group"] = d.get("group", "")
            out.append(t)
            if len(out) >= _MAX_TODOS:
                return out
    return out


def summarize(todos: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"open": 0, "in_progress": 0, "done": 0}
    for t in todos:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    counts["total"] = len(todos)
    return counts


# ── Mutations (all line-ending and encoding exact) ──────────────────────────


def _load_lines(root: Path, rel: str) -> tuple[Path, list[str]]:
    target = _docs.resolve_doc(root, rel)
    if not target.is_file():
        raise TodoError("document not found", 404)
    try:
        text = textio.read_exact(target)
    except textio.NotUtf8Error as exc:
        raise TodoError(str(exc), 422) from exc
    return target, text.splitlines(keepends=True)


def _checked_line(lines: list[str], line: int, expect: str | None) -> tuple[str, str, re.Match[str]]:
    if not isinstance(line, int) or isinstance(line, bool) or line < 0 or line >= len(lines):
        raise TodoError("line is out of range — the document changed; reload", 409)
    raw = lines[line]
    body = raw.rstrip("\r\n")
    ending = raw[len(body):]
    if expect is not None and line_fingerprint(body) != expect:
        raise TodoError("that todo moved or changed since you loaded it — reload", 409)
    m = CHECKBOX_RE.match(body)
    if m is None:
        raise TodoError("that line is not a checkbox", 400)
    return body, ending, m


def _result(target: Path, root: Path, lines: list[str], line: int) -> dict[str, Any]:
    textio.write_exact(target, "".join(lines))
    rel = target.relative_to(root.resolve()).as_posix()
    todo = next((t for t in parse_todos("".join(lines), rel) if t["line"] == line), None)
    return {"todo": todo, "mtime": target.stat().st_mtime}


def set_state(root: Path, rel: str, line: int, state: str, *, expect: str | None = None) -> dict[str, Any]:
    """Set a todo to ``open`` / ``in_progress`` / ``done``."""
    if state not in _STATE_CHAR:
        raise TodoError("state must be open, in_progress or done")
    target, lines = _load_lines(root, rel)
    body, ending, m = _checked_line(lines, line, expect)
    text = m.group(4)
    if state != "in_progress":
        text = _IN_PROGRESS_MARK.sub("", text).rstrip()
    new = body[: m.start(3)] + _STATE_CHAR[state] + body[m.end(3): m.start(4)] + text
    lines[line] = new + ending
    return _result(target, root, lines, line)


def edit_todo(root: Path, rel: str, line: int, text: str, *, expect: str | None = None) -> dict[str, Any]:
    """Replace a todo's text, keeping its indent, bullet and state."""
    clean = " ".join((text or "").split())
    if not clean:
        raise TodoError("todo text cannot be empty")
    target, lines = _load_lines(root, rel)
    body, ending, m = _checked_line(lines, line, expect)
    lines[line] = body[: m.start(4)] + clean + ending
    return _result(target, root, lines, line)


def add_todo(root: Path, rel: str, text: str, *, heading: str | None = None) -> dict[str, Any]:
    """Append ``- [ ] text`` under *heading* (after its last item), or at the end."""
    clean = " ".join((text or "").split())
    if not clean:
        raise TodoError("todo text cannot be empty")
    target = _docs.resolve_doc(root, rel)
    if not rel.lower().endswith(".md"):
        raise TodoError("todos live in markdown documents")
    if target.is_file():
        try:
            content = textio.read_exact(target)
        except textio.NotUtf8Error as exc:
            raise TodoError(str(exc), 422) from exc
    else:
        content = ""
    eol = textio.dominant_eol(content) if content else "\n"
    lines = content.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += eol
    new_line = f"- [ ] {clean}{eol}"

    insert_at = len(lines)
    if heading:
        wanted = heading.strip().lower()
        start = None
        level = 0
        for i, raw in enumerate(lines):
            h = _HEADING_RE.match(raw.rstrip("\r\n"))
            if h and start is None and h.group(2).strip().lower() == wanted:
                start, level = i, len(h.group(1))
            elif h and start is not None and len(h.group(1)) <= level:
                insert_at = i
                break
        if start is None:
            lines += [eol, f"## {heading.strip()}{eol}", eol]
            insert_at = len(lines)
        else:
            # After the section's last non-blank line, so the item joins its list.
            j = insert_at
            while j - 1 > start and not lines[j - 1].strip():
                j -= 1
            insert_at = j
    lines.insert(insert_at, new_line)
    return _result(target, root, lines, insert_at)
