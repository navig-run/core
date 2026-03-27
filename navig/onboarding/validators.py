"""
Integration validators — live connection tests for onboarding steps.

Each function performs the cheapest possible real check:
  - Matrix:     GET /_matrix/client/v3/account/whoami
  - Telegram:   GET /bot<token>/getMe
  - SMTP:       EHLO handshake (no AUTH, no credentials transmitted)
  - Twitter/X:  POST /oauth2/token (app-only bearer exchange)
  - LinkedIn:   GET /v2/me
  - Mastodon:   GET /api/v1/accounts/verify_credentials

All functions return `ValidationResult(ok, errors, info)` — never raise.
Network timeouts produce ok=False with the standard offline message.
Timeout policy: 5 s connect, 8 s read — tight enough for onboarding UX.
"""

from __future__ import annotations

import smtplib
import socket
from dataclasses import dataclass, field

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 8.0


@dataclass
class ValidationResult:
    ok: bool
    errors: list[dict[str, str]] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @classmethod
    def success(cls, **info) -> "ValidationResult":
        return cls(ok=True, info=info)

    @classmethod
    def failure(cls, field_: str, message: str) -> "ValidationResult":
        return cls(ok=False, errors=[{"field": field_, "message": message}])

    @classmethod
    def timeout(cls, service: str) -> "ValidationResult":
        return cls(
            ok=False,
            errors=[
                {
                    "field": "connection",
                    "message": (
                        f"Could not reach {service}. "
                        "Check your internet connection or skip and configure later."
                    ),
                }
            ],
        )


def _http_client():
    """Lazy-imported httpx.Client with a standard timeout."""
    import httpx  # noqa: PLC0415

    return httpx.Client(
        timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
        follow_redirects=True,
    )


# ── Matrix ─────────────────────────────────────────────────────────────────


def validate_matrix(homeserver_url: str, token: str) -> ValidationResult:
    """
    Validate a Matrix homeserver URL + access token.

    Sends GET /_matrix/client/v3/account/whoami with Bearer token.
    Returns ValidationResult.success(user_id=...) on success.
    """
    if not homeserver_url:
        return ValidationResult.failure("homeserver_url", "Homeserver URL is required.")
    if not token:
        return ValidationResult.failure("access_token", "Access token is required.")

    url = homeserver_url.rstrip("/") + "/_matrix/client/v3/account/whoami"
    try:
        with _http_client() as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            data = resp.json()
            return ValidationResult.success(user_id=data.get("user_id", ""))
        if resp.status_code == 401:
            try:
                detail = resp.json().get("error", "Invalid access token")
            except Exception:  # noqa: BLE001
                detail = "Invalid access token"
            return ValidationResult.failure("access_token", detail)
        if resp.status_code == 404:
            return ValidationResult.failure(
                "homeserver_url",
                "Matrix client endpoint not found. Verify the homeserver URL (include https://).",
            )
        return ValidationResult.failure(
            "homeserver_url",
            f"Unexpected response: HTTP {resp.status_code}",
        )
    except Exception as exc:  # noqa: BLE001
        s = str(exc).lower()
        if "timeout" in s or "connect" in s or "network" in s:
            return ValidationResult.timeout("Matrix homeserver")
        return ValidationResult.failure("homeserver_url", str(exc)[:120])


# ── Telegram ───────────────────────────────────────────────────────────────


def validate_telegram(token: str) -> ValidationResult:
    """
    Validate a Telegram bot token via GET /bot<token>/getMe.

    Returns ValidationResult.success(username=..., first_name=...) on success.
    """
    if not token:
        return ValidationResult.failure("bot_token", "Bot token is required.")

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with _http_client() as client:
            resp = client.get(url)
        data = resp.json()
        if data.get("ok"):
            result = data.get("result", {})
            return ValidationResult.success(
                username=result.get("username", ""),
                first_name=result.get("first_name", ""),
            )
        description = data.get("description", "Invalid bot token")
        return ValidationResult.failure("bot_token", description)
    except Exception as exc:  # noqa: BLE001
        s = str(exc).lower()
        if "timeout" in s or "connect" in s or "network" in s:
            return ValidationResult.timeout("Telegram API")
        return ValidationResult.failure("bot_token", str(exc)[:120])


# ── Email / SMTP ───────────────────────────────────────────────────────────


def validate_smtp(host: str, port: str | int) -> ValidationResult:
    """
    Validate SMTP host reachability via EHLO handshake.

    No credentials are transmitted — this confirms the host is reachable
    and speaks SMTP.  Port is coerced to int.
    """
    if not host:
        return ValidationResult.failure("smtp_host", "SMTP host is required.")
    try:
        port_int = int(port) if port else 587
    except (ValueError, TypeError):
        return ValidationResult.failure("smtp_port", f"Invalid port: {port!r}")

    try:
        with smtplib.SMTP(host, port_int, timeout=_CONNECT_TIMEOUT) as conn:
            code, msg = conn.ehlo()
            if 200 <= code < 300:
                return ValidationResult.success(
                    host=host,
                    port=port_int,
                    banner=msg.decode("utf-8", errors="replace")[:80],
                )
            return ValidationResult.failure(
                "smtp_host",
                f"EHLO rejected: {code} {msg.decode('utf-8', errors='replace')[:60]}",
            )
    except socket.timeout:
        return ValidationResult.timeout(f"SMTP server {host}:{port_int}")
    except ConnectionRefusedError:
        return ValidationResult.failure(
            "smtp_host",
            f"Connection refused on {host}:{port_int}. Check host and port.",
        )
    except smtplib.SMTPException as exc:
        return ValidationResult.failure("smtp_host", str(exc)[:120])
    except OSError as exc:
        s = str(exc).lower()
        if "timed out" in s or "timeout" in s:
            return ValidationResult.timeout(f"SMTP server {host}:{port_int}")
        return ValidationResult.failure("smtp_host", str(exc)[:120])


# ── Twitter / X ────────────────────────────────────────────────────────────


def validate_twitter(api_key: str, api_secret: str) -> ValidationResult:
    """
    Validate Twitter/X app credentials by exchanging them for a bearer token.

    POST https://api.twitter.com/oauth2/token (app-only OAuth 2).
    """
    if not api_key:
        return ValidationResult.failure("api_key", "API key is required.")
    if not api_secret:
        return ValidationResult.failure("api_secret", "API secret is required.")

    try:
        with _http_client() as client:
            resp = client.post(
                "https://api.twitter.com/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(api_key, api_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return ValidationResult.success(token_type=data.get("token_type", "bearer"))
        try:
            err = resp.json()
            msg = err.get("errors", [{}])[0].get("message", f"HTTP {resp.status_code}")
        except Exception:  # noqa: BLE001
            msg = f"HTTP {resp.status_code}"
        return ValidationResult.failure("api_key", msg)
    except Exception as exc:  # noqa: BLE001
        s = str(exc).lower()
        if "timeout" in s or "connect" in s or "network" in s:
            return ValidationResult.timeout("Twitter/X API")
        return ValidationResult.failure("api_key", str(exc)[:120])


# ── LinkedIn ───────────────────────────────────────────────────────────────


def validate_linkedin(access_token: str) -> ValidationResult:
    """
    Validate a LinkedIn access token via GET /v2/me.

    Returns ValidationResult.success(name=...) on success.
    """
    if not access_token:
        return ValidationResult.failure(
            "access_token", "LinkedIn access token is required."
        )

    try:
        with _http_client() as client:
            resp = client.get(
                "https://api.linkedin.com/v2/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            name = (
                data.get("localizedFirstName", "")
                + " "
                + data.get("localizedLastName", "")
            ).strip()
            return ValidationResult.success(name=name or "(unknown)")
        try:
            err = resp.json()
            msg = err.get("message", f"HTTP {resp.status_code}")
        except Exception:  # noqa: BLE001
            msg = f"HTTP {resp.status_code}"
        return ValidationResult.failure("access_token", msg)
    except Exception as exc:  # noqa: BLE001
        s = str(exc).lower()
        if "timeout" in s or "connect" in s or "network" in s:
            return ValidationResult.timeout("LinkedIn API")
        return ValidationResult.failure("access_token", str(exc)[:120])


# ── Mastodon ───────────────────────────────────────────────────────────────


def validate_mastodon(instance_url: str, access_token: str) -> ValidationResult:
    """
    Validate a Mastodon instance URL + access token.

    GET {instance_url}/api/v1/accounts/verify_credentials
    Returns ValidationResult.success(username=..., display_name=...) on success.
    """
    if not instance_url:
        return ValidationResult.failure("instance_url", "Instance URL is required.")
    if not access_token:
        return ValidationResult.failure("access_token", "Access token is required.")

    url = instance_url.rstrip("/") + "/api/v1/accounts/verify_credentials"
    try:
        with _http_client() as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 200:
            data = resp.json()
            return ValidationResult.success(
                username=data.get("username", ""),
                display_name=data.get("display_name", ""),
            )
        try:
            err = resp.json()
            msg = err.get("error", f"HTTP {resp.status_code}")
        except Exception:  # noqa: BLE001
            msg = f"HTTP {resp.status_code}"
        return ValidationResult.failure("access_token", msg)
    except Exception as exc:  # noqa: BLE001
        s = str(exc).lower()
        if "timeout" in s or "connect" in s or "network" in s:
            return ValidationResult.timeout(f"Mastodon instance {instance_url}")
        return ValidationResult.failure("instance_url", str(exc)[:120])
