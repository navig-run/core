"""navig.telegram — the MTProto user-client engine behind the Telegram Manager.

This is the "full account" half of the Telegram Manager (the other half is the
bot-token ``TelegramChannel`` under ``gateway/channels/``). It logs in the
*owner's own* Telegram account via Telethon (MTProto) so NAVIG can do what bots
cannot: read full history, list/resolve every dialog + forum topic, move/forward
messages across topics & groups, rename chats, dedupe & organize media, and
backfill the existing ``TelegramCatalogStore`` so search covers everything.

Security: this is the owner's account. It is driven **only** by the owner from
the CLI / deck — it is never reachable from an inbound bot message. The session
+ api_id/api_hash live encrypted in the vault, never on disk in plaintext, never
logged. Destructive ops (move = copy+delete, dedupe-delete) are confirm-gated and
default to dry-run.

Telethon is an OPTIONAL dependency: every import of it is lazy (inside functions),
so this package imports cleanly even when telethon is absent — callers should
check :func:`telethon_available` and degrade gracefully.
"""

from __future__ import annotations


def telethon_available() -> bool:
    """True when the optional ``telethon`` dependency is importable."""
    try:
        import telethon  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


__all__ = ["telethon_available"]
