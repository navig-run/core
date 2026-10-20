"""Sessions — past AI work on a project, as raw material for the Idea Fabric.

Work sessions mention ideas in passing ("later we could…", "out of scope", "follow-up")
and they get lost. Two local sources hold them:

* **Claude Code plan files** (``<claude home>/plans/*.md``). They are ONE shared
  folder for every project on the machine — measured here: 229 plans, 9 of them about
  Schema — so a plan belongs to a project only when it names the project's folder.
* **Claude Code memory notes** for the project (``<claude home>/projects/<slug>/memory``),
  where ``<slug>`` is the project's path with separators and the drive colon replaced
  by ``-``. The folder also holds session transcripts; those are only COUNTED here,
  never read (they are large and private, and the plans/memory are the distilled part).

Everything is read locally and only when asked; ``CLAUDE_CONFIG_DIR`` relocates the
Claude home, as it does for Claude Code itself.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["claude_home", "find_sessions", "read_source"]

_MAX_PLANS = 600
_MAX_LISTED = 150
_HEAD_BYTES = 64 * 1024
_MAX_SOURCE = 256 * 1024


def claude_home() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(env) if env else Path.home() / ".claude"


def _slug(root: Path) -> str:
    return re.sub(r"[:\\/]", "-", str(root))


def _project_dirs(root: Path) -> list[Path]:
    """Claude Code's per-project folders for *root* (case-insensitive match)."""
    base = claude_home() / "projects"
    want = _slug(root).lower()
    try:
        return [d for d in base.iterdir() if d.is_dir() and d.name.lower() == want]
    except OSError:
        return []


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines()[:20]:
        if line.startswith("# "):
            return line[2:].strip()[:160]
    return fallback


def _mentions(text: str, root: Path) -> bool:
    """The plan names this project's folder (either separator, any case)."""
    s = str(root).lower()
    variants = {s, s.replace("\\", "/"), s.replace("/", "\\"), s.replace("\\", "\\\\")}
    low = text.lower()
    return any(v and v in low for v in variants)


def find_sessions(root: Path) -> dict[str, Any]:
    """Session plans and memory notes about *root*, newest first."""
    root = Path(root).resolve()
    items: list[dict[str, Any]] = []
    plans_dir = claude_home() / "plans"
    try:
        plans = sorted(plans_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:_MAX_PLANS]
    except OSError:
        plans = []
    for p in plans:
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(_HEAD_BYTES)
        except OSError:
            continue
        if _mentions(head, root):
            items.append({"kind": "plan", "id": f"plan:{p.name}", "title": _title(head, p.stem),
                          "name": p.name, "mtime": p.stat().st_mtime, "size": p.stat().st_size})
    transcripts = 0
    for d in _project_dirs(root):
        try:
            transcripts += sum(1 for _ in d.glob("*.jsonl"))
        except OSError:
            pass
        mem = d / "memory"
        if mem.is_dir():
            for m in sorted(mem.glob("*.md")):
                if m.name.upper() == "MEMORY.MD":
                    continue  # the index, not a note
                try:
                    head = m.read_text(encoding="utf-8", errors="replace")[:4096]
                    st = m.stat()
                except OSError:
                    continue
                items.append({"kind": "memory", "id": f"memory:{d.name}/{m.name}",
                              "title": _title(head, m.stem.replace("-", " ")), "name": m.name,
                              "mtime": st.st_mtime, "size": st.st_size})
    items.sort(key=lambda x: -x["mtime"])
    # Schema alone has ~370 memory notes; the newest are what a harvest wants.
    return {"sources": items[:_MAX_LISTED], "total": len(items), "transcripts": transcripts,
            "claude_home": str(claude_home())}


def read_source(root: Path, source_id: str) -> tuple[str, str]:
    """(label, text) of one listed source. Only ids :func:`find_sessions` lists resolve."""
    root = Path(root).resolve()
    kind, _, ref = (source_id or "").partition(":")
    if kind == "plan":
        name = Path(ref).name
        path = claude_home() / "plans" / name
        if name != ref or not path.is_file():
            raise FileNotFoundError(source_id)
        text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_SOURCE]
        if not _mentions(text[:_HEAD_BYTES], root):
            raise PermissionError("that session plan is not about this project")
        return f"session plan {name}", text
    if kind == "memory":
        folder, _, name = ref.partition("/")
        allowed = {d.name: d for d in _project_dirs(root)}
        if folder not in allowed or Path(name).name != name:
            raise PermissionError("that memory note is not this project's")
        path = allowed[folder] / "memory" / name
        if not path.is_file():
            raise FileNotFoundError(source_id)
        return f"memory note {name}", path.read_text(encoding="utf-8", errors="replace")[:_MAX_SOURCE]
    raise FileNotFoundError(source_id)
