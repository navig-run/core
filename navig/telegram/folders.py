"""Chat-folder (dialog filter) management over MTProto.

Telegram "folders" are *dialog filters* on the owner's account. This module reads
the current folders and applies a desired folder layout from a plain plan, so the
owner can redesign their whole folder system declaratively:

    folders = list_folders()                      # current state, decoded
    apply_plan(plan, confirm=False)               # dry-run diff (default)
    apply_plan(plan, confirm=True)                # actually write

A *plan* is a list of folder dicts::

    [
      {"id": 2, "title": "Projects", "emoticon": "⭐",
       "include": [123, "@somechan", "https://t.me/x"],
       "exclude": [],
       "flags": {"groups": true, "broadcasts": false, ...}},
      ...
    ]

``id`` is optional for new folders (a free id is allocated). Anything destructive
is confirm-gated; with ``confirm=False`` you get a full per-folder diff and nothing
is changed. Telethon is imported lazily so this module stays import-safe.
"""

from __future__ import annotations

import logging

from .user_client import UserClient

logger = logging.getLogger(__name__)

# Telegram allows dialog-filter ids in this range for user-defined folders.
_MIN_FILTER_ID = 2
_MAX_FILTER_ID = 255

_FLAG_FIELDS = (
    "contacts", "non_contacts", "groups", "broadcasts", "bots",
    "exclude_muted", "exclude_read", "exclude_archived",
)


def _peer_raw_id(p) -> int | None:
    for attr in ("channel_id", "chat_id", "user_id"):
        v = getattr(p, attr, None)
        if v is not None:
            return int(v)
    return None


def _filter_title(f) -> str:
    """Decode a DialogFilter title across telethon layers (str or TextWithEntities)."""
    t = getattr(f, "title", "")
    return getattr(t, "text", t) or ""


def _make_title(text: str):
    """Build a title value telethon accepts on this layer (TextWithEntities or str)."""
    try:
        from telethon.tl.types import TextWithEntities
        return TextWithEntities(text=text, entities=[])
    except Exception:  # noqa: BLE001 — older layer wants a plain string
        return text


async def list_folders() -> list[dict]:
    """Return current chat folders, titles + peers decoded against your dialogs."""
    from telethon.tl.functions.messages import GetDialogFiltersRequest

    async with UserClient() as c:
        # Build raw_id -> {title, kind} from the dialog list for friendly output.
        ents: dict[int, dict] = {}
        async for d in c.iter_dialogs():
            ent = d.entity
            ents[int(getattr(ent, "id", 0))] = {
                "title": d.name or "",
                "username": getattr(ent, "username", None),
            }

        res = await c(GetDialogFiltersRequest())
        filters = getattr(res, "filters", res)
        out: list[dict] = []
        for f in filters:
            if f.__class__.__name__ == "DialogFilterDefault":
                continue
            inc = [_peer_raw_id(p) for p in (getattr(f, "include_peers", []) or [])]
            exc = [_peer_raw_id(p) for p in (getattr(f, "exclude_peers", []) or [])]
            pin = [_peer_raw_id(p) for p in (getattr(f, "pinned_peers", []) or [])]
            out.append({
                "id": getattr(f, "id", None),
                "title": _filter_title(f),
                "emoticon": getattr(f, "emoticon", None) or "",
                "flags": {k: bool(getattr(f, k, False)) for k in _FLAG_FIELDS},
                "include": [{"id": i, **ents.get(i, {})} for i in inc],
                "exclude": [{"id": i, **ents.get(i, {})} for i in exc],
                "pinned": [{"id": i, **ents.get(i, {})} for i in pin],
            })
        return out


async def _resolve_input_peers(client, refs: list) -> tuple[list, list]:
    """Resolve chat refs (raw id / @username / t.me link) to InputPeers.

    Returns ``(input_peers, unresolved_refs)`` — unresolved are reported, never fatal.
    """
    peers, missing = [], []
    for ref in refs or []:
        try:
            peers.append(await client.get_input_entity(ref))
        except Exception:  # noqa: BLE001 — not in cache / bad ref
            logger.debug("folder peer unresolved: %r", ref, exc_info=True)
            missing.append(ref)
    return peers, missing


def _alloc_id(used: set[int]) -> int:
    for i in range(_MIN_FILTER_ID, _MAX_FILTER_ID + 1):
        if i not in used:
            return i
    raise RuntimeError("No free folder id available (max 255 reached).")


async def apply_plan(plan: list[dict], *, confirm: bool = False,
                     prune: bool = False) -> dict:
    """Apply a desired folder layout.

    ``confirm=False`` (default) returns a dry-run diff and changes nothing.
    ``prune=True`` also deletes existing folders not present in the plan.
    Folders are matched by ``id`` when given, else by case-insensitive title.
    """
    from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
    from telethon.tl.types import DialogFilter

    async with UserClient() as c:
        await c.get_dialogs()  # warm the entity cache so include/exclude ids resolve
        res = await c(GetDialogFiltersRequest())
        current = [f for f in (getattr(res, "filters", res))
                   if f.__class__.__name__ != "DialogFilterDefault"]
        by_id = {getattr(f, "id", None): f for f in current}
        by_title = {_filter_title(f).strip().lower(): f for f in current}
        used_ids = {getattr(f, "id", None) for f in current if getattr(f, "id", None)}

        actions: list[dict] = []
        planned_ids: set[int] = set()

        for spec in plan:
            title = str(spec.get("title", "")).strip()
            existing = None
            if spec.get("id") in by_id:
                existing = by_id[spec["id"]]
            elif title.lower() in by_title:
                existing = by_title[title.lower()]

            fid = getattr(existing, "id", None) or spec.get("id")
            if not fid:
                fid = _alloc_id(used_ids | planned_ids)
            planned_ids.add(fid)
            used_ids.add(fid)

            inc_peers, inc_miss = await _resolve_input_peers(c, spec.get("include"))
            exc_peers, exc_miss = await _resolve_input_peers(c, spec.get("exclude"))
            flags = {k: bool(v) for k, v in (spec.get("flags") or {}).items() if k in _FLAG_FIELDS}

            action = {
                "op": "update" if existing else "create",
                "id": fid,
                "title": title,
                "include": len(inc_peers),
                "exclude": len(exc_peers),
                "unresolved": inc_miss + exc_miss,
            }
            actions.append(action)

            if confirm:
                dialog_filter = DialogFilter(
                    id=fid,
                    title=_make_title(title),
                    pinned_peers=[],
                    include_peers=inc_peers,
                    exclude_peers=exc_peers,
                    emoticon=spec.get("emoticon") or None,
                    **flags,
                )
                await c(UpdateDialogFilterRequest(id=fid, filter=dialog_filter))

        deleted: list[int] = []
        if prune:
            for f in current:
                fid = getattr(f, "id", None)
                if fid and fid not in planned_ids:
                    deleted.append(fid)
                    if confirm:
                        await c(UpdateDialogFilterRequest(id=fid, filter=None))

        return {
            "dry_run": not confirm,
            "folders": len(plan),
            "creates": sum(1 for a in actions if a["op"] == "create"),
            "updates": sum(1 for a in actions if a["op"] == "update"),
            "deletes": deleted,
            "actions": actions,
        }


async def rename_folders(renames: list[dict], *, confirm: bool = False) -> dict:
    """Rename folders (title + optional emoticon) **without touching membership**.

    ``renames`` is a list of ``{"id": int, "title": str, "emoticon"?: str}``. Each
    folder's existing peers/flags are reused verbatim (their InputPeers already carry
    valid access hashes), so only the label changes. Dry-run unless ``confirm``.
    """
    from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest

    want = {int(r["id"]): r for r in renames}
    async with UserClient() as c:
        res = await c(GetDialogFiltersRequest())
        filters = getattr(res, "filters", res)
        changes: list[dict] = []
        for f in filters:
            fid = getattr(f, "id", None)
            if fid not in want:
                continue
            old = _filter_title(f)
            new = str(want[fid]["title"])
            emo = want[fid].get("emoticon")
            changes.append({"id": fid, "from": old, "to": new,
                            "emoticon": emo if emo is not None else getattr(f, "emoticon", None)})
            if confirm:
                f.title = _make_title(new)
                if emo is not None:
                    f.emoticon = emo or None
                await c(UpdateDialogFilterRequest(id=fid, filter=f))
        return {"dry_run": not confirm, "renamed": len(changes), "changes": changes}


async def delete_folder(folder_id: int, *, confirm: bool = False) -> dict:
    """Delete a single chat folder by id. Confirm-gated."""
    if not confirm:
        return {"dry_run": True, "would_delete": folder_id}
    from telethon.tl.functions.messages import UpdateDialogFilterRequest
    async with UserClient() as c:
        await c(UpdateDialogFilterRequest(id=int(folder_id), filter=None))
    return {"deleted": folder_id}
