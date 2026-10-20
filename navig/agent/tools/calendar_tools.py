"""Agent tool: find open meeting times on the operator's Google Calendar.

Read-only — it asks Google when people are busy and proposes free slots; it books
nothing and emails no one. Booking stays with the connector's CREATE action
(``send_updates`` decides whether Google emails the attendees), so "find a time" can
never turn into "invited five people" by accident.

⚠ ``ToolResult`` takes a REQUIRED ``name`` and has no ``data`` field (see pim_tools).
"""

from __future__ import annotations

import logging
from datetime import time, timedelta
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_DAYS = 31


def _parse_hhmm(raw: Any, default: time) -> time:
    try:
        hh, mm = str(raw).split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return default


async def _connector():
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry

    ensure_connectors_loaded()
    connector = get_connector_registry().get("google_calendar")
    if not await ConnectorAuthManager().inject_token(connector):
        raise RuntimeError("Google Calendar is not connected — run: navig connector connect google_calendar")
    return connector


class CalendarFindSlotsTool(BaseTool):
    """Propose meeting times when the operator (and optional attendees) are free."""

    name = "calendar_find_slots"
    description = (
        "Find free meeting times on the operator's Google Calendar, optionally also "
        "checking other people's availability by email. Read-only: proposes slots, books "
        "nothing and sends no invitations. People who don't share their calendar are "
        "reported as unknown rather than treated as free."
    )
    parameters: dict[str, Any] = {
        "duration_minutes": {"type": "integer", "description": "Meeting length in minutes (default 30)."},
        "attendees": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Other people's email addresses whose availability to include.",
        },
        "within_days": {"type": "integer", "description": "How many days ahead to search (default 7, max 31)."},
        "work_start": {"type": "string", "description": "Earliest start, HH:MM in the operator's time (default 09:00)."},
        "work_end": {"type": "string", "description": "Latest end, HH:MM (default 17:00)."},
        "include_weekends": {"type": "boolean", "description": "Also propose Saturday/Sunday (default false)."},
        "limit": {"type": "integer", "description": "How many slots to propose (default 5)."},
    }

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        from navig.connectors.google_calendar.slots import find_free_slots
        from navig.pim.clock import local_now

        try:
            duration = timedelta(minutes=max(5, int(args.get("duration_minutes") or 30)))
            days = min(_MAX_DAYS, max(1, int(args.get("within_days") or 7)))
            limit = min(20, max(1, int(args.get("limit") or 5)))
        except (TypeError, ValueError) as exc:
            return ToolResult(name=self.name, success=False, error=f"bad number: {exc}")
        attendees = [str(a).strip() for a in (args.get("attendees") or []) if str(a).strip()]
        work_start = _parse_hhmm(args.get("work_start"), time(9, 0))
        work_end = _parse_hhmm(args.get("work_end"), time(17, 0))

        now = local_now()
        end = now + timedelta(days=days)
        try:
            connector = await _connector()
            calendars = ["primary", *attendees]
            answer = await connector.free_busy(calendars, now, end)
        except Exception as exc:  # noqa: BLE001 — a tool must never raise into the agent loop
            logger.info("calendar_find_slots failed: %s", exc)
            return ToolResult(name=self.name, success=False, error=str(exc))

        busy = [b for cal in answer.values() for b in cal["busy"]]
        unknown = [c for c, v in answer.items() if v.get("error")]
        slots = find_free_slots(
            busy, start=now, end=end, duration=duration, tz=now.tzinfo,
            work_start=work_start, work_end=work_end,
            weekdays_only=not bool(args.get("include_weekends")), limit=limit,
        )
        lines = [f"- {s:%a %d %b %H:%M}–{e:%H:%M}" for s, e in slots]
        out = (
            f"Free {int(duration.total_seconds() // 60)}-minute slots in the next {days} days "
            f"({now.tzname()}):\n" + ("\n".join(lines) if lines else "- none found in working hours")
        )
        if unknown:
            out += (
                "\nAvailability UNKNOWN for: " + ", ".join(unknown)
                + " (calendar not shared) — confirm with them before booking."
            )
        return ToolResult(name=self.name, success=True, output=out)


def register_calendar_tools() -> None:
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY  # noqa: PLC0415

    _AGENT_REGISTRY.register(CalendarFindSlotsTool(), toolset="calendar")
    logger.debug("calendar agent tools registered: calendar_find_slots")
