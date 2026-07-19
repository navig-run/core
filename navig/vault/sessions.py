"""Authenticated browser sessions in the vault (the "restore, don't retype" store).

The most robust auto-login re-enters *nothing*: it restores a previously
authenticated session (cookies + localStorage) so the site is already logged in.
This module persists Playwright ``storageState`` blobs in the vault, keyed by
host, so :mod:`navig.browser.autofill` (Stage 2) can restore a session before
ever falling back to form-filling a password.

Storage layout (no schema change — an ordinary vault item):

    kind      = VaultItemKind.JSON
    provider  = "web-session"
    label     = "session/<host>/<username>"
    payload   = json({domain, username, storage_state, captured_at, expires_hint})
    metadata  = {domain, username, captured_at}   (no cookie values)

``storage_state`` is Playwright's ``{cookies: [...], origins: [{origin,
localStorage: [...]}]}``. Cookie values ARE secrets, so the payload is encrypted
like any other vault item; only the non-secret metadata is plaintext.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .logins import _safe_username, normalize_host
from .types import VaultItemKind

if TYPE_CHECKING:  # pragma: no cover
    from .core import Vault

__all__ = [
    "SessionState",
    "SESSION_PROVIDER",
    "session_label",
    "save_session",
    "get_session",
    "list_sessions",
    "remove_session",
]

SESSION_PROVIDER = "web-session"


@dataclass
class SessionState:
    """A captured authenticated session for one host."""

    domain: str
    storage_state: dict[str, Any] = field(default_factory=dict)
    username: str | None = None
    captured_at: str | None = None  # ISO-8601 UTC
    expires_hint: str | None = None

    def to_payload(self) -> bytes:
        return json.dumps(
            {
                "domain": self.domain,
                "username": self.username,
                "storage_state": self.storage_state,
                "captured_at": self.captured_at,
                "expires_hint": self.expires_hint,
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def from_payload(cls, raw: bytes) -> "SessionState":
        data = json.loads(raw)
        return cls(
            domain=data.get("domain", ""),
            storage_state=data.get("storage_state") or {},
            username=data.get("username"),
            captured_at=data.get("captured_at"),
            expires_hint=data.get("expires_hint"),
        )


def session_label(domain: str, username: str | None = None) -> str:
    return f"{SESSION_PROVIDER}/{normalize_host(domain)}/{_safe_username(username or 'default')}"


def _vault(vault: "Vault | None") -> "Vault":
    if vault is not None:
        return vault
    from .core import get_vault  # noqa: PLC0415

    v = get_vault()
    if v is None:  # pragma: no cover
        raise RuntimeError("vault unavailable")
    return v


def save_session(
    domain: str,
    storage_state: dict[str, Any],
    *,
    username: str | None = None,
    expires_hint: str | None = None,
    vault: "Vault | None" = None,
) -> str:
    """Persist (or overwrite) a captured session for *domain*. Returns the item id."""
    v = _vault(vault)
    host = normalize_host(domain)
    captured_at = datetime.now(timezone.utc).isoformat()
    state = SessionState(
        domain=host,
        storage_state=storage_state or {},
        username=username,
        captured_at=captured_at,
        expires_hint=expires_hint,
    )
    metadata = {
        "domain": host,
        "username": username,
        "captured_at": captured_at,
        "type": "web_session",
    }
    return v.put(
        session_label(host, username),
        state.to_payload(),
        kind=VaultItemKind.JSON,
        provider=SESSION_PROVIDER,
        metadata=metadata,
    )


def get_session(
    domain: str,
    username: str | None = None,
    *,
    vault: "Vault | None" = None,
) -> SessionState | None:
    """Return the stored session for a host (+ optional account), or None.

    When no account is pinned, a session saved *with* an account
    (``web-session/<host>/<user>``) is otherwise invisible to a username-less
    lookup (which probes ``web-session/<host>/default``). So if the direct label
    misses and ``username`` is None, fall back to the most-recently captured
    session for this host regardless of its account label. A pinned ``username``
    is respected exactly (no fallback), so it never returns a different account.
    """
    v = _vault(vault)
    try:
        return SessionState.from_payload(v.get_bytes(session_label(domain, username)))
    except KeyError:
        pass
    if username is not None:
        return None
    host = normalize_host(domain)
    best_label: str | None = None
    best_at = ""
    for s in list_sessions(vault=v):
        if normalize_host(str(s.get("domain") or "")) != host:
            continue
        at = str(s.get("captured_at") or "")
        if best_label is None or at >= best_at:
            best_label, best_at = s.get("label"), at
    if not best_label:
        return None
    try:
        return SessionState.from_payload(v.get_bytes(best_label))
    except KeyError:
        return None


def list_sessions(*, vault: "Vault | None" = None) -> list[dict[str, Any]]:
    """List captured sessions (non-secret metadata summaries)."""
    v = _vault(vault)
    items = v.list(kind=VaultItemKind.JSON, provider=SESSION_PROVIDER)
    out: list[dict[str, Any]] = []
    for it in items:
        md = it.metadata or {}
        out.append(
            {
                "domain": md.get("domain", ""),
                "username": md.get("username"),
                "captured_at": md.get("captured_at"),
                "label": it.label,
                "id": it.id,
            }
        )
    return out


def remove_session(
    domain: str,
    username: str | None = None,
    *,
    vault: "Vault | None" = None,
) -> bool:
    """Delete a stored session. Returns True if something was removed."""
    v = _vault(vault)
    return v.delete(session_label(domain, username))
