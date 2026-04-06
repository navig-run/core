"""Identity data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SocialLink:
    """A linked social account."""

    platform: str  # "github", "twitter", "discord", etc.
    handle: str  # username / ID on that platform
    verified: bool = False
    linked_at: datetime = field(default_factory=datetime.now)  # utcnow deprecated in Py3.12+


@dataclass
class UserProfile:
    """NAVIG identity record.

    Primary key is ``telegram_id`` (integer) since Telegram is the
    default auth gateway.  A TON wallet address can be linked for
    on-chain identity verification.
    """

    telegram_id: int
    username: str | None = None  # Telegram @handle
    display_name: str | None = None

    # TON identity
    ton_wallet_address: str | None = None
    ton_verified: bool = False

    # Social links
    socials: list[SocialLink] = field(default_factory=list)

    # Preferences
    preferred_channel: str = "telegram"  # "telegram" | "matrix" | "both"
    matrix_user_id: str | None = None  # @user:homeserver.tld
    language: str = "en"
    timezone: str | None = None

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)  # utcnow deprecated in Py3.12+
    updated_at: datetime = field(default_factory=datetime.now)  # utcnow deprecated in Py3.12+
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- Serialisation helpers -----------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "telegram_id": self.telegram_id,
            "username": self.username,
            "display_name": self.display_name,
            "ton_wallet_address": self.ton_wallet_address,
            "ton_verified": self.ton_verified,
            "socials": [
                {"platform": s.platform, "handle": s.handle, "verified": s.verified}
                for s in self.socials
            ],
            "preferred_channel": self.preferred_channel,
            "matrix_user_id": self.matrix_user_id,
            "language": self.language,
            "timezone": self.timezone,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserProfile:
        socials = [
            SocialLink(
                platform=s["platform"],
                handle=s["handle"],
                verified=s.get("verified", False),
            )
            for s in d.get("socials", [])
        ]
        return cls(
            telegram_id=d["telegram_id"],
            username=d.get("username"),
            display_name=d.get("display_name"),
            ton_wallet_address=d.get("ton_wallet_address"),
            ton_verified=d.get("ton_verified", False),
            socials=socials,
            preferred_channel=d.get("preferred_channel", "telegram"),
            matrix_user_id=d.get("matrix_user_id"),
            language=d.get("language", "en"),
            timezone=d.get("timezone"),
            created_at=(
                datetime.fromisoformat(d["created_at"]) if "created_at" in d else datetime.now()
            ),
            updated_at=(
                datetime.fromisoformat(d["updated_at"]) if "updated_at" in d else datetime.now()
            ),
            metadata=d.get("metadata", {}),
        )
