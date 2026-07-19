"""Website login credentials in the vault.

A thin, purpose-built layer over :class:`~navig.vault.core.Vault` for storing and
retrieving **website logins** (``username`` + ``password`` [+ optional TOTP]).
It adds no schema — every login is an ordinary vault item:

    kind      = VaultItemKind.PASSWORD
    provider  = "web"                     (namespaces all website logins)
    label     = "web/<host>/<username>"   (UNIQUE per account)
    payload   = json({domain, username, password, url, totp_secret, ...selectors})
    metadata  = {domain, username, has_totp, url, type: "web_login"}   (no secrets)

The metadata is plaintext and searchable, so listing/looking-up a login never
decrypts a payload; only :func:`get_login` (and the resolver) decrypt.

Secrets never leave this module in plaintext except through :func:`get_login` /
:func:`resolve_login`, which callers must treat as sensitive — the value is
injected into a browser field server-side and never returned to an LLM.

See also :mod:`navig.vault.sessions` (the authenticated-session store that makes
auto-login "restore, don't retype").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .types import VaultItemKind

if TYPE_CHECKING:  # pragma: no cover
    from .core import Vault

__all__ = [
    "WebLogin",
    "LoginInfo",
    "LOGIN_PROVIDER",
    "normalize_host",
    "login_label",
    "add_login",
    "get_login",
    "list_logins",
    "find_logins_for_domain",
    "resolve_login",
    "remove_login",
]

LOGIN_PROVIDER = "web"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class WebLogin:
    """A full website login, including the secret fields.

    ``domain`` is the normalized host the credential belongs to (e.g.
    ``"github.com"``). Origin-matching against a live page is done by
    :mod:`navig.browser.origin_match` (Stage 2), not here.

    The optional ``*_selector`` fields are only used by the deterministic
    per-site template path; heuristic autofill ignores them.
    """

    domain: str
    username: str
    password: str
    url: str | None = None
    totp_secret: str | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    notes: str | None = None

    def to_payload(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_payload(cls, raw: bytes) -> "WebLogin":
        data = json.loads(raw)
        return cls(
            domain=data.get("domain", ""),
            username=data.get("username", ""),
            password=data.get("password", ""),
            url=data.get("url"),
            totp_secret=data.get("totp_secret"),
            username_selector=data.get("username_selector"),
            password_selector=data.get("password_selector"),
            submit_selector=data.get("submit_selector"),
            notes=data.get("notes"),
        )


@dataclass
class LoginInfo:
    """A non-secret summary of a stored login (safe to list / show / return)."""

    domain: str
    username: str
    label: str
    id: str
    url: str | None = None
    has_totp: bool = False
    last_used_at: datetime | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_host(value: str) -> str:
    """Reduce a URL or bare host to a lowercase hostname (no scheme/port/path).

    ``https://GitHub.com/login`` → ``github.com``. This is *not* registrable-domain
    extraction (that is Stage 2's ``origin_match``); it just canonicalises what the
    user typed so labels/lookups are stable.
    """
    v = (value or "").strip().lower()
    if "://" in v:
        v = urlparse(v).netloc or v
    # strip any leftover path, query, credentials, and port
    v = v.split("/", 1)[0]
    if "@" in v:
        v = v.rsplit("@", 1)[-1]
    v = v.split(":", 1)[0]
    return v


def _safe_username(username: str) -> str:
    """Sanitise a username for use inside a label (labels are '/'-delimited)."""
    return (username or "").strip().replace("/", "_") or "default"


def login_label(domain: str, username: str) -> str:
    return f"{LOGIN_PROVIDER}/{normalize_host(domain)}/{_safe_username(username)}"


def _vault(vault: "Vault | None") -> "Vault":
    if vault is not None:
        return vault
    from .core import get_vault  # noqa: PLC0415

    v = get_vault()
    if v is None:  # pragma: no cover — get_vault only returns None on hard init failure
        raise RuntimeError("vault unavailable")
    return v


def _info_from_item(item) -> LoginInfo:
    md = item.metadata or {}
    return LoginInfo(
        domain=md.get("domain", ""),
        username=md.get("username", ""),
        label=item.label,
        id=item.id,
        url=md.get("url"),
        has_totp=bool(md.get("has_totp", False)),
        last_used_at=item.last_used_at,
        created_at=item.created_at,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_login(login: WebLogin, *, vault: "Vault | None" = None) -> str:
    """Store (or overwrite) a website login. Returns the vault item id.

    The host is normalised; the domain on the stored record is the normalised
    host so later lookups are stable regardless of how the URL was typed.
    """
    v = _vault(vault)
    login.domain = normalize_host(login.domain)
    label = login_label(login.domain, login.username)
    metadata = {
        "domain": login.domain,
        "username": login.username,
        "has_totp": bool(login.totp_secret),
        "url": login.url,
        "type": "web_login",
    }
    return v.put(
        label,
        login.to_payload(),
        kind=VaultItemKind.PASSWORD,
        provider=LOGIN_PROVIDER,
        metadata=metadata,
    )


def get_login(domain: str, username: str, *, vault: "Vault | None" = None) -> WebLogin | None:
    """Return the full login (with secrets) for an exact domain+username, or None."""
    v = _vault(vault)
    label = login_label(domain, username)
    try:
        raw = v.get_bytes(label)
    except KeyError:
        return None
    return WebLogin.from_payload(raw)


def _get_login_by_label(label: str, *, vault: "Vault") -> WebLogin | None:
    try:
        raw = vault.get_bytes(label)
    except KeyError:
        return None
    return WebLogin.from_payload(raw)


def list_logins(*, vault: "Vault | None" = None) -> list[LoginInfo]:
    """List all stored website logins (non-secret summaries)."""
    v = _vault(vault)
    items = v.list(kind=VaultItemKind.PASSWORD, provider=LOGIN_PROVIDER)
    return [_info_from_item(it) for it in items]


def _registrable(host_or_url: str) -> str:
    """Registrable domain (eTLD+1) with a safe fallback to the normalised host.

    Lazy import keeps the vault layer from hard-depending on the browser layer.
    """
    try:
        from navig.browser.origin_match import registrable_domain  # noqa: PLC0415

        reg = registrable_domain(host_or_url)
        if reg:
            return reg
    except Exception:  # noqa: BLE001
        pass
    return normalize_host(host_or_url)


def find_logins_for_domain(domain: str, *, vault: "Vault | None" = None) -> list[LoginInfo]:
    """All stored logins that bind to *domain* by registrable domain (eTLD+1).

    A login stored for ``mail.google.com`` matches a lookup for ``google.com`` (and
    vice-versa), because auto-login resolves by the page's registrable domain. Exact
    host is still honoured as a subset of registrable-domain equality.
    """
    host = normalize_host(domain)
    target_reg = _registrable(domain)
    out = []
    for i in list_logins(vault=vault):
        if i.domain == host or _registrable(i.domain) == target_reg:
            out.append(i)
    return out


def resolve_login(
    domain: str,
    username: str | None = None,
    *,
    vault: "Vault | None" = None,
) -> tuple[WebLogin | None, str]:
    """Pick the login to use for *domain*, returning ``(login, status)``.

    status is one of:
      - ``"ok"``                  — a single credential resolved (``login`` set)
      - ``"no_credential"``       — nothing stored for this domain/username
      - ``"needs_disambiguation"``— several accounts and no *username* given

    Registrable-domain / origin matching is the caller's job (Stage 2); this
    resolves by the normalised host stored on the record.
    """
    v = _vault(vault)
    host = normalize_host(domain)
    candidates = find_logins_for_domain(domain, vault=v)

    if username:
        for info in candidates:
            if info.username == username:
                return _get_login_by_label(info.label, vault=v), "ok"
        return None, "no_credential"

    if not candidates:
        return None, "no_credential"
    # Prefer an EXACT-host match when exactly one exists — so a user who stored
    # separate credentials for a parent domain AND a subdomain still resolves the
    # precise host instead of getting a new disambiguation prompt.
    exact = [c for c in candidates if c.domain == host]
    if len(exact) == 1:
        return _get_login_by_label(exact[0].label, vault=v), "ok"
    if len(candidates) == 1:
        return _get_login_by_label(candidates[0].label, vault=v), "ok"
    return None, "needs_disambiguation"


def remove_login(domain: str, username: str, *, vault: "Vault | None" = None) -> bool:
    """Delete a stored login. Returns True if something was removed."""
    v = _vault(vault)
    return v.delete(login_label(domain, username))
