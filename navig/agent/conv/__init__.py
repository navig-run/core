"""navig.agent.conv — ConversationalAgent package.

Re-exports ConversationalAgent, StatusEvent, and ConsoleStatusRenderer for
backward compatibility and for new callers that use the structured event system.
"""
from __future__ import annotations

from navig.agent.conv.agent import ConversationalAgent
from navig.agent.conv.console_renderer import ConsoleStatusRenderer
from navig.agent.conv.status_event import StatusEvent

__all__ = ["ConversationalAgent", "ConsoleStatusRenderer", "StatusEvent"]
