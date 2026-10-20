"""
Licence auto-refresh — fetch the buyer's newest signed token from api.navig.run.

Why: every renewal (and every Bay purchase) mints a NEW signed token server-side
and emails it. The daemon only ever knew the token someone pasted, so a monthly
subscriber dropped to Free 31 days after their last paste even though they had
paid — unless they re-pasted every month.

How: POST the current token to ``/api/license/refresh``. Possessing a signed
token is the credential (its ``license_id`` is an unguessable UUID the server
looks up); the server answers with the newest live token for the same buyer.
The answer is only adopted if it VERIFIES OFFLINE against the bundled public
keys — the server cannot hand the daemon an entitlement it did not sign — and
it goes through :func:`navig.license.paste_license`, which never overwrites a
working licence with an invalid one.

Never raises: a network failure, a 404 (older Worker), or a malformed reply
leaves the existing licence exactly as it was.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.navig.run"
REFRESH_PATH = "/api/license/refresh"
# Twice a day: a renewal lands within hours, and the call is one small POST.
INTERVAL_SECONDS = 12 * 3600

Outcome = Literal["updated", "unchanged", "skipped", "unreachable", "rejected"]


@dataclass(frozen=True)
class RefreshResult:
    outcome: Outcome
    detail: str = ""
    tier: str | None = None


def _base_url() -> str:
    try:
        from navig.config import get_config_manager

        base = get_config_manager().get("cloud.broker_url", DEFAULT_BASE) or DEFAULT_BASE
    except Exception:  # noqa: BLE001
        base = DEFAULT_BASE
    return str(base).rstrip("/")


def auto_refresh_enabled() -> bool:
    """``license.auto_refresh`` (default on). Uses the canonical config coercion,
    so ``navig config set license.auto_refresh false`` really turns it off."""
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        return coerce_bool(get_config_manager().get("license.auto_refresh", True), default=True)
    except Exception:  # noqa: BLE001
        return True


def refresh_once(*, timeout: float = 10.0) -> RefreshResult:
    """Ask the licence server for a newer token; adopt it if it verifies."""
    from navig.license import current_status, paste_license, read_raw_token
    from navig.license.keys import verify_license

    token = read_raw_token()
    if not token:
        return RefreshResult("skipped", "no licence installed")
    if current_status().license_id == "dev-override":
        return RefreshResult("skipped", "dev override active")

    try:
        import httpx

        resp = httpx.post(f"{_base_url()}{REFRESH_PATH}", json={"token": token}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — offline is normal; try again next tick
        return RefreshResult("unreachable", f"{type(exc).__name__}")

    if resp.status_code == 404:
        # Unknown token (e.g. a hand-signed dev/comp licence) or an older Worker.
        return RefreshResult("unchanged", "licence server does not know this token")
    if resp.status_code >= 400:
        return RefreshResult("unreachable", f"HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return RefreshResult("unreachable", "malformed reply")

    new_token = body.get("token") if isinstance(body, dict) else None
    if not body.get("changed") or not isinstance(new_token, str) or new_token.strip() == token:
        return RefreshResult("unchanged")

    candidate = verify_license(new_token)
    if not candidate.valid:
        # Never trust the server's word over the signature.
        logger.warning("licence refresh: server offered a token that does not verify (%s)", candidate.reason)
        return RefreshResult("rejected", candidate.reason)

    status = paste_license(new_token)
    if not status.valid:
        return RefreshResult("rejected", status.reason)
    logger.info("licence refreshed: now %s", status.effective_tier)
    return RefreshResult("updated", tier=status.effective_tier)
