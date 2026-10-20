"""Conversation source adapters. Each one discovers sources and turns raw files into
attributed :class:`navig.recall.model.Turn` objects; none of them writes anywhere."""

from __future__ import annotations

#: ``chats`` reads only the chats the person shared from the chat library (navig-chats).
#: ``capture`` reads Navig Capture v1 notes the Dock wrote into the inbox.
#: ``notes`` reads the web notes the NAVIG Dock synced to this computer (navig-notes).
ADAPTERS = ("claude_code", "codex", "session_audit", "chats", "capture", "notes")
