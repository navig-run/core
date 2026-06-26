"""SocialPublisher protocol + a shared base class for platform publishers."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from navig.social.credentials import get_token
from navig.social.types import PublishReceipt, PublishTarget, RenderedPost

logger = logging.getLogger(__name__)


@runtime_checkable
class SocialPublisher(Protocol):
    """One-to-many publish surface for a social network."""

    name: str

    @property
    def capabilities(self) -> dict[str, Any]: ...

    def is_configured(self) -> bool: ...

    async def list_targets(self) -> list[PublishTarget]: ...

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt: ...


class BasePublisher:
    """Common publisher behaviour: credential lookup + capability defaults.

    Subclasses set ``name`` and override :meth:`publish`. Many platforms in this
    build ship as credential-wired stubs: if no token is configured, ``publish``
    short-circuits to a ``requires_auth`` receipt; once the user supplies a token
    via Settings the concrete API call runs.
    """

    name: str = "social"
    char_limit: int | None = None
    supports_media: bool = True

    @property
    def capabilities(self) -> dict[str, Any]:
        return {"text": True, "media": self.supports_media, "char_limit": self.char_limit}

    def token(self) -> str | None:
        return get_token(self.name)

    def is_configured(self) -> bool:
        return bool(self.token())

    async def list_targets(self) -> list[PublishTarget]:
        # Default: a single "account" target.
        return [
            PublishTarget(
                network=self.name,
                target="",
                display=f"{self.name} (default account)",
                media=self.supports_media,
                char_limit=self.char_limit,
            )
        ]

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:  # pragma: no cover - overridden
        raise NotImplementedError

    # ── helpers for subclasses ────────────────────────────────

    def _require_auth(self, target: str) -> PublishReceipt | None:
        if not self.is_configured():
            return PublishReceipt.failure(
                self.name, target,
                f"{self.name} not connected — add a token in Settings",
                requires_auth=True,
            )
        return None

    async def _session(self):
        import aiohttp

        return aiohttp.ClientSession()
