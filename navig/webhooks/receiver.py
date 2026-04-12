"""Webhook receiver for external event triggers."""

import json
import uuid
from collections.abc import Callable

try:
    from aiohttp import web as _aiohttp_web
except ImportError:  # aiohttp is optional
    _aiohttp_web = None  # type: ignore[assignment]
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from navig.debug_logger import get_debug_logger

from .signatures import (
    SignatureConfig,
    extract_event_type,
    verify_github_signature,
    verify_signature,
    verify_stripe_signature,
)

logger = get_debug_logger()


@dataclass
class WebhookEvent:
    """Received webhook event."""

    id: str
    source: str
    event_type: str
    payload: dict[str, Any]
    headers: dict[str, str]
    received_at: datetime
    signature_valid: bool | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "source": self.source,
            "event_type": self.event_type,
            "payload": self.payload,
            "received_at": self.received_at.isoformat(),
            "signature_valid": self.signature_valid,
        }


@dataclass
class WebhookSourceConfig:
    """Configuration for a webhook source."""

    name: str
    enabled: bool = True
    secret: str | None = None
    signature_header: str | None = None
    signature_algo: str = "sha256"
    events: list[str] | None = None  # Allowed events (None = all)
    verify_signature: bool = True

    def get_signature_config(self) -> SignatureConfig | None:
        """Get signature config for this source."""
        if not self.verify_signature or not self.signature_header:
            return None

        return SignatureConfig(
            header=self.signature_header,
            algorithm=self.signature_algo,
        )


class WebhookReceiver:
    """
    Receives and processes incoming webhooks.

    Features:
    - Signature verification per provider (GitHub, Stripe, etc.)
    - Event filtering by type
    - Event routing to handlers
    - Integration with Gateway system events

    Example:
        receiver = WebhookReceiver(config)

        @receiver.on_event
        async def handle_event(event: WebhookEvent):
            print(f"Received: {event.source}/{event.event_type}")

        # In aiohttp app:
        app.router.add_routes(receiver.get_routes())
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.webhook_config = config.get("webhooks", {})
        self.enabled = self.webhook_config.get("enabled", True)
        self.path_prefix = self.webhook_config.get("path_prefix", "/webhook")

        # Load source configs
        self._sources: dict[str, WebhookSourceConfig] = {}
        self._load_sources()

        # Event handlers
        self._handlers: list[Callable] = []

        # Event history (for debugging)
        self._recent_events: list[WebhookEvent] = []
        self._max_history = 100

    def _load_sources(self):
        """Load webhook source configurations."""
        import os

        secrets = self.webhook_config.get("secrets", {})
        sources = self.webhook_config.get("sources", {})

        # Default sources if not configured
        if not sources:
            sources = {
                "github": {
                    "enabled": True,
                    "signature_header": "X-Hub-Signature-256",
                    "signature_algo": "sha256",
                },
                "stripe": {
                    "enabled": True,
                    "signature_header": "Stripe-Signature",
                },
                "gitlab": {
                    "enabled": True,
                    "signature_header": "X-Gitlab-Token",
                    "signature_algo": "plain",
                },
                "custom": {
                    "enabled": True,
                    "verify_signature": False,
                },
            }

        for name, cfg in sources.items():
            # Resolve secret from secrets dict or environment
            secret = secrets.get(name)
            if secret and isinstance(secret, str):
                if secret.startswith("${") and secret.endswith("}"):
                    env_var = secret[2:-1]
                    secret = os.environ.get(env_var)

            self._sources[name] = WebhookSourceConfig(
                name=name,
                enabled=cfg.get("enabled", True),
                secret=secret,
                signature_header=cfg.get("signature_header"),
                signature_algo=cfg.get("signature_algo", "sha256"),
                events=cfg.get("events"),
                verify_signature=cfg.get("verify_signature", True),
            )

    def on_event(self, handler: Callable):
        """
        Register an event handler.

        Can be used as decorator:
            @receiver.on_event
            async def handle(event):
                ...
        """
        self._handlers.append(handler)
        return handler

    def get_routes(self):
        """
        Get aiohttp routes for webhook endpoints.

        Returns list of route definitions.
        """
        try:
            from aiohttp import web
        except ImportError as _exc:
            raise ImportError(
                "aiohttp required for webhook receiver: pip install aiohttp"
            ) from _exc

        return [
            web.post(f"{self.path_prefix}/{{source}}", self.handle_webhook),  # noqa: F821
            web.get(f"{self.path_prefix}/status", self.handle_status),  # noqa: F821
            web.get(f"{self.path_prefix}/history", self.handle_history),  # noqa: F821
        ]

    async def handle_webhook(self, request) -> Any:
        """
        Handle incoming webhook request.

        Route: POST /webhook/{source}
        """
        from aiohttp import web

        if not self.enabled:
            return web.json_response({"error": "Webhooks disabled"}, status=503)

        source = request.match_info.get("source")

        # Check if source is configured
        source_cfg = self._sources.get(source)
        if not source_cfg:
            logger.warning("Webhook from unknown source: %s", source)
            return web.json_response({"error": "Unknown source"}, status=404)

        if not source_cfg.enabled:
            return web.json_response({"error": "Source disabled"}, status=403)

        # Read body
        try:
            body = await request.read()
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Verify signature
        signature_valid = None
        if source_cfg.verify_signature:
            if not source_cfg.secret:
                # verify_signature=True but no secret configured — reject to
                # avoid silently accepting unauthenticated events.
                logger.warning(
                    "Webhook source '%s' requires signature but has no secret configured",
                    source,
                )
                return web.json_response({"error": "Source misconfigured: no secret"}, status=500)

            signature_valid = self._verify_signature(
                source, body, dict(request.headers), source_cfg
            )

            if not signature_valid:
                logger.warning("Invalid webhook signature from %s", source)
                return web.json_response({"error": "Invalid signature"}, status=401)

        # Extract event type
        event_type = extract_event_type(source, dict(request.headers), payload)

        # Check if event is allowed
        if source_cfg.events and event_type not in source_cfg.events:
            logger.debug("Ignoring event %s from %s (not in allowed list)", event_type, source)
            return web.json_response({"ok": True, "ignored": True})

        # Create event
        event = WebhookEvent(
            id=str(uuid.uuid4())[:8],
            source=source,
            event_type=event_type,
            payload=payload,
            headers=dict(request.headers.items()),
            received_at=datetime.now(),
            signature_valid=signature_valid,
        )

        # Store in history
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_history:
            self._recent_events.pop(0)

        # Process event
        await self._process_event(event)

        logger.info("Webhook received: %s/%s (id=%s)", source, event_type, event.id)

        return web.json_response(
            {
                "ok": True,
                "event_id": event.id,
                "event_type": event_type,
            }
        )

    async def handle_status(self, request) -> Any:
        """
        Get webhook receiver status.

        Route: GET /webhook/status
        """
        from aiohttp import web

        sources = []
        for name, cfg in self._sources.items():
            sources.append(
                {
                    "name": name,
                    "enabled": cfg.enabled,
                    "has_secret": cfg.secret is not None,
                    "verify_signature": cfg.verify_signature,
                }
            )

        return web.json_response(
            {
                "enabled": self.enabled,
                "path_prefix": self.path_prefix,
                "sources": sources,
                "recent_events_count": len(self._recent_events),
            }
        )

    async def handle_history(self, request) -> Any:
        """
        Get recent webhook events.

        Route: GET /webhook/history
        """
        from aiohttp import web

        try:
            limit = int(request.query.get("limit", 20))
        except (ValueError, TypeError):
            limit = 20
        source_filter = request.query.get("source")

        events = self._recent_events[-limit:]

        if source_filter:
            events = [e for e in events if e.source == source_filter]

        return web.json_response(
            {
                "events": [e.to_dict() for e in reversed(events)],
                "total": len(events),
            }
        )

    def _verify_signature(
        self,
        source: str,
        body: bytes,
        headers: dict,
        config: WebhookSourceConfig,
    ) -> bool:
        """Verify webhook signature based on source."""
        source_lower = source.lower()

        # Get signature from headers (case-insensitive)
        signature = None
        if config.signature_header:
            for key, value in headers.items():
                if key.lower() == config.signature_header.lower():
                    signature = value
                    break

        if not signature:
            logger.warning("No signature header found for %s", source)
            return False

        # Use source-specific verification
        if source_lower == "github":
            return verify_github_signature(body, signature, config.secret)

        if source_lower == "stripe":
            return verify_stripe_signature(body, signature, config.secret)

        # Generic verification
        sig_config = config.get_signature_config()
        if sig_config:
            return verify_signature(body, signature, config.secret, sig_config)

        return True  # No verification configured

    async def _process_event(self, event: WebhookEvent):
        """Process webhook event by calling all handlers."""
        import asyncio

        for handler in self._handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error("Webhook handler error: %s", e)

    def add_source(self, config: WebhookSourceConfig):
        """Add a new webhook source configuration."""
        self._sources[config.name] = config

    def remove_source(self, name: str):
        """Remove a webhook source configuration."""
        self._sources.pop(name, None)

    def get_recent_events(self, limit: int = 20, source: str | None = None) -> list[WebhookEvent]:
        """Get recent events, optionally filtered by source."""
        events = self._recent_events[-limit:]
        if source:
            events = [e for e in events if e.source == source]
        return list(reversed(events))
