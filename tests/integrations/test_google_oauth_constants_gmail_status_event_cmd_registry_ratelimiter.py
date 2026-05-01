"""
Batch 47 — hermetic unit tests for:
  navig/connectors/google_oauth_constants.py         — Google OAuth endpoint constants
  navig/connectors/gmail/oauth_config.py             — GMAIL_SCOPES, build_gmail_oauth_config
  navig/agent/conv/status_event.py                   — StatusEvent dataclass
  navig/bot/command_registry.py                      — BotCommand, CommandRegistry
  navig/gateway/channels/utils/decorators.py         — RateLimiter
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/connectors/google_oauth_constants.py
# ---------------------------------------------------------------------------

from navig.connectors.google_oauth_constants import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
)


class TestGoogleOAuthConstants:
    def test_auth_url_is_google(self):
        assert "accounts.google.com" in GOOGLE_AUTH_URL

    def test_auth_url_https(self):
        assert GOOGLE_AUTH_URL.startswith("https://")

    def test_token_url_googleapis(self):
        assert "googleapis.com" in GOOGLE_TOKEN_URL

    def test_token_url_https(self):
        assert GOOGLE_TOKEN_URL.startswith("https://")

    def test_userinfo_url_googleapis(self):
        assert "googleapis.com" in GOOGLE_USERINFO_URL

    def test_userinfo_url_https(self):
        assert GOOGLE_USERINFO_URL.startswith("https://")

    def test_urls_are_strings(self):
        for url in (GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL):
            assert isinstance(url, str)

    def test_all_urls_distinct(self):
        urls = {GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL}
        assert len(urls) == 3

    def test_auth_url_contains_oauth2(self):
        assert "oauth2" in GOOGLE_AUTH_URL.lower()

    def test_userinfo_url_v3(self):
        assert "v3" in GOOGLE_USERINFO_URL or "userinfo" in GOOGLE_USERINFO_URL


# ---------------------------------------------------------------------------
# navig/connectors/gmail/oauth_config.py
# ---------------------------------------------------------------------------

from navig.connectors.gmail.oauth_config import (
    GMAIL_SCOPES,
    build_gmail_oauth_config,
)


class TestGmailScopes:
    def test_is_list(self):
        assert isinstance(GMAIL_SCOPES, list)

    def test_not_empty(self):
        assert len(GMAIL_SCOPES) >= 4

    def test_contains_readonly_scope(self):
        assert "https://www.googleapis.com/auth/gmail.readonly" in GMAIL_SCOPES

    def test_contains_send_scope(self):
        assert "https://www.googleapis.com/auth/gmail.send" in GMAIL_SCOPES

    def test_contains_modify_scope(self):
        assert "https://www.googleapis.com/auth/gmail.modify" in GMAIL_SCOPES

    def test_contains_labels_scope(self):
        assert "https://www.googleapis.com/auth/gmail.labels" in GMAIL_SCOPES

    def test_contains_openid(self):
        assert "openid" in GMAIL_SCOPES

    def test_contains_email_and_profile(self):
        assert "email" in GMAIL_SCOPES
        assert "profile" in GMAIL_SCOPES


class TestBuildGmailOAuthConfig:
    def test_basic_build(self):
        cfg = build_gmail_oauth_config(client_id="gmail-client-id")
        assert cfg.client_id == "gmail-client-id"

    def test_name_is_gmail(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.name == "Gmail"

    def test_client_secret_none_by_default(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.client_secret is None

    def test_client_secret_provided(self):
        cfg = build_gmail_oauth_config(client_id="x", client_secret="super-secret")
        assert cfg.client_secret == "super-secret"

    def test_scopes_match_gmail_scopes(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.scopes == GMAIL_SCOPES

    def test_authorize_url_uses_google_auth_url(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.authorize_url == GOOGLE_AUTH_URL

    def test_token_url_uses_google_token_url(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.token_url == GOOGLE_TOKEN_URL

    def test_userinfo_url_uses_google_userinfo_url(self):
        cfg = build_gmail_oauth_config(client_id="x")
        assert cfg.userinfo_url == GOOGLE_USERINFO_URL


# ---------------------------------------------------------------------------
# navig/agent/conv/status_event.py
# ---------------------------------------------------------------------------

from navig.agent.conv.status_event import StatusEvent


class TestStatusEvent:
    def _ts(self):
        return datetime(2024, 1, 15, 12, 0, 0)

    def test_basic_construction(self):
        ev = StatusEvent(
            type="task_start",
            task_id="t1",
            message="Starting task",
            timestamp=self._ts(),
        )
        assert ev.type == "task_start"
        assert ev.task_id == "t1"
        assert ev.message == "Starting task"
        assert ev.timestamp == self._ts()

    def test_optional_fields_default_none(self):
        ev = StatusEvent(type="thinking", task_id="t2", message="...", timestamp=self._ts())
        assert ev.step_index is None
        assert ev.total_steps is None

    def test_metadata_default_empty_dict(self):
        ev = StatusEvent(type="step_done", task_id="t3", message="done", timestamp=self._ts())
        assert ev.metadata == {}

    def test_step_index_and_total_steps(self):
        ev = StatusEvent(
            type="step_start",
            task_id="t4",
            message="step 2 of 5",
            timestamp=self._ts(),
            step_index=2,
            total_steps=5,
        )
        assert ev.step_index == 2
        assert ev.total_steps == 5

    def test_metadata_populated(self):
        ev = StatusEvent(
            type="step_failed",
            task_id="t5",
            message="failed",
            timestamp=self._ts(),
            metadata={"error": "timeout"},
        )
        assert ev.metadata["error"] == "timeout"

    def test_all_valid_types(self):
        valid_types = [
            "task_start", "step_start", "step_done", "step_failed",
            "task_done", "thinking", "streaming_token",
        ]
        for t in valid_types:
            ev = StatusEvent(type=t, task_id="x", message="m", timestamp=self._ts())
            assert ev.type == t

    def test_is_dataclass(self):
        from dataclasses import fields
        field_names = {f.name for f in fields(StatusEvent)}
        assert "type" in field_names
        assert "task_id" in field_names
        assert "message" in field_names
        assert "timestamp" in field_names
        assert "metadata" in field_names

    def test_metadata_is_independent_per_instance(self):
        ev1 = StatusEvent(type="task_done", task_id="a", message="m", timestamp=self._ts())
        ev2 = StatusEvent(type="task_done", task_id="b", message="m", timestamp=self._ts())
        ev1.metadata["key"] = "val"
        assert "key" not in ev2.metadata


# ---------------------------------------------------------------------------
# navig/bot/command_registry.py
# ---------------------------------------------------------------------------

from navig.bot.command_registry import BotCommand, CommandRegistry


def _make_schema(name: str, description: str = "A command") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


class TestBotCommand:
    def test_fields(self):
        cmd = BotCommand(name="ls", schema={"type": "function"})
        assert cmd.name == "ls"
        assert cmd.schema == {"type": "function"}

    def test_tags_default_empty(self):
        cmd = BotCommand(name="x", schema={})
        assert cmd.tags == []

    def test_tags_set(self):
        cmd = BotCommand(name="x", schema={}, tags=["read", "file"])
        assert "read" in cmd.tags

    def test_repr(self):
        cmd = BotCommand(name="my_cmd", schema={})
        assert "my_cmd" in repr(cmd)


class TestCommandRegistry:
    def test_starts_empty(self):
        reg = CommandRegistry()
        assert reg.all() == []

    def test_add_single_command(self):
        reg = CommandRegistry()
        schema = _make_schema("list_files")
        cmd = reg.add(schema)
        assert cmd.name == "list_files"

    def test_get_registered_command(self):
        reg = CommandRegistry()
        reg.add(_make_schema("run_cmd"))
        cmd = reg.get("run_cmd")
        assert cmd is not None
        assert cmd.name == "run_cmd"

    def test_get_missing_returns_none(self):
        reg = CommandRegistry()
        assert reg.get("nonexistent") is None

    def test_all_returns_list(self):
        reg = CommandRegistry()
        reg.add(_make_schema("a"))
        reg.add(_make_schema("b"))
        all_cmds = reg.all()
        assert len(all_cmds) == 2
        names = {c.name for c in all_cmds}
        assert names == {"a", "b"}

    def test_schemas_returns_raw_dicts(self):
        reg = CommandRegistry()
        s1 = _make_schema("cmd1")
        s2 = _make_schema("cmd2")
        reg.add(s1)
        reg.add(s2)
        schemas = reg.schemas()
        assert s1 in schemas
        assert s2 in schemas

    def test_names_returns_command_names(self):
        reg = CommandRegistry()
        reg.add(_make_schema("x"))
        reg.add(_make_schema("y"))
        assert set(reg.names()) == {"x", "y"}

    def test_add_with_tags(self):
        reg = CommandRegistry()
        cmd = reg.add(_make_schema("tagged_cmd"), tags=["file", "read"])
        assert "file" in cmd.tags

    def test_add_invalid_schema_raises_valueerror(self):
        reg = CommandRegistry()
        with pytest.raises(ValueError, match="Schema must have shape"):
            reg.add({"type": "bad_schema"})

    def test_add_missing_function_key_raises(self):
        reg = CommandRegistry()
        with pytest.raises(ValueError):
            reg.add({})

    def test_overwrite_existing_command(self):
        reg = CommandRegistry()
        reg.add(_make_schema("dup", "first"))
        reg.add(_make_schema("dup", "second"))
        cmd = reg.get("dup")
        assert cmd.schema["function"]["description"] == "second"

    def test_register_decorator(self):
        reg = CommandRegistry()

        @reg.register
        def my_cmd():
            return _make_schema("my_cmd")

        cmd = reg.get("my_cmd")
        assert cmd is not None
        assert cmd.name == "my_cmd"

    def test_register_decorator_returns_original_function(self):
        reg = CommandRegistry()

        @reg.register
        def my_fn():
            return _make_schema("fn1")

        # Should still be callable
        result = my_fn()
        assert result["function"]["name"] == "fn1"

    def test_bulk_load(self):
        reg = CommandRegistry()
        schemas = [_make_schema("p"), _make_schema("q"), _make_schema("r")]
        reg.bulk_load(schemas)
        assert set(reg.names()) == {"p", "q", "r"}

    def test_bulk_load_with_tags(self):
        reg = CommandRegistry()
        reg.bulk_load([_make_schema("tagged")], tags=["system"])
        cmd = reg.get("tagged")
        assert "system" in cmd.tags

    def test_schemas_order_preserved(self):
        reg = CommandRegistry()
        for i in range(5):
            reg.add(_make_schema(f"cmd{i}"))
        names = reg.names()
        assert names == [f"cmd{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# navig/gateway/channels/utils/decorators.py — RateLimiter
# ---------------------------------------------------------------------------

from navig.gateway.channels.utils.decorators import RateLimiter


class TestRateLimiter:
    def test_default_max_requests(self):
        rl = RateLimiter()
        assert rl.max_requests == 30

    def test_default_window_minutes(self):
        from datetime import timedelta
        rl = RateLimiter()
        assert rl.window == timedelta(minutes=1)

    def test_custom_max_requests(self):
        rl = RateLimiter(max_requests=10)
        assert rl.max_requests == 10

    def test_first_request_allowed(self):
        rl = RateLimiter(max_requests=5)
        assert rl.is_allowed(user_id=1001) is True

    def test_multiple_requests_within_limit_allowed(self):
        rl = RateLimiter(max_requests=5)
        uid = 2001
        for _ in range(5):
            assert rl.is_allowed(uid) is True

    def test_request_over_limit_denied(self):
        rl = RateLimiter(max_requests=3)
        uid = 3001
        for _ in range(3):
            rl.is_allowed(uid)
        assert rl.is_allowed(uid) is False

    def test_different_users_isolated(self):
        rl = RateLimiter(max_requests=2)
        for _ in range(2):
            rl.is_allowed(user_id=10)
        # user 10 is at limit; user 20 should still be allowed
        assert rl.is_allowed(user_id=20) is True

    def test_old_requests_purged_after_window(self):
        from datetime import timedelta
        from unittest.mock import patch

        rl = RateLimiter(max_requests=2, window_minutes=1)
        uid = 4001

        past_time = datetime.now() - timedelta(minutes=2)
        rl.requests[uid] = [past_time, past_time]

        # After purging old requests, a new one should be allowed
        assert rl.is_allowed(uid) is True

    def test_requests_dict_starts_empty(self):
        rl = RateLimiter()
        assert len(rl.requests) == 0

    def test_user_request_count_tracked(self):
        rl = RateLimiter(max_requests=10)
        uid = 5001
        rl.is_allowed(uid)
        rl.is_allowed(uid)
        assert len(rl.requests[uid]) == 2
