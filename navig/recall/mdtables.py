"""Find GitHub-flavoured Markdown tables in text and keep them verbatim.

A table is a header row ``| a | b |`` followed by a separator row ``|---|:--|`` and at least
one body row. Tables inside fenced code blocks are ignored (they are examples, not data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


@dataclass
class MdTable:
    raw: str
    columns: list[str]
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""  # nearest heading or lead-in line above the table
    start_line: int = 0

    def row_dicts(self) -> list[dict[str, str]]:
        return [dict(zip(self.columns, r + [""] * (len(self.columns) - len(r)))) for r in self.rows]


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):  # split on | that is not escaped and not inside `code`
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if ch == "`":
            j = s.find("`", i + 1)
            if j > i:
                cur.append(s[i:j + 1])
                i = j + 1
                continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def find_tables(text: str, *, min_rows: int = 1) -> list[MdTable]:
    lines = text.splitlines()
    out: list[MdTable] = []
    in_fence = False
    caption = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        h = _HEADING.match(line)
        if h:
            caption = h.group(1).strip()
        elif line.strip() and not _ROW.match(line):
            caption = line.strip()
        if _ROW.match(line) and i + 1 < len(lines) and _SEP.match(lines[i + 1]) and not _SEP.match(line):
            j = i + 2
            while j < len(lines) and _ROW.match(lines[j]) and not _FENCE.match(lines[j]):
                j += 1
            body = lines[i + 2:j]
            if len(body) >= min_rows:
                out.append(MdTable(
                    raw="\n".join(lines[i:j]), columns=split_row(line),
                    rows=[split_row(r) for r in body], caption=caption[:200], start_line=i,
                ))
            i = j
            continue
        i += 1
    return out
