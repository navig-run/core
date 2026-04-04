"""
SmartLinker — Cross-Connector Relationship Engine

Links resources across connectors.  Phase 1 supports
Gmail ↔ Google Calendar linking:

* Emails mentioning calendar-like dates → related calendar events.
* Calendar events with attendees → matching email threads.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from navig.connectors.registry import get_connector_registry
from navig.connectors.types import Resource

logger = logging.getLogger("navig.connectors.smart_linker")

# Lightweight date phrases: "tomorrow", "next Monday", "Jan 15", etc.
_DATE_PATTERN = re.compile(
    r"\b("
    r"today|tomorrow|next\s+\w+day"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b",
    re.IGNORECASE,
)


class SmartLinker:
    """
    Discover relationships between resources from different connectors.

    Usage::

        linker = SmartLinker()
        enriched = await linker.enrich(email_resource)
        # enriched.metadata["related_events"] → [event_resource, ...]
    """

    def __init__(self) -> None:
        self._registry = get_connector_registry()

    async def enrich(self, resource: Resource) -> Resource:
        """
        Enrich *resource* with cross-connector relationships.

        Currently supports:

        * Gmail → Google Calendar (date mentions → events, attendee overlap)
        * Google Calendar → Gmail (attendee-based email search)
        """
        try:
            if resource.source == "gmail":
                resource = await self._link_gmail_to_calendar(resource)
            elif resource.source == "google_calendar":
                resource = await self._link_calendar_to_gmail(resource)
        except Exception as exc:
            logger.debug("SmartLinker enrichment failed: %s", exc)
        return resource

    async def find_related(
        self, resource: Resource, target_source: str | None = None
    ) -> list[Resource]:
        """
        Find related resources in other connectors.

        If *target_source* is given, only search that connector.
        """
        related: list[Resource] = []

        sources = (
            [target_source]
            if target_source
            else [
                c.manifest.id
                for c in self._registry.list_connected()
                if c.manifest.id != resource.source
            ]
        )

        for source_id in sources:
            try:
                if not self._registry.has(source_id):
                    continue
                connector = self._registry.get(source_id)
                query = self._build_cross_query(resource, source_id)
                if query:
                    results = await connector.search(query)
                    related.extend(results[:5])  # cap per connector
            except Exception as exc:
                logger.debug("Cross-search to %s failed: %s", source_id, exc)
        return related

    # -- Private: Gmail → Calendar -----------------------------------------

    async def _link_gmail_to_calendar(self, email: Resource) -> Resource:
        """Attach related calendar events to an email resource."""
        if not self._registry.has("google_calendar"):
            return email
        calendar = self._registry.get("google_calendar")

        related_events: list[dict[str, Any]] = []

        # Strategy 1: Date mentions in subject/preview
        text = f"{email.title} {email.preview}"
        date_matches = _DATE_PATTERN.findall(text)
        if date_matches:
            # Search calendar around mentioned dates
            try:
                results = await calendar.search(email.title)
                for r in results[:3]:
                    related_events.append(r.to_dict())
            except Exception as exc:
                logger.debug("Calendar search for email dates failed: %s", exc)

        # Strategy 2: Attendee overlap — search calendar for sender
        sender = email.metadata.get("from", "")
        if sender and not related_events:
            try:
                results = await calendar.search(sender)
                for r in results[:3]:
                    related_events.append(r.to_dict())
            except Exception as exc:
                logger.debug("Calendar search for sender failed: %s", exc)

        if related_events:
            email.metadata["related_events"] = related_events
        return email

    async def _link_calendar_to_gmail(self, event: Resource) -> Resource:
        """Attach related email threads to a calendar event."""
        if not self._registry.has("gmail"):
            return event
        gmail = self._registry.get("gmail")

        related_emails: list[dict[str, Any]] = []

        # Strategy 1: Search emails by event title
        if event.title and event.title != "(no title)":
            try:
                results = await gmail.search(event.title)
                for r in results[:3]:
                    related_emails.append(r.to_dict())
            except Exception as exc:
                logger.debug("Gmail search for event title failed: %s", exc)

        # Strategy 2: Search by attendees
        attendees = event.metadata.get("attendees", [])
        if attendees and not related_emails:
            # Search for first non-organizer attendee
            organizer = event.metadata.get("organizer", "")
            search_email = next((a for a in attendees if a != organizer), attendees[0])
            try:
                results = await gmail.search(f"from:{search_email}")
                for r in results[:3]:
                    related_emails.append(r.to_dict())
            except Exception as exc:
                logger.debug("Gmail search for attendee failed: %s", exc)

        if related_emails:
            event.metadata["related_emails"] = related_emails
        return event

    # -- Query builders ----------------------------------------------------

    @staticmethod
    def _build_cross_query(resource: Resource, target_source: str) -> str:
        """Build a search query for *target_source* based on *resource*."""
        if target_source == "google_calendar" and resource.source == "gmail":
            return resource.title or ""
        if target_source == "gmail" and resource.source == "google_calendar":
            attendees = resource.metadata.get("attendees", [])
            if attendees:
                return f"from:{attendees[0]}"
            return resource.title or ""
        # Generic fallback
        return resource.title or ""
