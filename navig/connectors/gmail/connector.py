"""
Gmail Connector

Full CRUD connector for Google Gmail API v1.
Implements ``BaseConnector`` with search, fetch, and act (reply/send/
archive/label/delete).

All HTTP calls go through ``httpx.AsyncClient`` — no dependency on
``google-api-python-client``.
"""

from __future__ import annotations

import asyncio
import base64
import html as _html
import logging
import re
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

# Google API request timeout in seconds.
_GOOGLE_API_TIMEOUT: float = 15.0

_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Gmail caps ``messages.list`` at 500 ids per page.
_LIST_PAGE_MAX = 500
# Metadata headers the mailroom needs for threading / triage in one round-trip.
DEFAULT_METADATA_HEADERS: tuple[str, ...] = (
    "Subject",
    "From",
    "To",
    "Cc",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
    # Stamped by a mailroom edge Worker before it forwards to the mailbox, so a Gmail-side
    # rule can key on the triage the edge already did (see the navig-email plugin).
    "X-Cybesis-Tag",
    "X-Cybesis-Alias",
)

_HTML_BLOCK_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_BREAK_RE = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6])\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """Cheap, dependency-free HTML -> readable text (spam mail is usually HTML-only)."""
    text = _HTML_BLOCK_RE.sub(" ", markup)
    text = _HTML_BREAK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _html.unescape(text).replace(" ", " ")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _NL_RE.sub("\n\n", text).strip()


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
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
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
                        "metadataHeaders": list(DEFAULT_METADATA_HEADERS),
                    },
                )
                resources.append(gmail_message_to_resource(msg))
            except ConnectorAPIError:
                logger.debug("Failed to fetch message %s", mid)
        return resources

    # -- Extended read API (mailroom) ---------------------------------------
    # Raw Gmail JSON in, raw JSON out: the ``Resource`` shape is for the generic
    # connector surface; the mailroom needs labelIds/threadId/internalDate and
    # whole threads, which ``Resource`` flattens away.

    async def get_profile(self) -> dict[str, Any]:
        """``users.getProfile`` — emailAddress, messagesTotal, threadsTotal, historyId."""
        data = await self._api_get("/users/me/profile")
        self._user_email = data.get("emailAddress") or self._user_email
        return data

    async def list_message_ids(
        self,
        query: str = "",
        *,
        max_results: int = _LIST_PAGE_MAX,
        page_token: str | None = None,
        label_ids: list[str] | None = None,
        include_spam_trash: bool = False,
    ) -> tuple[list[dict[str, str]], str | None]:
        """One ``messages.list`` page: ``([{id, threadId}, …], next_page_token)``.

        Ids only — cheap enough to count a whole month of mail. Gmail search
        syntax applies (``in:spam``, ``after:1700000000``, ``label:X`` …).
        """
        params: dict[str, Any] = {"maxResults": max(1, min(int(max_results), _LIST_PAGE_MAX))}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = label_ids
        if include_spam_trash:
            params["includeSpamTrash"] = "true"
        data = await self._api_get("/users/me/messages", params=params)
        entries = [
            {"id": m.get("id", ""), "threadId": m.get("threadId", "")}
            for m in data.get("messages", [])
            if m.get("id")
        ]
        return entries, data.get("nextPageToken") or None

    async def iter_message_ids(
        self,
        query: str = "",
        *,
        limit: int | None = None,
        label_ids: list[str] | None = None,
        include_spam_trash: bool = False,
    ) -> list[dict[str, str]]:
        """All ``{id, threadId}`` entries for *query*, paginating until *limit* (None = all)."""
        out: list[dict[str, str]] = []
        token: str | None = None
        while True:
            want = (
                _LIST_PAGE_MAX if limit is None else max(1, min(_LIST_PAGE_MAX, limit - len(out)))
            )
            page, token = await self.list_message_ids(
                query,
                max_results=want,
                page_token=token,
                label_ids=label_ids,
                include_spam_trash=include_spam_trash,
            )
            out.extend(page)
            if not token or not page or (limit is not None and len(out) >= limit):
                break
        return out[:limit] if limit is not None else out

    async def get_message(
        self,
        message_id: str,
        *,
        format: str = "metadata",
        headers: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """``messages.get`` raw JSON. ``format`` = metadata | full | minimal."""
        params: dict[str, Any] = {"format": format}
        if format == "metadata":
            params["metadataHeaders"] = list(headers or DEFAULT_METADATA_HEADERS)
        return await self._api_get(f"/users/me/messages/{message_id}", params=params)

    async def get_many(
        self,
        message_ids: list[str],
        *,
        format: str = "metadata",
        headers: list[str] | tuple[str, ...] | None = None,
        concurrency: int = 8,
    ) -> list[dict[str, Any]]:
        """Fetch many messages with bounded concurrency; failed ids are skipped (logged)."""
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(mid: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await self.get_message(mid, format=format, headers=headers)
                except ConnectorAPIError as exc:
                    logger.debug("get_many: skipping %s (%s)", mid, exc)
                    return None

        results = await asyncio.gather(*(_one(m) for m in message_ids))
        return [r for r in results if r]

    async def get_thread(
        self,
        thread_id: str,
        *,
        format: str = "metadata",
        headers: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """``threads.get`` raw JSON — every message of the thread with labelIds/internalDate."""
        params: dict[str, Any] = {"format": format}
        if format == "metadata":
            params["metadataHeaders"] = list(headers or DEFAULT_METADATA_HEADERS)
        return await self._api_get(f"/users/me/threads/{thread_id}", params=params)

    async def get_threads(
        self,
        thread_ids: list[str],
        *,
        format: str = "metadata",
        headers: list[str] | tuple[str, ...] | None = None,
        concurrency: int = 8,
    ) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(tid: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await self.get_thread(tid, format=format, headers=headers)
                except ConnectorAPIError as exc:
                    logger.debug("get_threads: skipping %s (%s)", tid, exc)
                    return None

        results = await asyncio.gather(*(_one(t) for t in thread_ids))
        return [r for r in results if r]

    async def list_history(
        self,
        start_history_id: str,
        *,
        label_id: str | None = None,
        history_types: tuple[str, ...] = ("messageAdded",),
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """``history.list`` raw JSON. Raises ``ConnectorAPIError(404)`` when the id is
        too old — callers fall back to a time-window search."""
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "historyTypes": list(history_types),
        }
        if label_id:
            params["labelId"] = label_id
        if page_token:
            params["pageToken"] = page_token
        return await self._api_get("/users/me/history", params=params)

    # -- Labels --------------------------------------------------------------

    async def list_labels(self) -> list[dict[str, Any]]:
        data = await self._api_get("/users/me/labels")
        return list(data.get("labels", []))

    async def get_label(self, label_id: str) -> dict[str, Any]:
        """``labels.get`` — includes messagesTotal / messagesUnread / threadsTotal."""
        return await self._api_get(f"/users/me/labels/{label_id}")

    async def create_label(self, name: str) -> dict[str, Any]:
        return await self._api_post(
            "/users/me/labels",
            json_body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )

    async def ensure_label(self, name: str) -> str:
        """Return the id of label *name*, creating it (and its ``A/B`` parents) if missing.
        System labels (INBOX, SPAM, SENT, UNREAD, STARRED …) resolve by their id."""
        wanted = name.strip().strip("/")
        if not wanted:
            raise ValueError("label name is empty")
        labels = await self.list_labels()
        by_name = {str(lbl.get("name", "")).lower(): lbl for lbl in labels}
        by_id = {str(lbl.get("id", "")): lbl for lbl in labels}
        if wanted in by_id:
            return wanted
        if wanted.lower() in by_name:
            return str(by_name[wanted.lower()]["id"])
        # Create parents first so nested labels never dangle.
        parts = [p for p in wanted.split("/") if p]
        created_id = ""
        for depth in range(1, len(parts) + 1):
            partial = "/".join(parts[:depth])
            existing = by_name.get(partial.lower())
            if existing:
                created_id = str(existing["id"])
                continue
            made = await self.create_label(partial)
            created_id = str(made.get("id", ""))
            by_name[partial.lower()] = made
        return created_id

    async def modify_message(
        self, message_id: str, *, add: list[str] | None = None, remove: list[str] | None = None
    ) -> ActionResult:
        return await self._modify_labels(message_id, add=add, remove=remove)

    async def modify_thread(
        self, thread_id: str, *, add: list[str] | None = None, remove: list[str] | None = None
    ) -> ActionResult:
        """Add/remove labels on every message of a thread (``threads.modify``)."""
        if not thread_id:
            return ActionResult(success=False, error="thread_id required")
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        if not body:
            return ActionResult(success=True)
        try:
            await self._api_post(f"/users/me/threads/{thread_id}/modify", json_body=body)
        except ConnectorAPIError as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True)

    # -- Drafts --------------------------------------------------------------

    async def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> dict[str, Any]:
        """``drafts.create`` — a reply draft when *thread_id*/*in_reply_to* are given.
        Never sends: the operator reviews it in Gmail's Drafts."""
        mime = MIMEText(body, "plain")
        mime["To"] = to
        mime["Subject"] = subject
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = references or in_reply_to
        if self._user_email:
            mime["From"] = self._user_email
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return await self._api_post("/users/me/drafts", json_body={"message": message})

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
    def _decode_part(payload: dict[str, Any]) -> str:
        data = payload.get("body", {}).get("data", "")
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _extract_body(payload: dict[str, Any]) -> str:
        """Extract a readable body from a Gmail payload: the first ``text/plain`` part,
        else the first ``text/html`` part converted to text (HTML-only mail is the norm
        for newsletters and spam — the old plain-only walk returned '' for all of it)."""
        plain = GmailConnector._first_part(payload, "text/plain")
        if plain:
            return plain
        markup = GmailConnector._first_part(payload, "text/html")
        return html_to_text(markup) if markup else ""

    @staticmethod
    def _first_part(payload: dict[str, Any], mime_type: str) -> str:
        if payload.get("mimeType", "") == mime_type:
            text = GmailConnector._decode_part(payload)
            if text:
                return text
        for part in payload.get("parts", []):
            text = GmailConnector._first_part(part, mime_type)
            if text:
                return text
        return ""
