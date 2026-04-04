"""
Google Calendar — Resource Mappers

Convert raw Calendar API v3 JSON into unified ``Resource`` instances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from navig.connectors.types import Resource, ResourceType


def _parse_event_time(time_obj: dict[str, str]) -> str:
    """
    Parse a Calendar event time to ISO 8601.

    Handles both ``dateTime`` (timed event) and ``date`` (all-day event).
    """
    dt_str = time_obj.get("dateTime") or time_obj.get("date", "")
    if not dt_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        # dateTime has timezone offset; date is just YYYY-MM-DD
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return dt_str


def calendar_event_to_resource(event: dict[str, Any]) -> Resource:
    """
    Map a Google Calendar API event to a ``Resource``.

    Expected fields: ``id``, ``summary``, ``description``, ``start``,
    ``end``, ``attendees``, ``location``, ``htmlLink``.
    """
    event_id = event.get("id", "")
    summary = event.get("summary", "(no title)")
    description = event.get("description", "")
    start = event.get("start", {})
    end = event.get("end", {})
    location = event.get("location", "")
    attendees = event.get("attendees", [])
    html_link = event.get("htmlLink", "")

    attendee_emails = [a.get("email", "") for a in attendees if a.get("email")]

    return Resource(
        id=event_id,
        source="google_calendar",
        title=summary,
        preview=description[:200] if description else "",
        url=html_link,
        timestamp=_parse_event_time(start),
        resource_type=ResourceType.EVENT,
        metadata={
            "start": _parse_event_time(start),
            "end": _parse_event_time(end),
            "location": location,
            "attendees": attendee_emails,
            "status": event.get("status", ""),
            "organizer": event.get("organizer", {}).get("email", ""),
            "all_day": "date" in start and "dateTime" not in start,
            "recurring": bool(event.get("recurringEventId")),
        },
    )
