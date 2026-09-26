"""One place that answers "is the user-account side working?".

This used to live as a second ``navig telegram status`` command registered onto
the same Typer app as the bot one. Typer keeps the last registration, so the
bot status shadowed it and the MTProto half was unreachable — which is exactly
the information missing when a vault engine goes absent and every credential
reads as unset. Rendering it from a function lets the one surviving command
show both transports.
"""
from __future__ import annotations


def collect() -> dict:
    """Facts about the MTProto side. Never raises; the caller renders."""
    from navig.telegram import config as tgcfg
    from navig.telegram import telethon_available

    out: dict = {
        "telethon": bool(telethon_available()),
        "vault_error": None,
        "credentials": False,
        "logged_in": False,
        "me": None,
        "authorized": None,
    }
    # A missing vault engine makes every credential read as absent. Reporting
    # "not set" there sends people to re-run setup over intact credentials, so
    # the engine problem has to be named separately.
    try:
        out["vault_error"] = tgcfg.vault_unavailable_reason()
    except Exception:  # noqa: BLE001 — status must never be the thing that breaks
        out["vault_error"] = None

    try:
        out["credentials"] = tgcfg.have_api_credentials()
        out["logged_in"] = tgcfg.is_logged_in()
    except Exception:  # noqa: BLE001
        return out
    return out


def probe_account() -> dict | None:
    """Who the session belongs to, or None when it is not authorized."""
    import asyncio

    from navig.telegram import user_client
    try:
        return asyncio.run(user_client.whoami())
    except Exception:  # noqa: BLE001 — an unreachable account is a status, not a crash
        return None
