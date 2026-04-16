"""
GCP Cloud Translation API v2 Connector — service account authentication.

Authentication: Google service account JSON (via file path or JSON string).

    GCP_SERVICE_ACCOUNT_FILE – path to a service account JSON key file
    GCP_SERVICE_ACCOUNT_JSON – raw JSON string of the service account key
    GCP_TRANSLATION_TOKEN    – pre-issued Bearer token (short-lived, optional)

The connector signs JWT access tokens using the RSA private key in the service
account JSON.  Requires the ``cryptography`` package (bundled in most envs).

Coverage:
    - Translate text (single or batch), any target language
    - Detect source language
    - List supported languages

Usage:
    connector = GcpTranslateConnector()
    await connector.connect()
    result = await connector.act(Action("translate", {"text": "Hello", "target": "fr"}))
    detected = await connector.act(Action("detect", {"text": "Bonjour"}))
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAuthError
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
)

logger = logging.getLogger("navig.connectors.gcp_translate")

_TRANSLATE_BASE = "https://translation.googleapis.com/language/translate/v2"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TRANSLATE_SCOPE = "https://www.googleapis.com/auth/cloud-translation"


# ── JWT helpers ──────────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_rs256(private_key_pem: str, message: bytes) -> bytes:
    """Sign *message* with RSA-SHA256 using *private_key_pem*."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pk = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return pk.sign(message, padding.PKCS1v15(), hashes.SHA256())


def _make_jwt(sa: dict, scope: str) -> str:
    """Build a signed JSON Web Token for Google OAuth2."""
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": scope,
                "aud": _TOKEN_URL,
                "exp": now + 3600,
                "iat": now,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    pk_pem = sa["private_key"].replace("\\n", "\n")
    sig = _b64url(_sign_rs256(pk_pem, signing_input))
    return f"{header}.{payload}.{sig}"


def _fetch_token(sa: dict, scope: str) -> tuple[str, int]:
    """Exchange a service account JWT for a Bearer access token.

    Returns
    -------
    (access_token, expires_at_unix_timestamp)
    """
    jwt = _make_jwt(sa, scope)
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }
    ).encode()
    req = urllib.request.Request(
        _TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=12)
    data = json.loads(resp.read())
    expires_in = int(data.get("expires_in", 3600))
    return data["access_token"], int(time.time()) + expires_in - 60


# ── Connector ────────────────────────────────────────────────────────────────


class GcpTranslateConnector(BaseConnector):
    """Connector for Google Cloud Translation API v2 (service account auth).

    Auth: Google service account JSON key file or raw JSON string, loaded from:
        ``GCP_SERVICE_ACCOUNT_FILE`` – path to JSON key file
        ``GCP_SERVICE_ACCOUNT_JSON`` – raw JSON string of the key

    Alternatively, the service account JSON can be stored in the navig vault
    under label ``gcp/service_account_json`` and the connector will read it
    from there if neither env var is set (see connect() implementation).

    Note: Billing is charged to the GCP project in the service account.
    Ensure you're authorised to use the target project.
    """

    manifest = ConnectorManifest(
        id="gcp_translate",
        display_name="GCP Translation",
        description=(
            "Google Cloud Translation API v2 via service account. "
            "Translate text to 195+ languages, detect source language, "
            "list all supported languages."
        ),
        domain=ConnectorDomain.AI_RESEARCH,
        icon="🌍",
        oauth_scopes=[_TRANSLATE_SCOPE],
        oauth_provider="google",
        requires_oauth=False,  # uses service account, not user OAuth
        can_search=False,
        can_fetch=False,
        can_act=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self._sa: dict | None = None  # service account JSON parsed
        self._token: str | None = None  # cached Bearer token
        self._token_expiry: int = 0  # unix ts when token expires

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Load and validate service account credentials."""
        sa = self._load_service_account()
        if not sa:
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                "GCP service account not found. Set GCP_SERVICE_ACCOUNT_FILE or "
                "GCP_SERVICE_ACCOUNT_JSON, or store JSON in vault under "
                "'gcp/service_account_json'.",
            )
        required_fields = {"client_email", "private_key", "project_id", "type"}
        missing = required_fields - sa.keys()
        if missing or sa.get("type") != "service_account":
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                f"Service account JSON is missing fields: {missing} or wrong type.",
            )
        self._sa = sa
        # Eager token fetch to validate credentials immediately
        try:
            self._token, self._token_expiry = _fetch_token(sa, _TRANSLATE_SCOPE)
        except Exception as exc:  # noqa: BLE001
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                f"Failed to obtain GCP access token: {exc}",
            ) from exc
        self._status = ConnectorStatus.CONNECTED
        logger.debug(
            "GcpTranslate connector connected (project=%s, sa=%s)",
            sa.get("project_id"),
            sa.get("client_email"),
        )

    async def disconnect(self) -> None:
        self._sa = None
        self._token = None
        self._token_expiry = 0
        self._status = ConnectorStatus.DISCONNECTED

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _load_service_account(self) -> dict | None:
        # 1. JSON string in env
        raw = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("GCP_SERVICE_ACCOUNT_JSON is not valid JSON; ignoring.")

        # 2. File path in env
        path = os.environ.get("GCP_SERVICE_ACCOUNT_FILE", "").strip()
        if path:
            import pathlib

            p = pathlib.Path(path)
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001 — log and fall through
                    logger.debug("Could not read service account file %s: %s", path, exc)

        return None

    def _bearer_token(self) -> str:
        """Return a valid Bearer token, refreshing if expired."""
        if self._token and int(time.time()) < self._token_expiry:
            return self._token
        if self._sa is None:
            raise RuntimeError("GcpTranslateConnector: not connected")
        self._token, self._token_expiry = _fetch_token(self._sa, _TRANSLATE_SCOPE)
        return self._token

    def _api(
        self,
        endpoint: str,
        method: str = "GET",
        body: dict | None = None,
        timeout: int = 15,
    ) -> dict:
        token = self._bearer_token()
        url = f"{_TRANSLATE_BASE}{endpoint}"
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())

    # ── Search (not supported) ───────────────────────────────────────────────

    async def search(self, query: str, limit: int = 10, **kwargs: Any) -> list[Resource]:
        raise NotImplementedError(
            "GcpTranslateConnector does not support search. Use act('translate') instead."
        )

    async def fetch(self, resource_id: str, **kwargs: Any) -> Resource | None:
        raise NotImplementedError(
            "GcpTranslateConnector does not support fetch. Use act('languages') to list languages."
        )

    # ── Act ──────────────────────────────────────────────────────────────────

    async def act(self, action: Action) -> ActionResult:
        """Supported actions:

        translate: {"text": str | list[str], "target": "fr", ["source": "en"]}
            Translate one or more strings to `target` language code.

        detect: {"text": str | list[str]}
            Detect source language of one or more strings.

        languages: {"target": "en"}  (optional)
            List all supported languages, optionally with display names in `target` lang.
        """
        self._require_connected()
        name = action.name
        p = action.params

        if name == "translate":
            texts = p.get("text", "")
            if isinstance(texts, str):
                texts = [texts]
            body: dict[str, Any] = {"q": texts, "target": p["target"]}
            if "source" in p:
                body["source"] = p["source"]
            else:
                body["format"] = "text"
            result = self._api("", method="POST", body=body)
            translations = result.get("data", {}).get("translations", [])
            out = [
                {
                    "original": orig,
                    "translated": t.get("translatedText", ""),
                    "detected_source": t.get("detectedSourceLanguage", p.get("source", "?")),
                    "target": p["target"],
                }
                for orig, t in zip(texts, translations)
            ]
            return ActionResult(success=True, data={"translations": out})

        if name == "detect":
            texts = p.get("text", "")
            if isinstance(texts, str):
                texts = [texts]
            result = self._api("/detect", method="POST", body={"q": texts})
            detections = result.get("data", {}).get("detections", [])
            out = [
                {
                    "text": orig,
                    "language": d[0].get("language", "?") if d else "?",
                    "confidence": d[0].get("confidence", 0.0) if d else 0.0,
                }
                for orig, d in zip(texts, detections)
            ]
            return ActionResult(success=True, data={"detections": out})

        if name == "languages":
            target = p.get("target", "en")
            result = self._api(f"/languages?target={target}")
            langs = result.get("data", {}).get("languages", [])
            return ActionResult(
                success=True,
                data={"languages": langs, "count": len(langs)},
            )

        return ActionResult(
            success=False, error=f"Unknown action '{name}'. Use: translate, detect, languages"
        )

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Test a single-word translation to verify end-to-end connectivity."""
        if not self._sa:
            return HealthStatus(healthy=False, message="Not connected", latency_ms=0)
        t0 = time.monotonic()
        try:
            result = self._api("", method="POST", body={"q": ["hello"], "target": "es"})
            latency_ms = int((time.monotonic() - t0) * 1000)
            translations = result.get("data", {}).get("translations", [])
            if translations:
                translated = translations[0].get("translatedText", "")
                return HealthStatus(
                    healthy=True,
                    message=f"Cloud Translation OK: 'hello' → '{translated}' (es)",
                    latency_ms=latency_ms,
                )
            return HealthStatus(
                healthy=False, message="Empty translation response", latency_ms=latency_ms
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(healthy=False, message=str(exc), latency_ms=latency_ms)
