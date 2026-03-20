"""
NAVIG Identity Layer

User registry linking Telegram IDs to TON wallet addresses
and optional social profiles.

Usage:
    from navig.identity import IdentityStore, UserProfile
    store = IdentityStore()
    profile = store.get_or_create(telegram_id=123456)
    profile.ton_wallet = "UQ..."
    store.save(profile)
"""

from navig.identity.models import SocialLink, UserProfile
from navig.identity.store import IdentityStore, get_identity_store

__all__ = [
    "UserProfile",
    "SocialLink",
    "IdentityStore",
    "get_identity_store",
]
