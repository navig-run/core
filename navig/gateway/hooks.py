"""
navig/gateway/hooks.py
──────────────────────
Hardened webhook intake with fail-fast config resolution.

All hook configuration is validated once at boot into a typed ``HooksConfig``
dataclass.  Missing auth tokens, invalid base paths, and bad limits raise
``HooksConfigError`` immediately so misconfiguration never reaches the first
live request.

Request-time enforcement:

- Bearer-token authentication (``Authorization: Bearer <token>``).
- Body-size limit (``hooks.max_body_bytes``, default 262 144 bytes).
- Idempotency-key length limit (``X-Idempotency-Key`` header,
  default 256 chars).
- Session-prefix allowlist: the prefix before ``/`` in the path segment
  after ``base_path`` is resolved once at boot; unknown prefixes are
  rejected with 403.

No HTTP-framework dependency — the handler accepts raw ``(method, path,
headers, body)`` tuples and returns ``(status_code, body_bytes)``.  Callers
wire this into aiohttp / httpx / any server of choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

# ──────────────────────────────────────────────────────────────────────────────
# Public constants (single source of truth — defaults.yaml mirrors these)
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_BASE_PATH: str = "/hooks"
_DEFAULT_MAX_BODY_BYTES: int = 262_144          # 256 KiB
_DEFAULT_IDEMPOTENCY_KEY_MAX_LEN: int = 256

_BEARER_RE = re.compile(r"^Bearer\s+(.+)$", re.ASCII)

# ──────────────────────────────────────────────────────────────────────────────
# Config types
# ──────────────────────────────────────────────────────────────────────────────


class HooksConfigError(ValueError):
    """Raised at startup when the ``hooks`` config block is invalid."""


@dataclass(frozen=True)
class HooksConfig:
    """Immutable, fully-validated webhook configuration resolved at boot.

    Attributes
    ----------
    auth_token:
        Required secret.  Incoming requests **must** carry
        ``Authorization: Bearer <auth_token>``; anything else is rejected
        with 401.
    base_path:
        URL prefix for all webhook routes.  Must start with ``/`` and must
        not be exactly ``/``.
    max_body_bytes:
        Upper bound on request body size.  Bodies exceeding this limit are
        rejected with 413 before any parsing occurs.
    idempotency_key_max_len:
        Maximum length of the ``X-Idempotency-Key`` header value.  Keys
        that exceed this length are rejected with 400.
    session_prefix_allowlist:
        Ordered list of permitted session-prefix strings.  An empty list
        means *all* prefixes are allowed.  Resolved once at boot; unknown
        prefixes are rejected with 403 at request time.
    """

    auth_token: str
    base_path: str = _DEFAULT_BASE_PATH
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES
    idempotency_key_max_len: int = _DEFAULT_IDEMPOTENCY_KEY_MAX_LEN
    session_prefix_allowlist: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # --- auth token ---
        if not self.auth_token or not self.auth_token.strip():
            raise HooksConfigError(
                "hooks.auth_token is required but is empty. "
                "Set it in ~/.navig/config.yaml under hooks.auth_token."
            )
        # --- base_path ---
        if not self.base_path.startswith("/"):
            raise HooksConfigError(
                f"hooks.base_path must start with '/'; got: {self.base_path!r}"
            )
        if self.base_path == "/":
            raise HooksConfigError(
                "hooks.base_path must not be '/'. "
                "Use a specific path such as '/hooks' or '/webhooks'."
            )
        # --- limits ---
        if self.max_body_bytes < 1:
            raise HooksConfigError(
                f"hooks.max_body_bytes must be positive; got: {self.max_body_bytes}"
            )
        if self.idempotency_key_max_len < 1:
            raise HooksConfigError(
                "hooks.idempotency_key_max_len must be positive; "
                f"got: {self.idempotency_key_max_len}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Config factory
# ──────────────────────────────────────────────────────────────────────────────


def load_hooks_config(raw: dict) -> HooksConfig:
    """Parse and validate the ``hooks`` sub-dict from the merged config.

    Parameters
    ----------
    raw:
        The ``hooks`` block extracted from the application config, e.g.
        ``get_config_manager().global_config.get('hooks', {})``.

    Returns
    -------
    HooksConfig
        Validated, frozen config dataclass.

    Raises
    ------
    HooksConfigError
        If any required field is missing or any value fails validation.
    """
    if not isinstance(raw, dict):
        raise HooksConfigError(
            f"hooks config block must be a mapping; got {type(raw).__name__!r}"
        )

    auth_token: str = raw.get("auth_token", "")
    base_path: str = raw.get("base_path", _DEFAULT_BASE_PATH)
    max_body_bytes: int = int(raw.get("max_body_bytes", _DEFAULT_MAX_BODY_BYTES))
    idempotency_key_max_len: int = int(
        raw.get("idempotency_key_max_len", _DEFAULT_IDEMPOTENCY_KEY_MAX_LEN)
    )
    raw_allowlist = raw.get("session_prefix_allowlist") or []
    if not isinstance(raw_allowlist, (list, tuple)):
        raise HooksConfigError(
            "hooks.session_prefix_allowlist must be a list of strings; "
            f"got {type(raw_allowlist).__name__!r}"
        )
    allowlist: tuple[str, ...] = tuple(str(p) for p in raw_allowlist)

    # Delegate all validation to the dataclass __post_init__
    return HooksConfig(
        auth_token=auth_token,
        base_path=base_path,
        max_body_bytes=max_body_bytes,
        idempotency_key_max_len=idempotency_key_max_len,
        session_prefix_allowlist=allowlist,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Request handling
# ──────────────────────────────────────────────────────────────────────────────


class HooksHandler:
    """Stateless request validator for incoming webhook calls.

    This class performs *only* security and sanity checks; it does not parse
    payloads or dispatch business logic.  The caller is responsible for wiring
    the result into its HTTP server.

    Parameters
    ----------
    config:
        A fully-validated ``HooksConfig`` instance (typically produced by
        :func:`load_hooks_config` at application startup).
    """

    def __init__(self, config: HooksConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        """Validate one incoming webhook request.

        Parameters
        ----------
        method:
            HTTP method (unused in validation but kept for interface clarity).
        path:
            Request path exactly as received (e.g. ``/hooks/myprefix/event``).
        headers:
            Case-insensitive mapping of header names to values.
        body:
            Raw request body bytes (already read by the caller).

        Returns
        -------
        (status_code, response_body)
            ``(200, b"OK")`` if all checks pass.
            ``(401, b"Unauthorized")`` — missing or wrong auth token.
            ``(400, b"Bad Request: ...")`` — idempotency key too long.
            ``(413, b"Payload Too Large")`` — body exceeds max_body_bytes.
            ``(403, b"Forbidden: unknown session prefix")`` — prefix not in
              allowlist (when allowlist is non-empty).
        """
        # 1. Body-size check (cheapest possible — done before anything else)
        if len(body) > self._cfg.max_body_bytes:
            return 413, b"Payload Too Large"

        # 2. Authentication
        auth_status = self._check_auth(headers)
        if auth_status != 200:
            return auth_status, b"Unauthorized"

        # 3. Idempotency key length
        idem_key = _header(headers, "x-idempotency-key")
        if idem_key is not None and len(idem_key) > self._cfg.idempotency_key_max_len:
            return 400, (
                b"Bad Request: X-Idempotency-Key exceeds maximum length of "
                + str(self._cfg.idempotency_key_max_len).encode()
                + b" characters"
            )

        # 4. Session-prefix allowlist (only enforced when list is non-empty)
        if self._cfg.session_prefix_allowlist:
            prefix = _extract_session_prefix(path, self._cfg.base_path)
            if prefix is None or prefix not in self._cfg.session_prefix_allowlist:
                return 403, b"Forbidden: unknown session prefix"

        return 200, b"OK"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_auth(self, headers: Mapping[str, str]) -> int:
        """Return 200 on success, 401 otherwise."""
        auth_header = _header(headers, "authorization")
        if auth_header is None:
            return 401
        m = _BEARER_RE.match(auth_header)
        if not m:
            return 401
        if m.group(1) != self._cfg.auth_token:
            return 401
        return 200


# ──────────────────────────────────────────────────────────────────────────────
# Helpers (module-private)
# ──────────────────────────────────────────────────────────────────────────────


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup.

    Checks both the exact name and a title-cased version to cope with
    servers that normalise differently.
    """
    # Prefer direct lookup with common casings
    for key in (name, name.title(), name.upper(), name.lower()):
        val = headers.get(key)
        if val is not None:
            return val
    # Fallback: linear scan
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


def _extract_session_prefix(path: str, base_path: str) -> str | None:
    """Extract the first path segment after ``base_path``.

    Example::

        base_path = "/hooks"
        path      = "/hooks/myprefix/some-event"
        → "myprefix"

    Returns ``None`` if the path does not start with ``base_path`` or if
    there is no segment after the base.
    """
    # Normalise trailing slash on base_path for the comparison
    bp = base_path.rstrip("/")
    if not path.startswith(bp):
        return None
    remainder = path[len(bp):]
    if not remainder.startswith("/"):
        return None
    # Strip the leading slash and take the first segment
    parts = remainder.lstrip("/").split("/")
    first = parts[0] if parts else ""
    return first or None
