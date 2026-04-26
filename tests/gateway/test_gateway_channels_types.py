"""Tests for navig.gateway.channels.types — ContextMessage, MessageMetadata."""
from __future__ import annotations

import pytest

from navig.gateway.channels.types import ContextMessage, MessageMetadata


class TestContextMessage:
    def test_basic_creation(self):
        msg: ContextMessage = {"role": "user", "content": "hello"}
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_assistant_role(self):
        msg: ContextMessage = {"role": "assistant", "content": "world"}
        assert msg["role"] == "assistant"

    def test_is_dict(self):
        msg: ContextMessage = {"role": "user", "content": "hi"}
        assert isinstance(msg, dict)


class TestMessageMetadata:
    def test_empty_metadata_valid(self):
        meta: MessageMetadata = {}
        assert isinstance(meta, dict)

    def test_all_optional_keys_accepted(self):
        meta: MessageMetadata = {
            "chat_id": 100,
            "user_id": 42,
            "username": "alice",
            "message_id": 1,
            "is_group": False,
            "reply_to": None,
        }
        assert meta["user_id"] == 42
        assert meta["reply_to"] is None

    def test_routing_keys(self):
        meta: MessageMetadata = {
            "tier_override": "big",
            "detected_language": "fr",
            "resolved_model": "openai:gpt-4o",
        }
        assert meta["tier_override"] == "big"
        assert meta["detected_language"] == "fr"

    def test_session_keys(self):
        meta: MessageMetadata = {
            "session_key": "user:42:chat:100",
            "context_messages": [{"role": "user", "content": "hi"}],
        }
        assert meta["session_key"] == "user:42:chat:100"
        assert len(meta["context_messages"]) == 1
