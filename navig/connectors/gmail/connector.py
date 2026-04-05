"""
Gmail Connector

Full CRUD connector for Google Gmail API v1.
Implements ``BaseConnector`` with search, fetch, and act (reply/send/
archive/label/delete).

All HTTP calls go through ``httpx.AsyncClient`` — no dependency on
``google-api-python-client``.
"""

from __future__ import annotations

import base64
import logging
import time
from email.mime.text import MIMEText
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAPIError
from navig.connectors.gmail.mappers import gmail_message_to_resource
from navig.connectors.types import (
    Action,
    ActionResult,
    ActionType,
    ConnectorDomain,
    HealthStatus,
    Resource,
)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

logger = logging.getLogger("navig.connectors.gmail")

_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(BaseConnector):
    """
    Gmail connector — search, read, send, label, archive.

    Requires OAuth scopes: gmail.readonly, gmail.send,
    gmail.modify, gmail.labels.
    """

    manifest = ConnectorManifest(
        id="gmail",
        display_name="Gmail",
        description="Search, read, send, and manage Gmail messages.",
        domain=ConnectorDomain.COMMUNICATION,
        icon="📧",
        oauth_scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.labels",
        ],
        oauth_provider="gmail",
        requires_oauth=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self._user_email: str | None = None

    # -- Helpers -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
        }

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET request to Gmail API with error handling."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code != 200:
                raise ConnectorAPIError("gmail", resp.status_code, resp.text[:200])
            return resp.json()

    async def _api_post(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST request to Gmail API."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_API_BASE}{path}",
                headers=self._headers(),
                json=json_body,
            )
            if resp.status_code not in (200, 201, 204):
                raise ConnectorAPIError("gmail", resp.status_code, resp.text[:200])
            return resp.json() if resp.content else {}

    # -- BaseConnector interface -------------------------------------------

    async def search(self, query: str, limit: int = 5) -> list[Resource]:
        """
        Search Gmail messages matching *query*.

        Uses the same query syntax as the Gmail search box:
        https://support.google.com/mail/answer/7190

        Args:
            query: Gmail search expression (subject:, from:, etc.)
            limit: Maximum number of messages to return (default 5).
        """
        # 1. Get message IDs
        data = await self._api_get(
            "/users/me/messages",
            params={"q": query, "maxResults": limit},
        )
        message_ids = [m["id"] for m in data.get("messages", [])]
        if not message_ids:
            return []

        # 2. Batch-fetch metadata for each message
        resources: list[Resource] = []
        for mid in message_ids[:limit]:  # cap to match requested limit
            try:
                msg = await self._api_get(
                    f"/users/me/messages/{mid}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["Subject", "From", "To", "Date", "Message-ID"],
                    },
                )
                resources.append(gmail_message_to_resource(msg))
            except ConnectorAPIError:
                logger.debug("Failed to fetch message %s", mid)
        return resources

    async def fetch(self, resource_id: str) -> Resource:
        """Fetch a single Gmail message by ID (full body)."""
        msg = await self._api_get(
            f"/users/me/messages/{resource_id}",
            params={"format": "full"},
        )
        resource = gmail_message_to_resource(msg)
        # Extract body text from payload
        body = self._extract_body(msg.get("payload", {}))
        if body:
            resource.preview = body[:500]
            resource.metadata["body"] = body
        return resource

    async def act(self, action: Action) -> ActionResult:
        """
        Execute a Gmail action.

        Supported action_types:
            SEND   — send a new email (params: to, subject, body)
            REPLY  — reply to a message (params: body; resource_id = message ID)
            ARCHIVE — remove INBOX label
            LABEL  — add/remove labels (params: add_labels, remove_labels)
            DELETE — move to trash
        """
        try:
            if action.action_type == ActionType.SEND:
                return await self._send(action)
            elif action.action_type == ActionType.REPLY:
                return await self._reply(action)
            elif action.action_type == ActionType.ARCHIVE:
                return await self._modify_labels(
                    action.resource_id or "",
                    remove=["INBOX"],
                )
            elif action.action_type == ActionType.LABEL:
                return await self._modify_labels(
                    action.resource_id or "",
                    add=action.params.get("add_labels", []),
                    remove=action.params.get("remove_labels", []),
                )
            elif action.action_type == ActionType.DELETE:
                return await self._trash(action.resource_id or "")
            else:
                return ActionResult(
                    success=False,
                    error=f"Unsupported action: {action.action_type.value}",
                )
        except ConnectorAPIError as exc:
            return ActionResult(success=False, error=str(exc))

    async def health_check(self) -> HealthStatus:
        """Check Gmail API availability by fetching user profile."""
        start = time.monotonic()
        try:
            data = await self._api_get("/users/me/profile")
            latency = (time.monotonic() - start) * 1000
            self._user_email = data.get("emailAddress")
            return HealthStatus(ok=True, latency_ms=latency)
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(ok=False, latency_ms=latency, message=str(exc))

    async def connect(self) -> None:
        """Validate token by fetching user profile."""
        await super().connect()
        try:
            health = await self.health_check()
            if not health.ok:
                logger.warning("Gmail health check failed on connect: %s", health.message)
        except Exception as exc:
            logger.debug("Health check on connect failed: %s", exc)

    # -- Private action implementations ------------------------------------

    async def _send(self, action: Action) -> ActionResult:
        """Send a new email."""
        to = action.params.get("to", "")
        subject = action.params.get("subject", "")
        body = action.params.get("body", "")

        mime = MIMEText(body, "plain")
        mime["To"] = to
        mime["Subject"] = subject
        if self._user_email:
            mime["From"] = self._user_email

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        data = await self._api_post("/users/me/messages/send", json_body={"raw": raw})
        return ActionResult(
            success=True,
            resource=Resource(
                id=data.get("id", ""),
                source="gmail",
                title=f"Sent: {subject}",
                preview=body[:200],
                url=f"https://mail.google.com/mail/#sent/{data.get('id', '')}",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )

    async def _reply(self, action: Action) -> ActionResult:
        """Reply to an existing message."""
        if not action.resource_id:
            return ActionResult(success=False, error="resource_id required for reply")

        # Fetch original to get thread ID and headers
        original = await self._api_get(
            f"/users/me/messages/{action.resource_id}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Message-ID"]},
        )
        payload = original.get("payload", {})
        headers = payload.get("headers", [])
        thread_id = original.get("threadId", "")

        subject = ""
        to_addr = ""
        message_id = ""
        for h in headers:
            name = h.get("name", "").lower()
            if name == "subject":
                subject = h.get("value", "")
            elif name == "from":
                to_addr = h.get("value", "")
            elif name == "message-id":
                message_id = h.get("value", "")

        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        body = action.params.get("body", "")
        mime = MIMEText(body, "plain")
        mime["To"] = to_addr
        mime["Subject"] = subject
        if message_id:
            mime["In-Reply-To"] = message_id
            mime["References"] = message_id
        if self._user_email:
            mime["From"] = self._user_email

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        data = await self._api_post(
            "/users/me/messages/send",
            json_body={"raw": raw, "threadId": thread_id},
        )
        return ActionResult(
            success=True,
            resource=Resource(
                id=data.get("id", ""),
                source="gmail",
                title=subject,
                preview=body[:200],
                url=f"https://mail.google.com/mail/#inbox/{data.get('id', '')}",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )

    async def _modify_labels(
        self,
        message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> ActionResult:
        """Add/remove labels on a message."""
        if not message_id:
            return ActionResult(success=False, error="message_id required")
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        await self._api_post(f"/users/me/messages/{message_id}/modify", json_body=body)
        return ActionResult(success=True)

    async def _trash(self, message_id: str) -> ActionResult:
        """Move a message to trash."""
        if not message_id:
            return ActionResult(success=False, error="message_id required")
        await self._api_post(f"/users/me/messages/{message_id}/trash")
        return ActionResult(success=True)

    # -- Body extraction ---------------------------------------------------

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        """Recursively extract plain-text body from a Gmail payload."""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                except Exception:
                    return ""

        # Recurse into parts
        for part in payload.get("parts", []):
            text = GmailConnector._extract_body(part)
            if text:
                return text
        return ""
