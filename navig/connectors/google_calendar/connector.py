"""
Google Calendar Connector

Full CRUD connector for Google Calendar API v3.
Implements ``BaseConnector`` — search events, fetch details,
create/update/delete events.

HTTP via ``httpx`` — no google-api-python-client dependency.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAPIError
from navig.connectors.google_calendar.mappers import calendar_event_to_resource
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

logger = logging.getLogger("navig.connectors.google_calendar")

# Google API request timeout in seconds.
_GOOGLE_API_TIMEOUT: float = 15.0

_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarConnector(BaseConnector):
    """
    Google Calendar connector — list, search, create, update, delete events.
    """

    manifest = ConnectorManifest(
        id="google_calendar",
        display_name="Google Calendar",
        description="Search, view, and manage Google Calendar events.",
        domain=ConnectorDomain.CALENDAR,
        icon="📅",
        oauth_scopes=[
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        oauth_provider="google_calendar",
        requires_oauth=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self._calendar_id = "primary"

    # -- Helpers -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
        }

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
            resp = await client.get(
                f"{_API_BASE}{path}",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code != 200:
                raise ConnectorAPIError("google_calendar", resp.status_code, resp.text[:200])
            return resp.json()

    async def _api_post(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
            resp = await client.post(
                f"{_API_BASE}{path}",
                headers=self._headers(),
                json=json_body,
            )
            if resp.status_code not in (200, 201):
                raise ConnectorAPIError("google_calendar", resp.status_code, resp.text[:200])
            return resp.json()

    async def _api_put(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
            resp = await client.put(
                f"{_API_BASE}{path}",
                headers=self._headers(),
                json=json_body,
            )
            if resp.status_code != 200:
                raise ConnectorAPIError("google_calendar", resp.status_code, resp.text[:200])
            return resp.json()

    async def _api_delete(self, path: str) -> None:
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install: pip install httpx")
        async with httpx.AsyncClient(timeout=_GOOGLE_API_TIMEOUT) as client:
            resp = await client.delete(
                f"{_API_BASE}{path}",
                headers=self._headers(),
            )
            if resp.status_code not in (200, 204):
                raise ConnectorAPIError("google_calendar", resp.status_code, resp.text[:200])

    # -- BaseConnector interface -------------------------------------------

    async def search(self, query: str, limit: int = 5) -> list[Resource]:
        """
        Search calendar events.

        *query* is free-text; results from the next 30 days by default.

        Args:
            query: Free-text search string for event title / description.
            limit: Maximum number of events to return (default 5).
        """
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=30)).isoformat()

        data = await self._api_get(
            f"/calendars/{self._calendar_id}/events",
            params={
                "q": query,
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": limit,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        events = data.get("items", [])[:limit]
        return [calendar_event_to_resource(e) for e in events]

    async def fetch(self, resource_id: str) -> Resource:
        """Fetch a single calendar event by ID."""
        event = await self._api_get(f"/calendars/{self._calendar_id}/events/{resource_id}")
        return calendar_event_to_resource(event)

    async def act(self, action: Action) -> ActionResult:
        """
        Execute a Calendar action.

        Supported action_types:
            CREATE — create a new event (params: summary, start, end,
                     description, location, attendees)
            UPDATE — update an event (resource_id required, params: partial event)
            DELETE — delete an event (resource_id required)
        """
        try:
            if action.action_type == ActionType.CREATE:
                return await self._create_event(action)
            elif action.action_type == ActionType.UPDATE:
                return await self._update_event(action)
            elif action.action_type == ActionType.DELETE:
                return await self._delete_event(action)
            else:
                return ActionResult(
                    success=False,
                    error=f"Unsupported action: {action.action_type.value}",
                )
        except ConnectorAPIError as exc:
            return ActionResult(success=False, error=str(exc))

    async def health_check(self) -> HealthStatus:
        """Check Calendar API availability by listing calendars."""
        start = time.monotonic()
        try:
            await self._api_get("/users/me/calendarList", params={"maxResults": 1})
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(ok=True, latency_ms=latency)
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(ok=False, latency_ms=latency, message=str(exc))

    async def connect(self) -> None:
        """Validate token by running a health check."""
        await super().connect()
        try:
            health = await self.health_check()
            if not health.ok:
                logger.warning("Calendar health check failed: %s", health.message)
        except Exception as exc:
            logger.debug("Health check on connect failed: %s", exc)

    # -- Conflict detection ------------------------------------------------

    async def check_conflicts(
        self,
        start: str,
        end: str,
    ) -> list[Resource]:
        """
        Return events that overlap with the given time range.

        *start* and *end* should be ISO 8601 datetime strings.
        """
        data = await self._api_get(
            f"/calendars/{self._calendar_id}/events",
            params={
                "timeMin": start,
                "timeMax": end,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 10,
            },
        )
        events = data.get("items", [])
        return [calendar_event_to_resource(e) for e in events]

    # -- Private action implementations ------------------------------------

    async def _create_event(self, action: Action) -> ActionResult:
        """Create a new calendar event."""
        params = action.params
        body: dict[str, Any] = {
            "summary": params.get("summary", ""),
            "start": self._build_time_obj(params.get("start", "")),
            "end": self._build_time_obj(params.get("end", "")),
        }
        if params.get("description"):
            body["description"] = params["description"]
        if params.get("location"):
            body["location"] = params["location"]
        if params.get("attendees"):
            body["attendees"] = [{"email": e} for e in params["attendees"]]

        event = await self._api_post(
            f"/calendars/{self._calendar_id}/events",
            json_body=body,
        )
        resource = calendar_event_to_resource(event)
        return ActionResult(success=True, resource=resource)

    async def _update_event(self, action: Action) -> ActionResult:
        """Update an existing calendar event."""
        if not action.resource_id:
            return ActionResult(success=False, error="resource_id required for update")

        # Fetch current event
        current = await self._api_get(f"/calendars/{self._calendar_id}/events/{action.resource_id}")

        # Merge updates
        params = action.params
        if params.get("summary"):
            current["summary"] = params["summary"]
        if params.get("description"):
            current["description"] = params["description"]
        if params.get("location"):
            current["location"] = params["location"]
        if params.get("start"):
            current["start"] = self._build_time_obj(params["start"])
        if params.get("end"):
            current["end"] = self._build_time_obj(params["end"])
        if params.get("attendees"):
            current["attendees"] = [{"email": e} for e in params["attendees"]]

        event = await self._api_put(
            f"/calendars/{self._calendar_id}/events/{action.resource_id}",
            json_body=current,
        )
        resource = calendar_event_to_resource(event)
        return ActionResult(success=True, resource=resource)

    async def _delete_event(self, action: Action) -> ActionResult:
        """Delete a calendar event."""
        if not action.resource_id:
            return ActionResult(success=False, error="resource_id required for delete")
        await self._api_delete(f"/calendars/{self._calendar_id}/events/{action.resource_id}")
        return ActionResult(success=True)

    # -- Utilities ---------------------------------------------------------

    @staticmethod
    def _build_time_obj(dt_str: str) -> dict[str, str]:
        """
        Build a Calendar API time object from an ISO 8601 string.

        Detects all-day dates (YYYY-MM-DD) vs. timed events.
        """
        if not dt_str:
            # Default to now
            return {"dateTime": datetime.now(timezone.utc).isoformat()}
        if "T" not in dt_str and len(dt_str) == 10:
            return {"date": dt_str}
        return {"dateTime": dt_str}
