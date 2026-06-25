"""Webhook signature verification for various providers."""

import hashlib
import hmac
from dataclasses import dataclass

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


@dataclass
class SignatureConfig:
    """Signature verification configuration."""

    header: str  # Header name containing signature
    algorithm: str = "sha256"  # sha256, sha1
    prefix: str = ""  # e.g., "sha256=" for GitHub

    @classmethod
    def for_github(cls) -> "SignatureConfig":
        """GitHub webhook signature config."""
        return cls(
            header="X-Hub-Signature-256",
            algorithm="sha256",
            prefix="sha256=",
        )

    @classmethod
    def for_stripe(cls) -> "SignatureConfig":
        """Stripe webhook signature config."""
        return cls(
            header="Stripe-Signature",
            algorithm="sha256",
            prefix="",
        )

    @classmethod
    def for_gitlab(cls) -> "SignatureConfig":
        """GitLab webhook signature config."""
        return cls(
            header="X-Gitlab-Token",
            algorithm="plain",  # Just token comparison
            prefix="",
        )


def verify_signature(
    body: bytes,
    signature: str,
    secret: str,
    config: SignatureConfig,
) -> bool:
    """
    Verify webhook signature.

    Args:
        body: Raw request body
        signature: Signature from header
        secret: Webhook secret
        config: Signature configuration

    Returns:
        True if signature is valid
    """
    if not signature:
        logger.warning("No signature provided")
        return False

    if not secret:
        logger.warning("No secret configured")
        return False

    # Handle plain token comparison (GitLab style)
    if config.algorithm == "plain":
        return hmac.compare_digest(signature, secret)

    # Remove prefix if present
    if config.prefix and signature.startswith(config.prefix):
        signature = signature[len(config.prefix) :]

    # Compute expected signature
    if config.algorithm == "sha256":
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    elif config.algorithm == "sha1":
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha1,
        ).hexdigest()
    else:
        logger.error("Unknown signature algorithm: %s", config.algorithm)
        return False

    # Constant-time comparison
    return hmac.compare_digest(expected.lower(), signature.lower())


def verify_github_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    return verify_signature(body, signature, secret, SignatureConfig.for_github())


def verify_twilio_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    """Verify Twilio's ``X-Twilio-Signature`` (P-F).

    Twilio's scheme: HMAC-SHA1, keyed by the account auth token, over the exact
    request URL Twilio POSTed to **concatenated with each POST param** (sorted by
    key, ``key+value`` with no separator), then base64. The *url* must be the
    public webhook URL Twilio was configured with (reconstruct from forwarded
    headers behind a tunnel) — a wrong URL fails every legitimate request, which
    is why the caller gates this behind an explicit opt-in.
    """
    import base64
    import hashlib

    if not auth_token or not signature:
        return False
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key])
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def verify_stripe_signature(
    body: bytes,
    signature_header: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """
    Verify Stripe webhook signature.

    Stripe uses a custom format: t=timestamp,v1=signature
    """
    if not signature_header:
        return False

    # Parse Stripe signature header
    parts = {}
    for item in signature_header.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parts[key] = value

    timestamp = parts.get("t")
    signature = parts.get("v1")

    if not timestamp or not signature:
        return False

    # Verify timestamp is not too old
    import time

    try:
        ts = int(timestamp)
        if abs(time.time() - ts) > tolerance:
            logger.warning("Stripe signature timestamp out of tolerance")
            return False
    except ValueError:
        return False

    # Compute expected signature
    signed_payload = f"{timestamp}.{body.decode()}"
    expected = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def extract_event_type(source: str, headers: dict, payload: dict) -> str:
    """
    Extract event type from webhook request.

    Args:
        source: Webhook source (github, stripe, etc.)
        headers: Request headers
        payload: Request body as dict

    Returns:
        Event type string
    """
    source_lower = source.lower()

    if source_lower == "github":
        return headers.get("X-GitHub-Event", headers.get("x-github-event", "unknown"))

    if source_lower == "gitlab":
        return headers.get("X-Gitlab-Event", headers.get("x-gitlab-event", "unknown"))

    if source_lower == "stripe":
        return payload.get("type", "unknown")

    if source_lower == "slack":
        return payload.get("event", {}).get("type", payload.get("type", "unknown"))

    # Generic extraction
    return payload.get("event_type", payload.get("event", payload.get("type", "unknown")))
