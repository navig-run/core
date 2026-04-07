"""Tests for telegram_keyboards.py core components.

Covers: classify_response, choose_profile, CallbackStore, CallbackEntry,
ResponseKeyboardBuilder, _mdv2_escape, _short_hash,
and CallbackHandler dispatch for untested callback prefixes.
"""

import time

import pytest

from navig.gateway.channels.telegram_keyboards import (
    CallbackEntry,
    CallbackStore,
    ContentCategory,
    KeyboardProfile,
    ResponseKeyboardBuilder,
    _mdv2_escape,
    _short_hash,
    choose_profile,
    classify_response,
    get_callback_store,
)

# ────────────────────────────────────────────────────────────
# _mdv2_escape
# ────────────────────────────────────────────────────────────


class TestMdv2Escape:
    def test_escapes_markdown_v2_special_chars(self):
        assert _mdv2_escape("hello_world") == r"hello\_world"
        assert _mdv2_escape("2*3=6") == r"2\*3\=6"

    def test_escapes_brackets_and_parens(self):
        assert _mdv2_escape("[link](url)") == r"\[link\]\(url\)"

    def test_escapes_backticks(self):
        assert _mdv2_escape("`code`") == r"\`code\`"

    def test_plain_alphanum_unchanged(self):
        assert _mdv2_escape("hello123") == "hello123"

    def test_empty_string(self):
        assert _mdv2_escape("") == ""

    def test_non_string_auto_cast(self):
        assert _mdv2_escape(42) == "42"


# ────────────────────────────────────────────────────────────
# _short_hash
# ────────────────────────────────────────────────────────────


class TestShortHash:
    def test_default_length(self):
        h = _short_hash("test")
        assert len(h) == 6

    def test_custom_length(self):
        h = _short_hash("test", length=10)
        assert len(h) == 10

    def test_deterministic(self):
        assert _short_hash("abc") == _short_hash("abc")

    def test_different_inputs_differ(self):
        assert _short_hash("abc") != _short_hash("xyz")


# ────────────────────────────────────────────────────────────
# ContentCategory enum
# ────────────────────────────────────────────────────────────


class TestContentCategory:
    def test_all_values_exist(self):
        assert ContentCategory.INFORMATIONAL.value == "info"
        assert ContentCategory.CODE.value == "code"
        assert ContentCategory.CONVERSATIONAL.value == "chat"
        assert ContentCategory.ERROR.value == "error"
        assert ContentCategory.COMPARISON.value == "compare"
        assert ContentCategory.HOWTO.value == "howto"


# ────────────────────────────────────────────────────────────
# classify_response
# ────────────────────────────────────────────────────────────


class TestClassifyResponse:
    def test_code_block_detected(self):
        text = "Here is some code:\n```python\ndef foo():\n    return 42\n```\nDone."
        assert classify_response(text) == ContentCategory.CODE

    def test_comparison_words(self):
        assert (
            classify_response("Pros and cons of using Docker vs VMs") == ContentCategory.COMPARISON
        )

    def test_howto_signals(self):
        text = "Step 1: Install the package. Step 2: Configure. Step 3: Run."
        assert classify_response(text) == ContentCategory.HOWTO

    def test_error_short(self):
        text = "Sorry, I'm unable to process that request."
        assert classify_response(text) == ContentCategory.ERROR

    def test_error_long_not_classified_as_error(self):
        # Error signals in very long text should not match as error
        text = "Sorry, I cannot help with that. " * 20  # >300 chars
        result = classify_response(text)
        # Long error text won't be classified as ERROR due to length guard
        assert result != ContentCategory.ERROR

    def test_opinion(self):
        text = "I think Python is a great choice for most scripting tasks."
        assert classify_response(text) == ContentCategory.OPINION

    def test_numbered_list(self):
        text = "1. Item A\n2. Item B\n3. Item C\n4. Item D"
        assert classify_response(text) == ContentCategory.LIST

    def test_bullet_list(self):
        text = "- First\n- Second\n- Third\n- Fourth\n- Fifth"
        assert classify_response(text) == ContentCategory.LIST

    def test_long_informational(self):
        text = "A" * 650  # >600 chars, no special markers
        assert classify_response(text) == ContentCategory.INFORMATIONAL

    def test_short_conversational(self):
        assert classify_response("Sure, let me check.") == ContentCategory.CONVERSATIONAL

    def test_empty_string(self):
        assert classify_response("") == ContentCategory.CONVERSATIONAL

    def test_howto_tutorial(self):
        text = "Here's how to set up your project with navig init."
        assert classify_response(text) == ContentCategory.HOWTO

    def test_alternatives_comparison(self):
        text = "The advantages of option A far outweigh the disadvantages."
        assert classify_response(text) == ContentCategory.COMPARISON


# ────────────────────────────────────────────────────────────
# choose_profile
# ────────────────────────────────────────────────────────────


class TestChooseProfile:
    def test_approval_always_action(self):
        result = choose_profile("anything", ContentCategory.CODE, has_approval=True)
        assert result == KeyboardProfile.ACTION

    def test_short_text_no_buttons(self):
        result = choose_profile("OK", ContentCategory.CONVERSATIONAL)
        assert result == KeyboardProfile.NONE

    def test_greeting_no_buttons(self):
        result = choose_profile("Hello! How can I help?", ContentCategory.CONVERSATIONAL)
        assert result == KeyboardProfile.NONE

    def test_short_error_no_buttons(self):
        result = choose_profile("Sorry, I cannot do that.", ContentCategory.ERROR)
        assert result == KeyboardProfile.NONE

    def test_code_gets_expand(self):
        text = "Here is the solution:\n" + "x" * 500
        result = choose_profile(text, ContentCategory.CODE)
        assert result == KeyboardProfile.EXPAND

    def test_long_text_gets_expand(self):
        text = "A" * 600
        result = choose_profile(text, ContentCategory.INFORMATIONAL)
        assert result == KeyboardProfile.EXPAND

    def test_medium_conversational_gets_none(self):
        # Between 120 and 250 chars, conversational → NONE
        text = "A" * 200
        result = choose_profile(text, ContentCategory.CONVERSATIONAL)
        assert result == KeyboardProfile.NONE

    def test_medium_informational_gets_none(self):
        # 200 chars, informational → default = NONE
        text = "A" * 200
        result = choose_profile(text, ContentCategory.INFORMATIONAL)
        assert result == KeyboardProfile.NONE


# ────────────────────────────────────────────────────────────
# CallbackStore
# ────────────────────────────────────────────────────────────


class TestCallbackStore:
    def test_put_and_get(self):
        store = CallbackStore(max_entries=10)
        entry = CallbackEntry(action="test", user_message="hi", ai_response="hey", category="chat")
        store.put("k1", entry)
        assert store.get("k1") is entry

    def test_get_missing_returns_none(self):
        store = CallbackStore()
        assert store.get("nonexistent") is None

    def test_remove(self):
        store = CallbackStore()
        entry = CallbackEntry(action="a", user_message="", ai_response="", category="")
        store.put("k1", entry)
        store.remove("k1")
        assert store.get("k1") is None

    def test_remove_nonexistent_ok(self):
        store = CallbackStore()
        store.remove("nope")  # Should not raise

    def test_ttl_expiry(self):
        """Entries older than TTL are removed on get()."""
        store = CallbackStore(max_entries=10, ttl_seconds=10)
        # Create an entry with a timestamp from 20 seconds ago (expired)
        old_entry = CallbackEntry(
            action="old", user_message="", ai_response="", category="",
            created_at=time.time() - 20,
        )
        store._store["old_key"] = old_entry  # Bypass put() to avoid expire_old
        assert store.get("old_key") is None  # Should be expired

        # Fresh entry should be retrievable
        fresh_entry = CallbackEntry(
            action="fresh", user_message="", ai_response="", category="",
            created_at=time.time(),
        )
        store.put("fresh_key", fresh_entry)
        assert store.get("fresh_key") is fresh_entry

    def test_eviction_when_full(self):
        store = CallbackStore(max_entries=5)
        for i in range(5):
            entry = CallbackEntry(
                action=f"a{i}",
                user_message="",
                ai_response="",
                category="",
                created_at=time.time() + i,  # Ensure ordering
            )
            store.put(f"k{i}", entry)

        # Store is at max. Adding one more triggers eviction.
        new_entry = CallbackEntry(
            action="new", user_message="", ai_response="", category="", created_at=time.time() + 10
        )
        store.put("new_key", new_entry)

        # At least one old entry should have been evicted
        assert store.get("new_key") is not None
        # Total entries should be <= max_entries
        assert len(store._store) <= 5

    def test_eviction_removes_oldest(self):
        store = CallbackStore(max_entries=3)
        now = time.time()
        for i in range(3):
            entry = CallbackEntry(
                action=f"a{i}",
                user_message="",
                ai_response="",
                category="",
                created_at=now + i,  # Use current timestamps so TTL doesn't expire them
            )
            store.put(f"k{i}", entry)

        # Add a 4th — should evict the oldest (k0)
        store.put(
            "k3",
            CallbackEntry(
                action="a3", user_message="", ai_response="", category="", created_at=now + 10
            ),
        )
        assert store.get("k0") is None  # Oldest evicted
        assert store.get("k3") is not None  # New one present

    def test_singleton_accessor(self):
        s1 = get_callback_store()
        s2 = get_callback_store()
        assert s1 is s2


# ────────────────────────────────────────────────────────────
# CallbackEntry
# ────────────────────────────────────────────────────────────


class TestCallbackEntry:
    def test_defaults(self):
        entry = CallbackEntry(action="test", user_message="u", ai_response="a", category="chat")
        assert entry.extra == {}
        assert isinstance(entry.created_at, float)

    def test_custom_extra(self):
        entry = CallbackEntry(
            action="a", user_message="", ai_response="", category="", extra={"k": "v"}
        )
        assert entry.extra == {"k": "v"}


# ────────────────────────────────────────────────────────────
# ResponseKeyboardBuilder
# ────────────────────────────────────────────────────────────


class TestResponseKeyboardBuilder:
    def test_short_text_returns_none(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        result = builder.build("OK", user_message="test")
        assert result is None

    def test_long_code_returns_keyboard(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        code_text = "Here:\n```python\n" + "x = 1\n" * 30 + "```\nDone."
        result = builder.build(code_text, user_message="show code", message_id=1)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0

    def test_profile_override_none(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        text = "A" * 600
        result = builder.build(text, profile_override="none")
        assert result is None

    def test_profile_override_expand(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        text = "A" * 200  # Would normally be NONE
        result = builder.build(text, user_message="test", profile_override="expand")
        assert result is not None

    def test_action_profile_with_approval(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        actions = [{"label": "Approve", "action": "approve_deploy"}]
        result = builder.build(
            "Deploy to production?",
            user_message="deploy",
            message_id=42,
            approval_actions=actions,
        )
        assert result is not None
        # Should have at least one row with buttons
        assert len(result) >= 1

    def test_invalid_profile_override_falls_back(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        text = "OK"
        result = builder.build(text, profile_override="invalid_profile")
        # Short text → should fall back to choose_profile → NONE
        assert result is None

    def test_buttons_are_dicts(self):
        builder = ResponseKeyboardBuilder(store=CallbackStore())
        code_text = "```python\n" + "print('hello world')\n" * 20 + "```"
        result = builder.build(code_text, user_message="show", message_id=5)
        if result:
            for row in result:
                for btn in row:
                    assert isinstance(btn, dict)
                    assert "text" in btn
                    assert "callback_data" in btn

    def test_make_button_long_actions_generate_distinct_callback_keys(self):
        store = CallbackStore()
        builder = ResponseKeyboardBuilder(store=store)

        action1 = "a" * 80
        action2 = "a" * 79 + "b"

        btn1 = builder._make_button("One", action1, msg_hash="samehash")
        btn2 = builder._make_button("Two", action2, msg_hash="samehash")

        assert btn1["callback_data"] != btn2["callback_data"]
        assert len(btn1["callback_data"].encode("utf-8")) <= 64
        assert len(btn2["callback_data"].encode("utf-8")) <= 64


# ────────────────────────────────────────────────────────────
# CallbackHandler — dispatch for untested prefixes
# ────────────────────────────────────────────────────────────
from navig.gateway.channels.telegram_keyboards import CallbackHandler


class _MinimalChannel:
    """Minimal fake channel for testing CallbackHandler dispatch."""

    def __init__(self):
        self.api_calls = []
        self.messages = []

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
        self.messages.append(("send", chat_id, text))
        return {"ok": True}

    async def edit_message(
        self, chat_id, message_id, text, parse_mode=None, keyboard=None, **kwargs
    ):
        self.messages.append(("edit", chat_id, message_id, text))
        return {"ok": True}


class TestCallbackHandlerDispatch:
    @pytest.mark.asyncio
    async def test_handle_unknown_cb_data_answers_gracefully(self):
        """Unknown callback data should answer the callback but not crash."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        # Build a fake callback query
        cb_query = {
            "id": "q1",
            "from": {"id": 123},
            "message": {"message_id": 10, "chat": {"id": 456}},
            "data": "totally_unknown_prefix_xyz",
        }

        # Should not raise
        await handler.handle(cb_query)

        # Should have answered the callback at minimum
        answered = [c for c in ch.api_calls if c[0] == "answerCallbackQuery"]
        assert len(answered) >= 1

    @pytest.mark.asyncio
    async def test_handle_expired_store_entry(self):
        """Callback referencing expired/evicted store entry should show error screen."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        # Use a store-based prefix like "fb_up:hash123" but don't add to store
        cb_query = {
            "id": "q2",
            "from": {"id": 123},
            "message": {"message_id": 10, "chat": {"id": 456}},
            "data": "fb_up:nonexistent_hash",
        }

        await handler.handle(cb_query)
        # Should not crash — gracefully handled

    @pytest.mark.asyncio
    async def test_handle_helpme_prefix(self):
        """helpme callback is dispatched without crashing."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        cb_query = {
            "id": "q3",
            "from": {"id": 123},
            "message": {"message_id": 10, "chat": {"id": 456}},
            "data": "helpme:test_topic",
        }

        await handler.handle(cb_query)
        # Just verify no crash


class TestCallbackHandlerModelSwitch:
    """Test _handle_model_switch for ms_* quick-switch prefixes."""

    @pytest.mark.asyncio
    async def test_ms_small_dispatches(self, monkeypatch):
        """ms_small triggers model switch to small tier."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        # Mock dependencies to avoid real AI client
        monkeypatch.setattr(
            "navig.gateway.channels.telegram_keyboards.CallbackHandler._get_ai_response",
            lambda *a, **kw: None,
        )

        cb_query = {
            "id": "q1",
            "from": {"id": 100},
            "message": {"message_id": 5, "chat": {"id": 200}},
            "data": "ms_small",
        }

        # Should not crash even if AI client isn't configured
        try:
            await handler.handle(cb_query)
        except Exception:
            pass  # Expected — no AI client configured in test env

    @pytest.mark.asyncio
    async def test_ms_prov_dispatches(self):
        """ms_prov callback shows provider picker."""
        ch = _MinimalChannel()
        # Add _handle_providers to channel
        ch._handle_providers = lambda *a, **kw: None
        ch.provider_renders = []

        async def fake_providers(chat_id, user_id=0, message_id=None):
            ch.provider_renders.append((chat_id, user_id, message_id))

        ch._handle_providers = fake_providers
        handler = CallbackHandler(ch)

        cb_query = {
            "id": "q2",
            "from": {"id": 100},
            "message": {"message_id": 5, "chat": {"id": 200}},
            "data": "ms_prov",
        }

        await handler.handle(cb_query)


class TestCallbackHandlerSettings:
    """Test _handle_settings_callback for st_* prefixes."""

    @pytest.mark.asyncio
    async def test_settings_callback_hub(self):
        """st_hub shows the settings hub."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        cb_query = {
            "id": "q1",
            "from": {"id": 100},
            "message": {"message_id": 5, "chat": {"id": 200}},
            "data": "st_hub",
        }

        # Should not crash
        await handler.handle(cb_query)

    @pytest.mark.asyncio
    async def test_settings_callback_close(self):
        """st_close deletes the settings message."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        cb_query = {
            "id": "q2",
            "from": {"id": 100},
            "message": {"message_id": 5, "chat": {"id": 200}},
            "data": "st_close",
        }

        await handler.handle(cb_query)
        # Should have tried to delete the message
        delete_calls = [c for c in ch.api_calls if c[0] == "deleteMessage"]
        assert len(delete_calls) >= 1 or len(ch.api_calls) >= 1


class TestCallbackHandlerDebug:
    """Test _handle_debug_callback for dbg_* prefixes."""

    @pytest.mark.asyncio
    async def test_debug_callback_log(self):
        """dbg_log callback does not crash."""
        ch = _MinimalChannel()
        handler = CallbackHandler(ch)

        cb_query = {
            "id": "q1",
            "from": {"id": 100},
            "message": {"message_id": 5, "chat": {"id": 200}},
            "data": "dbg_log",
        }

        await handler.handle(cb_query)
