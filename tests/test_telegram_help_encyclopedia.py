"""Tests for the Telegram Help Encyclopedia — interactive /help navigation."""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_commands import (
    _HELP_CATEGORIES,
    TelegramCommandsMixin,
    _ensure_help_cmd_index,
    _HelpCategory,
    _HelpSubcategory,
    _iter_unique_registry,
)
from navig.gateway.channels.telegram_keyboards import CallbackHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(monkeypatch):
    """Return a TelegramChannel with noop send/edit/api_call."""
    ch = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *a, **kw: None,
    )

    calls: dict[str, list] = {"send": [], "edit": [], "api": [], "delete": []}

    async def _send(chat_id, text, parse_mode=None, keyboard=None, **kw):
        calls["send"].append(
            {"text": text, "parse_mode": parse_mode, "keyboard": keyboard, "chat_id": chat_id}
        )
        return {"message_id": 999}

    async def _edit(chat_id, message_id, text, parse_mode=None, keyboard=None, **kw):
        calls["edit"].append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "keyboard": keyboard,
            }
        )
        return {"ok": True}

    async def _api_call(method, payload, **kw):
        calls["api"].append({"method": method, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(ch, "send_message", _send)
    monkeypatch.setattr(ch, "edit_message", _edit)
    monkeypatch.setattr(ch, "_api_call", _api_call)
    return ch, calls


# ---------------------------------------------------------------------------
# Data-structure sanity checks
# ---------------------------------------------------------------------------


class TestHelpDataStructures:
    """Verify _HELP_CATEGORIES covers the registry without dangling refs."""

    def test_categories_not_empty(self):
        assert len(_HELP_CATEGORIES) >= 10

    def test_every_category_has_commands_or_subcategories(self):
        for cat in _HELP_CATEGORIES:
            has_cmds = cat.commands and len(cat.commands) > 0
            has_subs = cat.subcategories and len(cat.subcategories) > 0
            assert has_cmds or has_subs, (
                f"Category {cat.key!r} has neither commands nor subcategories"
            )

    def test_all_referenced_commands_exist_in_registry(self):
        idx = _ensure_help_cmd_index()
        missing = []
        for cat in _HELP_CATEGORIES:
            cmds = cat.commands or []
            if cat.subcategories:
                for sub in cat.subcategories:
                    cmds = cmds + sub.commands
            for cmd in cmds:
                if cmd not in idx:
                    missing.append(f"{cat.key}/{cmd}")
        assert missing == [], (
            f"Commands referenced in _HELP_CATEGORIES but missing from registry: {missing}"
        )

    def test_category_keys_are_unique(self):
        keys = [c.key for c in _HELP_CATEGORIES]
        assert len(keys) == len(set(keys))

    def test_subcategory_keys_unique_within_parent(self):
        for cat in _HELP_CATEGORIES:
            if cat.subcategories:
                keys = [s.key for s in cat.subcategories]
                assert len(keys) == len(set(keys)), f"Duplicate sub keys in {cat.key}"


# ---------------------------------------------------------------------------
# Builder methods (static, no I/O)
# ---------------------------------------------------------------------------


class TestBuildHelpHome:
    def test_returns_text_and_keyboard(self):
        text, kb = TelegramCommandsMixin._build_help_home()
        assert "NAVIG Command Center" in text
        assert isinstance(kb, list)
        # Last row is Close button
        assert kb[-1][0]["callback_data"] == "help:close"

    def test_each_category_has_button(self):
        text, kb = TelegramCommandsMixin._build_help_home()
        all_cb = [btn["callback_data"] for row in kb for btn in row]
        for cat in _HELP_CATEGORIES:
            assert f"help:c:{cat.key}" in all_cb, f"Missing button for {cat.key}"

    def test_buttons_max_two_per_row(self):
        _text, kb = TelegramCommandsMixin._build_help_home()
        for row in kb:
            assert len(row) <= 2


class TestBuildHelpCategory:
    def test_direct_commands_category(self):
        # monitoring has direct commands
        result = TelegramCommandsMixin._build_help_category("monitoring")
        assert result is not None
        text, kb = result
        assert "Monitoring" in text
        # Should have command detail buttons
        all_cb = [btn["callback_data"] for row in kb for btn in row]
        assert any(cb.startswith("help:d:") for cb in all_cb)

    def test_subcategory_chooser(self):
        # ai_models has subcategories
        result = TelegramCommandsMixin._build_help_category("ai_models")
        assert result is not None
        text, kb = result
        assert "AI & Models" in text
        # Should have subcategory buttons, not command detail buttons
        all_cb = [btn["callback_data"] for row in kb for btn in row]
        assert any(cb.startswith("help:s:") for cb in all_cb)
        assert not any(cb.startswith("help:d:") for cb in all_cb)

    def test_unknown_category_returns_none(self):
        assert TelegramCommandsMixin._build_help_category("nonexistent") is None

    def test_footer_has_back_and_close(self):
        result = TelegramCommandsMixin._build_help_category("docker")
        assert result is not None
        _text, kb = result
        last_row_cbs = [btn["callback_data"] for btn in kb[-1]]
        assert "help:home" in last_row_cbs
        assert "help:close" in last_row_cbs


class TestBuildHelpSubcategory:
    def test_valid_subcategory(self):
        result = TelegramCommandsMixin._build_help_subcategory("utilities", "network")
        assert result is not None
        text, kb = result
        assert "Network" in text
        all_cb = [btn["callback_data"] for row in kb for btn in row]
        assert any(cb.startswith("help:d:") for cb in all_cb)

    def test_back_returns_to_parent_category(self):
        result = TelegramCommandsMixin._build_help_subcategory("ai_models", "models")
        assert result is not None
        _text, kb = result
        last_row_cbs = [btn["callback_data"] for btn in kb[-1]]
        assert "help:c:ai_models" in last_row_cbs

    def test_unknown_parent_returns_none(self):
        assert TelegramCommandsMixin._build_help_subcategory("fake", "network") is None

    def test_unknown_sub_returns_none(self):
        assert TelegramCommandsMixin._build_help_subcategory("utilities", "fake") is None


class TestBuildHelpCommandDetail:
    def test_known_command(self):
        result = TelegramCommandsMixin._build_help_command_detail("help", "getting_started")
        assert result is not None
        text, kb = result
        assert "/help" in text
        assert "Category" in text

    def test_back_to_flat_category(self):
        result = TelegramCommandsMixin._build_help_command_detail("disk", "monitoring")
        assert result is not None
        _text, kb = result
        back_btn = kb[0][0]
        assert back_btn["callback_data"] == "help:c:monitoring"

    def test_back_to_subcategory(self):
        result = TelegramCommandsMixin._build_help_command_detail("ip", "utilities", "network")
        assert result is not None
        _text, kb = result
        back_btn = kb[0][0]
        assert back_btn["callback_data"] == "help:s:utilities:network"

    def test_unknown_command_returns_none(self):
        assert TelegramCommandsMixin._build_help_command_detail("zzzz", "core") is None


# ---------------------------------------------------------------------------
# Callback routing (_handle_help_callback)
# ---------------------------------------------------------------------------


class TestHelpCallbackRouting:
    @pytest.mark.asyncio
    async def test_help_home_callback(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:home", 100, 200)
        assert len(calls["edit"]) == 1
        assert "NAVIG Command Center" in calls["edit"][0]["text"]

    @pytest.mark.asyncio
    async def test_help_category_callback(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:c:docker", 100, 200)
        assert len(calls["edit"]) == 1
        assert "Docker" in calls["edit"][0]["text"]

    @pytest.mark.asyncio
    async def test_help_subcategory_callback(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:s:utilities:network", 100, 200)
        assert len(calls["edit"]) == 1
        assert "Network" in calls["edit"][0]["text"]

    @pytest.mark.asyncio
    async def test_help_detail_callback(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:d:disk:monitoring", 100, 200)
        assert len(calls["edit"]) == 1
        assert "/disk" in calls["edit"][0]["text"]

    @pytest.mark.asyncio
    async def test_help_detail_with_sub_back(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:d:ip:utilities:network", 100, 200)
        assert len(calls["edit"]) == 1
        kb = calls["edit"][0]["keyboard"]
        back_btn = kb[0][0]
        assert back_btn["callback_data"] == "help:s:utilities:network"

    @pytest.mark.asyncio
    async def test_help_close_deletes_message(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:close", 100, 200)
        assert len(calls["api"]) == 1
        assert calls["api"][0]["method"] == "deleteMessage"
        assert calls["api"][0]["payload"]["message_id"] == 200

    @pytest.mark.asyncio
    async def test_invalid_callback_falls_back_to_home(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help_callback("help:x:garbage", 100, 200)
        assert len(calls["edit"]) == 1
        assert "NAVIG Command Center" in calls["edit"][0]["text"]


# ---------------------------------------------------------------------------
# CallbackHandler dispatch for help: prefix
# ---------------------------------------------------------------------------


class TestCallbackHandlerHelpDispatch:
    @pytest.mark.asyncio
    async def test_callback_handler_dispatches_help_prefix(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)

        dispatched: list[str] = []

        async def _fake_help_cb(cb_data, chat_id, message_id):
            dispatched.append(cb_data)

        monkeypatch.setattr(ch, "_handle_help_callback", _fake_help_cb)

        handler = CallbackHandler(ch)

        async def _noop_answer(*a, **kw):
            return None

        monkeypatch.setattr(handler, "_answer", _noop_answer)

        await handler.handle(
            {
                "id": "cb-1",
                "data": "help:c:docker",
                "message": {"chat": {"id": 100}, "message_id": 200},
                "from": {"id": 42},
            }
        )
        assert dispatched == ["help:c:docker"]


# ---------------------------------------------------------------------------
# /help slash command sends interactive home
# ---------------------------------------------------------------------------


class TestHandleHelpCommand:
    @pytest.mark.asyncio
    async def test_handle_help_sends_interactive_home(self, monkeypatch):
        ch, calls = _make_channel(monkeypatch)
        await ch._handle_help(chat_id=100)
        assert len(calls["send"]) == 1
        sent = calls["send"][0]
        assert "NAVIG Command Center" in sent["text"]
        assert sent["parse_mode"] == "Markdown"
        # Should have category buttons
        all_cb = [btn["callback_data"] for row in sent["keyboard"] for btn in row]
        assert any(cb.startswith("help:c:") for cb in all_cb)


# ---------------------------------------------------------------------------
# Callback data length — Telegram max is 64 bytes
# ---------------------------------------------------------------------------


class TestCallbackDataLength:
    def test_all_callback_data_under_64_bytes(self):
        """Every callback_data string generated by help builders must be ≤ 64 bytes."""
        idx = _ensure_help_cmd_index()
        violations = []

        # Home buttons
        _text, kb = TelegramCommandsMixin._build_help_home()
        for row in kb:
            for btn in row:
                data = btn["callback_data"]
                if len(data.encode("utf-8")) > 64:
                    violations.append(data)

        # Each category
        for cat in _HELP_CATEGORIES:
            result = TelegramCommandsMixin._build_help_category(cat.key)
            if result:
                _text, kb = result
                for row in kb:
                    for btn in row:
                        data = btn["callback_data"]
                        if len(data.encode("utf-8")) > 64:
                            violations.append(data)

            # Subcategories
            if cat.subcategories:
                for sub in cat.subcategories:
                    result = TelegramCommandsMixin._build_help_subcategory(cat.key, sub.key)
                    if result:
                        _text, kb = result
                        for row in kb:
                            for btn in row:
                                data = btn["callback_data"]
                                if len(data.encode("utf-8")) > 64:
                                    violations.append(data)

        # Command details — sample a few
        for cmd_name in ("help", "disk", "ip", "auto_start"):
            if cmd_name in idx:
                result = TelegramCommandsMixin._build_help_command_detail(
                    cmd_name, "getting_started"
                )
                if result:
                    _text, kb = result
                    for row in kb:
                        for btn in row:
                            data = btn["callback_data"]
                            if len(data.encode("utf-8")) > 64:
                                violations.append(data)

        assert violations == [], f"Callback data exceeds 64 bytes: {violations}"
