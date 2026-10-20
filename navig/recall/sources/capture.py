"""Browser captures (Navig Capture v1) as a Recall source.

The Dock writes each capture as an inbox note with YAML frontmatter
(:mod:`navig.inbox.capture_v1`). This adapter reads those notes back:

* one source per capture, ``capture:<id>`` — the id is in the frontmatter, so a note the
  inbox router later moves keeps its identity (and what was archived stays searchable);
* every turn is spoken by ``web_capture`` — text from a web page is never attributed to the
  operator as something they typed or decided;
* a WHOLE conversation capture (``kind: chat``) is skipped: conversations belong to the
  navig-chats library, which Recall reads through its ``chats`` source with the person's
  share level (owner decision 2026-10-16 — no second ChatGPT import path);
* the project is the frontmatter ``project`` when it names a registered project, else
  ``shared`` — a capture never invents a project;
* Pass 0 then makes table records from captured Markdown tables.

:func:`ingest_file` is what the capture route calls right after writing a note, so the
capture is searchable immediately (Recall must be enabled — the route checks).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.recall.model import Turn
from navig.recall.sources.base import SourceRef

if TYPE_CHECKING:
    from navig.recall.ingest import Ingestor, IngestReport
    from navig.recall.projects import ProjectResolver

logger = logging.getLogger(__name__)

ADAPTER = "capture"
SPEAKER = "web_capture"
_HEAD_BYTES = 8192
_LOCK = threading.Lock()


def capture_roots() -> list[Path]:
    """Where capture notes land: the global inbox (``data_dir()/inbox``)."""
    from navig.platform.paths import data_dir  # noqa: PLC0415

    return [Path(data_dir()) / "inbox"]


def _read(path: Path) -> tuple[dict[str, Any] | None, str]:
    from navig.inbox.capture_v1 import read_note  # noqa: PLC0415

    return read_note(path.read_text(encoding="utf-8", errors="replace"))


def _is_capture(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return False
    return head.startswith(b"---") and b"navig_capture: 1" in head


def ref_for(path: Path) -> SourceRef | None:
    """The source a capture note is, or None when the file is not a v1 capture."""
    if not _is_capture(path):
        return None
    fm, _ = _read(path)
    if not fm or not fm.get("id"):
        return None
    if str(fm.get("kind") or "") == "chat":
        return None  # whole chats are the navig-chats library's, read via the `chats` source
    return SourceRef(source_id=f"{ADAPTER}:{fm['id']}", adapter=ADAPTER, path=str(path), meta=dict(fm))


def discover(roots: list[Path] | None = None) -> Iterator[SourceRef]:
    for root in roots if roots is not None else capture_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*--*.md")):
            ref = ref_for(path)
            if ref is not None:
                yield ref


def resolve_project(name: str, resolver: ProjectResolver) -> str:
    """The registered project *name* refers to, else ``shared``."""
    from navig.recall.projects import SHARED  # noqa: PLC0415

    name = (name or "").strip()
    if not name:
        return SHARED
    hit = resolver.by_name(name)
    if hit:
        return hit
    try:  # the Dock's picker lists /api/deck/projects (discovered spaces, not only the registry)
        from navig.projects.model import resolve_project as _resolve  # noqa: PLC0415

        _canonical, root = _resolve(name)
    except Exception:  # noqa: BLE001 — unknown project (or no project index): the shared bucket
        return SHARED
    return resolver.resolve(str(root)).id


def turns(fm: dict[str, Any], content: str) -> list[Turn]:
    """The capture as one attributed turn."""
    ts = str(fm.get("captured_at") or "")
    kind = str(fm.get("kind") or "page")
    base = {k: fm[k] for k in ("url", "title", "site", "conversation_id", "hint", "client") if fm.get(k)}
    note = str(fm.get("note") or "").strip()
    text = f"{note}\n\n{content}".strip() if note else content
    if not text:
        text = str(fm.get("title") or fm.get("url") or "")
    meta = dict(base)
    for key in ("role", "message_ids", "images"):
        if fm.get(key):
            meta[key] = fm[key]
    if note:
        meta["note"] = note
    return [Turn(msg_id="c1", speaker=SPEAKER, text=text, ts=ts, kind=kind, meta=meta)]


def ingest_ref(ing: Ingestor, ref: SourceRef, rep: IngestReport, only_project: str | None = None) -> None:
    """Read one capture note into the bank (unchanged bytes → nothing to do)."""
    from navig.inbox.capture_v1 import note_content  # noqa: PLC0415
    from navig.recall import pass0  # noqa: PLC0415
    from navig.recall.ingest import _archivable, _redacted  # noqa: PLC0415
    from navig.store.base import _utcnow  # noqa: PLC0415

    path = Path(ref.path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    src = ing.store.get_source(ref.source_id)
    if src and src["head_sha"] == digest and not src["error"]:
        rep.unchanged += 1
        return
    fm, body = _read(path)
    if fm is None:
        return
    proj = resolve_project(str(fm.get("project") or ""), ing.resolver)
    if only_project and proj != only_project:
        return
    known = ing.store.known_msg_ids(ref.source_id) if src else set()
    new = [_redacted(t) for t in turns(fm, note_content(fm, body)) if t.msg_id not in known]
    st = path.stat()
    meta = {k: v for k, v in fm.items() if k not in ("navig_capture", "title")}
    meta["requested_project"] = str(fm.get("project") or "")
    row = {
        "source_id": ref.source_id, "adapter": ADAPTER, "path": str(path), "project": proj,
        "title": str(fm.get("title") or ""), "cwd": "", "size": st.st_size, "mtime": st.st_mtime,
        "head_sha": digest, "tail_sha": "", "cursor": len(raw), "status": "ok", "error": "",
        "first_ts": str(fm.get("captured_at") or ""), "last_ts": str(fm.get("captured_at") or ""),
        "turns": int((src or {}).get("turns", 0)) + len(new), "meta": meta, "updated_at": _utcnow(),
    }
    ing.archive.append(ADAPTER, ref.source_id, new, _archivable(row))
    added = ing.store.add_turns(ref.source_id, new)
    rep.turns_added += len(added)
    ing.store.upsert_source(row)
    rep.sources_read += 1
    if added:
        ing.commit(pass0.records_from_turns(ref.source_id, proj, added), rep)


def ingest_all(ing: Ingestor, rep: IngestReport, only_project: str | None,
               progress: Callable[[int, int, str], None] | None = None,
               roots: list[Path] | None = None) -> None:
    """Every capture note under the inbox roots (one bad note never stops the rest)."""
    refs = list(discover(roots))
    for i, ref in enumerate(refs, 1):
        if progress:
            progress(i, len(refs), ref.source_id)
        rep.sources_seen += 1
        try:
            with _LOCK:
                ingest_ref(ing, ref, rep, only_project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("recall: capture %s failed: %s", ref.source_id, type(exc).__name__)
            rep.errors.append((ref.source_id, f"{type(exc).__name__}: {exc}"[:300]))
            ing._mark_error(ref, exc)


def ingest_file(path: Path, ing: Ingestor | None = None) -> dict[str, Any]:
    """Index one capture note now (the capture route's call). Returns what happened."""
    from navig.recall.ingest import Ingestor, IngestReport  # noqa: PLC0415

    ref = ref_for(Path(path))
    if ref is None:
        return {"indexed": False, "reason": "not a v1 capture"}
    ing = ing or Ingestor()
    rep = IngestReport(sources_seen=1)
    with _LOCK:
        ingest_ref(ing, ref, rep)
    src = ing.store.get_source(ref.source_id) or {}
    return {"indexed": True, "source_id": ref.source_id, "project": src.get("project", ""),
            "turns": rep.turns_added, "records": rep.records_created}
