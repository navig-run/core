"""Legacy changelog parsing — deterministic, no LLM, three real-world families:

* **Session addenda** (NAVIG's archived CURRENT_PHASE): ``## ✅ Session Addendum —
  2026-10-13 (Title)`` and ``## Addendum 2026-10-10 — Title``;
* **Dated dev entries** (GRABNITE): ``## 2025-11-22: Title …``;
* **Release notes** (Keep a Changelog: anchor, dock, netwizard): ``## [Unreleased]``,
  ``## [0.1.0] - 2026-01-01`` (migrated for search, files RETAINED — release
  documentation is not private dev history).

An entry spans its ``## `` header up to the next ``## `` header (``###`` subsections
stay inside). A file with lines but no recognizable header raises — a parser that
"finds nothing" must never be the reason a migration retires a source.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Order matters: addendum shapes are more specific than the generic release header.
_H_ADDENDUM_PAREN = re.compile(r"^##\s+(?:✅\s*)?Session Addendum\s+—\s+(\d{4}-\d{2}-\d{2})\s+\((.+?)\)\s*$")
_H_ADDENDUM_DASH = re.compile(r"^##\s+Addendum\s+(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
_H_DATED = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2}):\s*(.+?)\s*$")
_H_RELEASE = re.compile(r"^##\s+\[?([^\]\n]+?)\]?(?:\s*[—-]\s*([^\n]+?))?\s*$")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_H_LEVEL2 = re.compile(r"^##\s+(?!#)")


@dataclass(frozen=True)
class Entry:
    title: str
    date: str          # YYYY-MM-DD or ""
    start: int         # 1-based header line
    end: int           # 1-based last line (inclusive)
    text: str          # header + body, verbatim
    sha: str           # sha256(text)[:16] — content-addressed, so re-import is idempotent

    @property
    def locator(self) -> str:
        import json  # noqa: PLC0415 — tiny, keeps module import-light

        return json.dumps({"lines": [self.start, self.end]})


def _header_parts(line: str) -> tuple[str, str] | None:
    """(title, date) when *line* is a level-2 entry header of a known family."""
    if not _H_LEVEL2.match(line):
        return None
    m = _H_ADDENDUM_PAREN.match(line)
    if m:
        return m.group(2).strip(), m.group(1)
    m = _H_ADDENDUM_DASH.match(line)
    if m:
        return m.group(2).strip(), m.group(1)
    m = _H_DATED.match(line)
    if m:
        return m.group(2).strip(), m.group(1)
    m = _H_RELEASE.match(line)
    if m:
        title = m.group(1).strip()
        suffix = (m.group(2) or "").strip()
        iso = _ISO.search(suffix)
        if iso:
            return title, iso.group(0)          # `## [0.1.0] - 2026-01-01 …`
        if suffix:
            # version + a NON-date label (`## [1.0.0] — Unreleased`, `## [3.38.34] —
            # AI-Proposed Enhancement`): keep the label in the title, no fake date —
            # these matched NO header family before the pilot migration (2026-10-20).
            return f"{title} — {suffix}", ""
        return title, ""
    return None


def parse(text: str) -> list[Entry]:
    """Entries in file order (newest-first files stay newest-first). Empty text → [].
    Non-empty text with no recognizable entry header → ValueError.

    Every level-2 header is a BOUNDARY: an unrecognized ``##`` section ends the entry
    before it and is itself skipped, so foreign sections never bleed into a record."""
    lines = text.splitlines()
    if not text.strip():
        return []
    bounds = [i for i, line in enumerate(lines) if _H_LEVEL2.match(line)]
    if not any(_header_parts(lines[i]) is not None for i in bounds):
        raise ValueError("no recognized entry headers (## Addendum / ## date: / ## [version])")

    entries: list[Entry] = []
    for n, i in enumerate(bounds):
        if _header_parts(lines[i]) is None:
            continue  # an unrecognized ## section: not an entry, not merged into one
        j = bounds[n + 1] if n + 1 < len(bounds) else len(lines)
        if not "\n".join(lines[i + 1:j]).strip():
            continue  # header with nothing under it (e.g. an empty `## [Unreleased]`)
        body = "\n".join(lines[i:j]).rstrip()
        title, date = _header_parts(lines[i])  # type: ignore[misc]
        entries.append(Entry(
            title=title, date=date, start=i + 1, end=j, text=body + "\n",
            sha=hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
        ))
    return entries


def record_id(project: str, filename: str, entry: Entry) -> str:
    """Content-addressed ``rk-`` id: re-importing the same file is idempotent; an edited
    entry mints a new record instead of silently rewriting history."""
    from navig.recall.model import record_id as _rk  # noqa: PLC0415

    return _rk("changelog", project, filename, entry.sha)


def classify(entries: list[Entry]) -> str:
    """``private_dev`` · ``release_notes`` · ``sample_artifact``.

    Release notes are migrated but never retired (published release documentation must
    survive). A file that SELF-DESCRIBES as a sample/proposal ("sample proposed change",
    "AI-Proposed" — the pilot case, 2026-10-20) is a ``sample_artifact``: retirable,
    and NOT protected by the release-notes rule it only resembles via semver titles.
    """
    body = " ".join(e.text for e in entries[:20]).casefold()
    if "sample proposed change" in body or "ai-proposed" in body:
        return "sample_artifact"
    names = " ".join(e.title for e in entries[:10]).lower()
    if "unreleased" in names or any(re.match(r"^\d+\.\d+\.\d+", e.title) for e in entries[:10]):
        return "release_notes"
    return "private_dev"
