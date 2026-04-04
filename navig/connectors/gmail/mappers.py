"""
Gmail — Resource Mappers

Convert raw Gmail API JSON responses into unified ``Resource`` instances.
"""

from __future__ import annotations

import email.utils
from datetime import datetime, timezone
from typing import Any

from navig.connectors.types import Resource, ResourceType


def _extract_header(headers: list[dict[str, str]], name: str) -> str:
    """Extract a header value from Gmail message headers list."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_timestamp(date_str: str) -> str:
    """Parse an RFC 2822 date from email headers → ISO 8601."""
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat()


def gmail_message_to_resource(msg: dict[str, Any]) -> Resource:
    """
    Map a Gmail API ``messages.get`` response to a ``Resource``.

    Expected fields: ``id``, ``snippet``, ``payload.headers``,
    ``labelIds``, ``internalDate``.
    """
    msg_id = msg.get("id", "")
    snippet = msg.get("snippet", "")
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    label_ids = msg.get("labelIds", [])

    subject = _extract_header(headers, "Subject") or "(no subject)"
    from_addr = _extract_header(headers, "From")
    to_addr = _extract_header(headers, "To")
    date_str = _extract_header(headers, "Date")
    message_id_header = _extract_header(headers, "Message-ID")

    return Resource(
        id=msg_id,
        source="gmail",
        title=subject,
        preview=snippet[:200] if snippet else "",
        url=f"https://mail.google.com/mail/#inbox/{msg_id}",
        timestamp=_parse_timestamp(date_str),
        resource_type=ResourceType.EMAIL,
        metadata={
            "from": from_addr,
            "to": to_addr,
            "labels": label_ids,
            "message_id": message_id_header,
            "thread_id": msg.get("threadId", ""),
        },
    )


def gmail_message_list_entry_to_resource(entry: dict[str, Any]) -> Resource:
    """
    Map a lightweight ``messages.list`` entry (id + threadId only)
    into a minimal ``Resource`` (to be enriched by batch fetch).
    """
    msg_id = entry.get("id", "")
    return Resource(
        id=msg_id,
        source="gmail",
        title="",
        preview="",
        url=f"https://mail.google.com/mail/#inbox/{msg_id}",
        timestamp="",
        resource_type=ResourceType.EMAIL,
        metadata={"thread_id": entry.get("threadId", "")},
    )
