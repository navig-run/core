"""The chat library (navig-chats) as a Recall source — only what the person chose to share.

A chat reaches Recall only when its sharing level (on the chat, or inherited from its NAVIG
folder) is ``local`` or ``agent``; the default is ``off``. Withdrawing the share makes the next
ingest FORGET it: its turns, its archive segment and every record only it supported.

* One source per chat: ``chats:<provider>:<native id>``; ``msg_id`` is the provider's message
  id, so every citation opens the exact message (``<chat url>#navig-msg=<msg_id>``).
* The project is the folder's ``brain_project`` (a NAVIG project/space id) or ``chats`` — a
  chat never lands in another space's project unless the person mapped its folder there.
* Records are the chat library's own deterministic extraction (:mod:`navig_chats.extract`):
  decisions the person stated or marked (current / superseded across chats), open tasks and
  questions, code the person kept, and the assistant's proposals — never promoted to decisions.
* ``may_leave_machine`` is the rule for anything that would send this text to a model:
  only ``agent``-level chats may. Recall itself calls no model.

The plugin is optional: without it there are simply no chat sources.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from navig.recall.model import Evidence, Record, Turn, canonical_key, record_id
from navig.recall.sources.base import SourceRef

logger = logging.getLogger(__name__)

ADAPTER = "chats"
DEFAULT_PROJECT = "chats"


def _plugin():
    try:
        from navig_chats import extract  # noqa: PLC0415
        from navig_chats.deck_routes import get_store  # noqa: PLC0415
        from navig_chats.store import brain_shared  # noqa: PLC0415
    except ImportError:
        return None
    return get_store, brain_shared, extract


def available() -> bool:
    return _plugin() is not None


def source_id_for(conversation_id: str) -> str:
    return f"{ADAPTER}:{conversation_id}"


def may_leave_machine(source_meta: dict[str, Any] | None) -> bool:
    """Whether a chat source's text may be sent to an AI provider (agent-level shares only)."""
    return (source_meta or {}).get("brain_level") == "agent"


def discover() -> Iterator[SourceRef]:
    """Every shared chat, newest revision first in its meta. Nothing when the plugin is absent."""
    plug = _plugin()
    if plug is None:
        return
    get_store, brain_shared, _ = plug
    store = get_store()
    for conv in brain_shared(store):
        yield SourceRef(
            source_id=source_id_for(conv["id"]), adapter=ADAPTER, path=str(store.db_path),
            meta={
                "conversation_id": conv["id"], "title": conv["display_title"], "url": conv.get("url") or "",
                "provider": conv["provider"], "revision": int(conv.get("revision") or 0),
                "completeness": conv["completeness"], "brain_level": conv["brain_level"],
                "project": conv.get("brain_project") or DEFAULT_PROJECT, "updated_at": conv.get("updated_at") or "",
            },
        )


def fingerprint(ref: SourceRef) -> str:
    """What makes a chat source 'changed': its content revision, its level and its project."""
    m = ref.meta
    return f"r{m['revision']}:{m['brain_level']}:{m['project']}"


def load(ref: SourceRef) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plug = _plugin()
    if plug is None:
        raise RuntimeError("the chat library plugin (navig-chats) is not installed")
    store = plug[0]()
    conv = store.get(ref.meta["conversation_id"])
    if conv is None:
        raise LookupError(ref.meta["conversation_id"])
    return conv, store.messages(conv["id"])


def turns(ref: SourceRef, conv: dict[str, Any], messages: list[dict[str, Any]]) -> list[Turn]:
    url = conv.get("url") or ""
    out = []
    for m in messages:
        speaker = "user_typed" if m["role"] == "user" else "assistant"
        out.append(Turn(
            msg_id=m["id"], speaker=speaker, text=m["text"], ts=m.get("created_at") or "",
            meta={"provider": conv["provider"], "role": m["role"],
                  "link": f"{url}#navig-msg={m['id']}" if url else ""},
        ))
    return out


def extract_items(convs: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[Any]:
    """Items for every given chat, resolved TOGETHER so a later chat can supersede an earlier one."""
    plug = _plugin()
    if plug is None:
        return []
    ex = plug[2]
    items = []
    for conv, msgs in convs:
        items += ex.extract(conv, msgs, conv.get("bookmarks"))
    return ex.resolve(items)


_STATUS = {
    ("decision", "current"): "confirmed",
    ("decision", "superseded"): "superseded",
    ("decision", "proposed"): "proposed",
    ("task", "open"): "unresolved",
    ("task", "done"): "implemented",
    ("question", "open"): "unresolved",
}


def records(items: list[Any], refs: dict[str, SourceRef]) -> tuple[list[Record], list[tuple[str, str, str]]]:
    """Recall records (+ ``superseded_by`` links) for extracted items of SHARED chats only."""
    out: list[Record] = []
    by_cite: dict[str, str] = {}
    pending_links: list[tuple[str, str]] = []
    for it in items:
        if it.kind in ("link", "highlight") or (it.kind == "snippet" and it.basis != "marked"):
            continue  # code and links stay searchable as turns; only what was kept becomes a record
        if it.kind == "decision" and it.status == "restated":
            continue
        status = "confirmed" if it.kind == "snippet" else _STATUS.get((it.kind, it.status))
        if status is None:
            continue
        ref = refs.get(source_id_for(it.conversation_id))
        if ref is None:
            continue  # extracted from a chat that is not shared: never recorded
        sid = ref.source_id
        user_basis = it.basis in ("user", "marked")
        if it.kind == "task" and it.basis == "assistant" and it.status == "open":
            status = "proposed"
        title = (f"{it.subject}: " if it.subject else "") + (it.note or it.text if it.kind == "snippet" else it.text)
        rid = record_id(sid, str(it.message_id), it.kind, canonical_key(it.subject or it.text)[:60])
        link = f"{ref.meta['url']}#navig-msg={it.message_id}" if ref.meta.get("url") and it.message_id else ""
        rec = Record(
            id=rid, type=it.kind, title=" ".join(title.split())[:160],
            summary=f"{'You' if user_basis else 'The assistant'} · {ref.meta['title']}",
            body=f"```{it.lang}\n{it.text}\n```" if it.kind == "snippet" else it.text,
            project=ref.meta["project"], status=status,
            status_basis="user" if user_basis else "assistant",
            endorsement="user_endorsed" if user_basis and it.kind == "decision" else "none",
            confidence=0.95 if it.basis == "marked" else 0.85 if user_basis else 0.6,
            canonical_key=canonical_key(it.subject or it.text),
            tags=["chat", ref.meta["provider"]] + (["marked"] if it.basis == "marked" else []),
            created=it.ts or "", updated=it.ts or "",
            attrs={"link": link, "conversation_id": it.conversation_id, "subject": it.subject,
                   "basis": it.basis, "brain_level": ref.meta["brain_level"]},
            evidence=[Evidence(sid, str(it.message_id), "user_typed" if it.role == "user" else "assistant",
                               it.text[:600], it.ts or "")],
        )
        out.append(rec)
        by_cite[f"{it.conversation_id}#{it.message_id}#{it.kind}#{rec.canonical_key[:60]}"] = rid
        if it.superseded_by:
            pending_links.append((rid, it.superseded_by))
    links: list[tuple[str, str, str]] = []
    cur_by_msg = {k.rsplit("#", 2)[0]: v for k, v in by_cite.items() if "#decision#" in k}
    for old, newer_cite in pending_links:
        new_rid = cur_by_msg.get(newer_cite)
        if new_rid and new_rid != old:
            links.append((old, new_rid, "superseded_by"))
    return out, links


__all__ = ["ADAPTER", "available", "discover", "extract_items", "fingerprint", "load", "may_leave_machine",
           "records", "source_id_for", "turns"]
