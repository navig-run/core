"""Value objects for the social publishing framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PostContent:
    """A composed post, network-agnostic until rendered for a target.

    ``media`` items are attachment descriptors (``{path|url|data, kind,
    filename, mime}``) — the same shape the messaging adapters consume.
    ``per_network`` holds per-network overrides keyed by network name, e.g.
    ``{"twitter": {"body": "short version"}}``.
    """

    body: str = ""
    media: list[dict[str, Any]] = field(default_factory=list)
    link: str | None = None
    hashtags: list[str] = field(default_factory=list)
    per_network: dict[str, dict[str, Any]] = field(default_factory=dict)

    def render(self, network: str) -> "RenderedPost":
        """Resolve the final text + media for *network*, applying overrides."""
        ov = self.per_network.get(network, {})
        body = ov.get("body", self.body) or ""
        tags = ov.get("hashtags", self.hashtags) or []
        link = ov.get("link", self.link)
        media = ov.get("media", self.media) or []

        text = body
        # Append hashtags that aren't already present.
        tag_str = " ".join(f"#{t.lstrip('#')}" for t in tags if t)
        if tag_str and tag_str not in text:
            text = f"{text}\n\n{tag_str}".strip()
        if link and link not in text:
            text = f"{text}\n{link}".strip()
        return RenderedPost(text=text, media=media, link=link)

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "media": self.media,
            "link": self.link,
            "hashtags": self.hashtags,
            "per_network": self.per_network,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PostContent":
        d = d or {}
        return cls(
            body=d.get("body", "") or "",
            media=d.get("media", []) or [],
            link=d.get("link"),
            hashtags=d.get("hashtags", []) or [],
            per_network=d.get("per_network", {}) or {},
        )


@dataclass(frozen=True)
class RenderedPost:
    """The final text + media to hand to a single network."""

    text: str
    media: list[dict[str, Any]]
    link: str | None = None


@dataclass(frozen=True)
class PublishTarget:
    """A destination on a network (a feed, page, channel, subreddit, account)."""

    network: str
    target: str = ""          # account default when empty; else id / handle / subreddit
    display: str = ""
    media: bool = True
    char_limit: int | None = None


@dataclass(frozen=True)
class PublishReceipt:
    """Result of publishing one post to one target."""

    network: str
    target: str
    ok: bool
    id: str | None = None
    url: str | None = None
    error: str | None = None
    requires_auth: bool = False

    @classmethod
    def success(cls, network: str, target: str, *, id: str | None = None, url: str | None = None) -> "PublishReceipt":
        return cls(network=network, target=target, ok=True, id=id, url=url)

    @classmethod
    def failure(cls, network: str, target: str, error: str, *, requires_auth: bool = False) -> "PublishReceipt":
        return cls(network=network, target=target, ok=False, error=error, requires_auth=requires_auth)

    def to_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "target": self.target,
            "ok": self.ok,
            "id": self.id,
            "url": self.url,
            "error": self.error,
            "requires_auth": self.requires_auth,
        }
