"""Web notes (navig-notes, synced from the NAVIG Dock) as a Recall source.

A note reaches Recall only when the Dock synced it — the Library's "NAVIG on this computer"
setting is ``local`` (indexed here, never sent anywhere) or ``agent`` (may also go into agent
context). A note deleted in the browser is a tombstone in the store and leaves Recall at the
next ingest.

* One source per note: ``notes:<id>``. The note's own words are ``user_typed``; lines quoted
  from the page (``> …``, what "Create note from selection" writes) are ``web_capture`` — the
  person kept them, they did not say them.
* Records: the note's checkboxes. ``- [ ] x`` is an open task, ``- [x] x`` a done one — both
  the person's own (``status_basis = user``). Everything else stays searchable as turns.
* Every citation links back to the page the note lives on.
* ``may_leave_machine``: only ``agent``-level notes. Recall itself calls no model.

The plugin is optional: without it there are simply no note sources.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from navig.recall.model import Evidence, Record, Turn, canonical_key, record_id
from navig.recall.sources.base import SourceRef

ADAPTER = "notes"
DEFAULT_PROJECT = "notes"

_TASK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")


def _plugin():
    try:
        from navig_notes.deck_routes import get_store  # noqa: PLC0415
        from navig_notes.store import display_title  # noqa: PLC0415
    except ImportError:
        return None
    return get_store, display_title


def available() -> bool:
    return _plugin() is not None


def source_id_for(note_id: str) -> str:
    return f"{ADAPTER}:{note_id}"


def may_leave_machine(source_meta: dict[str, Any] | None) -> bool:
    """Whether a note's text may be sent to an AI provider (agent-level shares only)."""
    return (source_meta or {}).get("brain_level") == "agent"


def _iso(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def discover() -> Iterator[SourceRef]:
    """Every synced, live note. Nothing when the plugin is absent."""
    plug = _plugin()
    if plug is None:
        return
    get_store, display_title = plug
    store = get_store()
    for n in store.shared("local"):
        yield SourceRef(
            source_id=source_id_for(n["id"]), adapter=ADAPTER, path=str(store.db_path),
            meta={
                "note_id": n["id"], "title": display_title(n), "url": n.get("sourceUrl") or "",
                "scope": n.get("scope"), "site": n.get("siteKey") or "", "revision": int(n.get("rev") or 1),
                "brain_level": n.get("share") or "local", "project": DEFAULT_PROJECT,
                "updated_at": _iso(n.get("updatedAt")),
            },
        )


def fingerprint(ref: SourceRef) -> str:
    """What makes a note source 'changed': its revision and its sharing level."""
    m = ref.meta
    return f"r{m['revision']}:{m['brain_level']}"


def load(ref: SourceRef) -> dict[str, Any]:
    plug = _plugin()
    if plug is None:
        raise RuntimeError("the notes plugin (navig-notes) is not installed")
    note = plug[0]().get(ref.meta["note_id"])
    if note is None:
        raise LookupError(ref.meta["note_id"])
    return note


def turns(ref: SourceRef, note: dict[str, Any]) -> list[Turn]:
    """The note as turns: the person's own words, and what they quoted from the page."""
    ts = _iso(note.get("updatedAt"))
    meta = {"link": note.get("sourceUrl") or "", "scope": note.get("scope"), "site": note.get("siteKey") or ""}
    own: list[str] = []
    quoted: list[str] = []
    for line in (note.get("body") or "").splitlines():
        (quoted if line.lstrip().startswith(">") else own).append(line.lstrip()[1:].lstrip() if line.lstrip().startswith(">") else line)
    out: list[Turn] = []
    title = (note.get("title") or "").strip()
    text = "\n".join(([title] if title else []) + own).strip()
    if text:
        out.append(Turn(msg_id=f"{note['id']}:note", speaker="user_typed", text=text, ts=ts, kind="doc", meta=meta))
    q = "\n".join(quoted).strip()
    if q:
        out.append(Turn(msg_id=f"{note['id']}:quote", speaker="web_capture", text=q, ts=ts, kind="doc", meta=meta))
    return out


def records(ref: SourceRef, note: dict[str, Any]) -> list[Record]:
    """The note's checkboxes, as the person's own open / done tasks."""
    out: list[Record] = []
    sid = ref.source_id
    ts = _iso(note.get("updatedAt"))
    for i, line in enumerate((note.get("body") or "").splitlines()):
        m = _TASK.match(line)
        if not m:
            continue
        done = m.group(1).lower() == "x"
        text = " ".join(m.group(2).split())[:300]
        key = canonical_key(text)
        out.append(Record(
            id=record_id(sid, str(i), "task", key[:60]), type="task", title=text[:160],
            summary=f"You · note “{ref.meta['title']}”" + (f" on {ref.meta['site']}" if ref.meta.get("site") else ""),
            body=text, project=ref.meta["project"], status="implemented" if done else "unresolved",
            status_basis="user", endorsement="none", confidence=0.9, canonical_key=key,
            tags=["note", "web"] + [f"#{t}" for t in note.get("tags") or []][:5],
            created=_iso(note.get("createdAt")), updated=ts,
            attrs={"link": ref.meta.get("url") or "", "note_id": note["id"], "brain_level": ref.meta["brain_level"]},
            evidence=[Evidence(sid, f"{note['id']}:note", "user_typed", line.strip()[:600], ts)],
        ))
    return out


__all__ = ["ADAPTER", "available", "discover", "fingerprint", "load", "may_leave_machine", "records", "source_id_for",
           "turns"]
