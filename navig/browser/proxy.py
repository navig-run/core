"""Proxy pool — BYO proxies with rotation, per-profile assignment, and block-cooldown.

The whole stealth stack was missing an IP layer: ``stealth.py`` took a single static
proxy string (and silently dropped username/password), the yt-dlp path had none, and there
was no rotation or block-aware cooldown. This module is the reusable IP layer they share.

Config (``~/.navig/config.yaml``)::

    browser:
      proxies:
        - "http://user:pass@host:port"                        # URL form
        - {server: "socks5://host:port", username: u, password: p, label: res-us}
      proxy_rotation: round-robin      # or "random"   (default round-robin)
      proxy_cooldown_seconds: 300      # a blocked proxy sits out this long

A profile's own ``proxy`` field (``cdp-profiles.json``) overrides the pool for that profile.
Nothing here dials out on its own — proxies are BYO and opt-in; with none configured every
resolver returns ``None`` and the caller goes direct.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

__all__ = [
    "ProxySpec",
    "ProxyPool",
    "get_pool",
    "reset_pool",
    "resolve_proxy",
    "resolve_proxy_url",
]


@dataclass
class ProxySpec:
    """One proxy endpoint. ``server`` carries scheme + host + port (no credentials)."""

    server: str
    username: str | None = None
    password: str | None = None
    label: str | None = None

    # ── parsing ──────────────────────────────────────────────────────────────
    @classmethod
    def from_url(cls, url: str, *, label: str | None = None) -> "ProxySpec":
        """Parse ``scheme://[user:pass@]host:port`` into a spec (creds split out)."""
        url = url.strip()
        if "://" not in url:
            url = "http://" + url  # bare host:port → assume http proxy
        parts = urlsplit(url)
        scheme = parts.scheme or "http"
        host = parts.hostname or ""
        netloc = host if parts.port is None else f"{host}:{parts.port}"
        server = urlunsplit((scheme, netloc, "", "", ""))
        # urlsplit leaves credentials percent-encoded — decode so the stored spec holds
        # the real values; to_url() re-encodes them on the way out (stable round-trip).
        user = unquote(parts.username) if parts.username else None
        pwd = unquote(parts.password) if parts.password else None
        return cls(server=server, username=user, password=pwd, label=label or host)

    @classmethod
    def from_config(cls, entry: "str | dict") -> "ProxySpec | None":
        """Accept either a URL string or a ``{server, username, password, label}`` dict."""
        if isinstance(entry, str):
            return cls.from_url(entry) if entry.strip() else None
        if isinstance(entry, dict):
            server = (entry.get("server") or entry.get("url") or "").strip()
            if not server:
                return None
            # A dict may still embed creds in the server URL — normalise via from_url,
            # then let explicit username/password fields win.
            base = cls.from_url(server, label=entry.get("label"))
            return cls(
                server=base.server,
                username=entry.get("username", base.username),
                password=entry.get("password", base.password),
                label=entry.get("label") or base.label,
            )
        return None

    # ── adapters ─────────────────────────────────────────────────────────────
    def to_playwright(self) -> dict:
        """Playwright/Patchright launch ``proxy=`` dict (server + optional creds)."""
        out: dict = {"server": self.server}
        if self.username is not None:
            out["username"] = self.username
        if self.password is not None:
            out["password"] = self.password
        return out

    def to_url(self) -> str:
        """A single proxy URL with credentials embedded — the form yt-dlp/curl want."""
        parts = urlsplit(self.server)
        netloc = parts.netloc
        if self.username is not None:
            cred = quote(self.username, safe="")
            if self.password is not None:
                cred += ":" + quote(self.password, safe="")
            netloc = f"{cred}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, "", "", ""))

    def redacted(self) -> str:
        """Loggable form — never leaks the password."""
        who = f"{self.username}:***@" if self.username else ""
        return f"{self.label or ''}[{who}{self.server}]".strip()


class ProxyPool:
    """Rotates a set of proxies, skipping any in block-cooldown."""

    def __init__(self, specs: list[ProxySpec], *, rotation: str = "round-robin",
                 cooldown_seconds: float = 300.0,
                 clock: Callable[[], float] = time.time):
        self._specs = list(specs)
        self._rotation = rotation if rotation in ("round-robin", "random") else "round-robin"
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._idx = 0
        self._blocked_until: dict[str, float] = {}  # server → epoch when it's usable again

    def __len__(self) -> int:
        return len(self._specs)

    def available(self) -> list[ProxySpec]:
        now = self._clock()
        return [s for s in self._specs if self._blocked_until.get(s.server, 0) <= now]

    def next(self) -> ProxySpec | None:
        """Return the next usable proxy, or None if the pool is empty / all cooling down."""
        usable = self.available()
        if not usable:
            return None
        if self._rotation == "random":
            return random.choice(usable)
        # Round-robin across the FULL list so ordering is stable; skip cooled-down ones.
        n = len(self._specs)
        for _ in range(n):
            spec = self._specs[self._idx % n]
            self._idx += 1
            if spec in usable:
                return spec
        return usable[0]

    def mark_blocked(self, spec: ProxySpec, *, cooldown_seconds: float | None = None) -> None:
        """Sideline a proxy that just got blocked/rate-limited for a cooldown window."""
        cd = self._cooldown if cooldown_seconds is None else float(cooldown_seconds)
        self._blocked_until[spec.server] = self._clock() + cd

    def clear_cooldowns(self) -> None:
        self._blocked_until.clear()


# ── config-backed singleton ──────────────────────────────────────────────────

_pool: ProxyPool | None = None
_pool_signature: tuple | None = None


def _load_browser_config() -> dict:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        return get_config_manager().global_config.get("browser", {}) or {}
    except Exception:  # noqa: BLE001 — config unavailable → empty (go direct)
        return {}


def get_pool(*, force_reload: bool = False) -> ProxyPool:
    """Return the process proxy pool built from config (rebuilt if config changed)."""
    global _pool, _pool_signature
    cfg = _load_browser_config()
    raw = cfg.get("proxies") or []
    signature = (tuple(map(str, raw)), cfg.get("proxy_rotation"),
                 cfg.get("proxy_cooldown_seconds"))
    if _pool is None or force_reload or signature != _pool_signature:
        specs = [s for s in (ProxySpec.from_config(e) for e in raw) if s is not None]
        _pool = ProxyPool(
            specs,
            rotation=str(cfg.get("proxy_rotation", "round-robin")),
            cooldown_seconds=float(cfg.get("proxy_cooldown_seconds", 300)),
        )
        _pool_signature = signature
    return _pool


def reset_pool() -> None:
    """Drop the cached pool (tests / after a config change)."""
    global _pool, _pool_signature
    _pool = None
    _pool_signature = None


def resolve_proxy(profile_proxy: str | None = None) -> ProxySpec | None:
    """The proxy to use: an explicit per-profile URL wins, else the next pool proxy.

    Returns a ``ProxySpec`` (or None to go direct). Callers pick ``to_playwright()`` or
    ``to_url()`` for their transport.
    """
    if profile_proxy:
        return ProxySpec.from_url(profile_proxy)
    return get_pool().next()


def resolve_proxy_url(profile_proxy: str | None = None) -> str | None:
    """Convenience: the resolved proxy as a credential-embedded URL, or None."""
    spec = resolve_proxy(profile_proxy)
    return spec.to_url() if spec is not None else None
