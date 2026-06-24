"""Category-aware duplicate detection (ported + generalized from tg-music-bot).

Groups every copy of a media item (by ``file_unique_id``, falling back to a
normalized artist+title+duration key), then classifies each group:

  EXACT     — 2+ byte-identical copies (same ``file_unique_id``) in the SAME topic
              → re-upload; safe to delete the extras.
  INCOMING  — item is sorted into a real topic AND also still sits in the inbox
              ("Incoming") → keep the sorted copy, drop the inbox copy. Safe.
  NEAR      — same item, different file (re-encode/quality), one topic → REVIEW.
  CONFLICT  — same item lives in 2+ different real topics → wrong-category
              candidate; NEVER auto-deleted; the owner decides. REVIEW.

Keep rule: prefer a sorted copy over inbox; then largest file (best quality);
then earliest. The SAFE auto-delete set is EXACT + INCOMING only — everything
else is review-only. Pure stdlib; no Telegram calls here.

A record is a dict with any of: ``file_unique_id, performer, title, file_name,
duration, file_size, topic, message_id, chat_id``.
"""

from __future__ import annotations

from collections import defaultdict

from .util import artist_title_key, dur_bucket

INCOMING = "Incoming"


def _song_key(r: dict):
    """Group key: prefer a normalized artist/title + duration (so re-encodes of the
    same track collide — the music case); fall back to ``file_unique_id`` for media
    with no usable title (photos/videos/docs → exact-dupe grouping only)."""
    k = artist_title_key(r.get("performer"), r.get("title"), r.get("file_name"))
    if k and len(k) >= 4:
        return ("key", k, dur_bucket(r.get("duration")))
    fuid = r.get("file_unique_id")
    if fuid:
        return ("fuid", fuid)
    return None


def _exact_sig(r: dict):
    if r.get("file_unique_id"):
        return ("fuid", r["file_unique_id"])
    if r.get("file_size") and r.get("duration"):
        return ("sz", r["file_size"], dur_bucket(r["duration"], 1))
    return ("uid", r.get("message_id"))  # unique → won't collide


def _topic(r: dict) -> str:
    return r.get("topic") or INCOMING


def _keep(members: list[dict]) -> dict:
    return sorted(
        members,
        key=lambda r: (_topic(r) == INCOMING, -(r.get("file_size") or 0), r.get("message_id") or 0),
    )[0]


def find_duplicates(records: list[dict]) -> dict:
    """Return ``{"groups": [...], "safe_delete": [...], "review": [...]}``.

    ``safe_delete`` lists ``{chat_id, message_id}`` for EXACT + INCOMING extras
    (never CONFLICT/NEAR). ``review`` lists groups a human must decide on.
    """
    songs: dict = defaultdict(list)
    for r in records:
        k = _song_key(r)
        if k:
            songs[k].append(r)

    groups: list[dict] = []
    safe_delete: list[dict] = []
    review: list[dict] = []

    for members in songs.values():
        if len(members) < 2:
            continue
        topics = {_topic(m) for m in members}
        real_topics = topics - {INCOMING}
        keep = _keep(members)
        dels = [m for m in members if m.get("message_id") != keep.get("message_id")]

        if len(real_topics) >= 2:
            tier = "CONFLICT"           # wrong-category candidate — review only
        elif INCOMING in topics and real_topics:
            tier = "INCOMING"           # sorted + inbox copy → drop the inbox copy
        else:
            sigs = {_exact_sig(m) for m in members}
            tier = "EXACT" if len(sigs) == 1 else "NEAR"

        group = {
            "tier": tier,
            "key": artist_title_key(keep.get("performer"), keep.get("title"), keep.get("file_name")),
            "keep": {"chat_id": keep.get("chat_id"), "message_id": keep.get("message_id"),
                     "topic": _topic(keep), "file_size": keep.get("file_size")},
            "delete": [{"chat_id": m.get("chat_id"), "message_id": m.get("message_id"),
                        "topic": _topic(m), "file_size": m.get("file_size")} for m in dels],
            "topics": sorted(topics),
        }
        groups.append(group)
        if tier in ("EXACT", "INCOMING"):
            safe_delete.extend(group["delete"])
        else:
            review.append(group)

    return {"groups": groups, "safe_delete": safe_delete, "review": review,
            "summary": {
                "groups": len(groups),
                "safe_delete": len(safe_delete),
                "review": len(review),
            }}
