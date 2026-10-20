from __future__ import annotations

import pytest

from navig.gateway.channels.telegram import TelegramChannel

pytestmark = pytest.mark.integration


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload: bytes):
        self._payload = payload

    def get(self, _url: str):
        return _FakeResponse(self._payload)


async def test_handle_photo_vision_appends_ocr_snippet(monkeypatch):
    sent: list[tuple[int, str, str | None]] = []

    class _Channel(TelegramChannel):
        async def send_message(self, chat_id, text, parse_mode="HTML", **kwargs):
            sent.append((chat_id, text, parse_mode))
            return {"ok": True}

        async def _api_call(self, method, data):
            if method == "getFile":
                return {"file_path": "photos/x.jpg"}
            return {"ok": True}

        async def _call_vision_api(self, *args, **kwargs):
            return "Vision summary"

    channel = _Channel("test-token")
    channel._session = _FakeSession(b"fake-image-bytes")

    monkeypatch.setattr(
        "navig.providers.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr(
        "navig.providers.discovery.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr("navig.providers.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr("navig.providers.discovery.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr(channel, "_extract_photo_ocr_text", lambda _b: "Invoice #123")

    result = await channel._handle_photo_vision(
        chat_id=11,
        user_id=22,
        is_group=False,
        message={"photo": [{"file_id": "abc", "file_size": 10}]},
    )

    assert sent
    _chat_id, text, parse_mode = sent[-1]
    assert "Vision summary" in text
    assert "OCR" in text
    assert "Invoice #123" in text
    assert parse_mode == "HTML"
    # Regression: the OCR must be RETURNED to the caller, not just posted to the
    # chat — a bubble alone leaves the agent blind to the photo's content.
    assert result is not None
    assert result.ocr_text == "Invoice #123"
    assert result.description == "Vision summary"


async def test_handle_photo_vision_sends_ocr_when_vision_empty(monkeypatch):
    sent: list[tuple[int, str, str | None]] = []

    class _Channel(TelegramChannel):
        async def send_message(self, chat_id, text, parse_mode="HTML", **kwargs):
            sent.append((chat_id, text, parse_mode))
            return {"ok": True}

        async def _api_call(self, method, data):
            if method == "getFile":
                return {"file_path": "photos/x.jpg"}
            return {"ok": True}

        async def _call_vision_api(self, *args, **kwargs):
            return None

    channel = _Channel("test-token")
    channel._session = _FakeSession(b"fake-image-bytes")

    monkeypatch.setattr(
        "navig.providers.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr(
        "navig.providers.discovery.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr("navig.providers.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr("navig.providers.discovery.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr(channel, "_extract_photo_ocr_text", lambda _b: "Only OCR text")

    result = await channel._handle_photo_vision(
        chat_id=11,
        user_id=22,
        is_group=False,
        message={"photo": [{"file_id": "abc", "file_size": 10}]},
    )

    assert sent
    _chat_id, text, _parse_mode = sent[-1]
    assert "Only OCR text" in text
    assert "Vision analysis failed" not in text
    # The incident's shape: OCR present, vision description empty — must still
    # hand something back so the agent sees the text.
    assert result is not None
    assert result.ocr_text == "Only OCR text"
    assert result.description is None


async def test_process_update_captioned_photo_runs_photo_handler_and_text_flow(monkeypatch):
    seen: dict[str, object] = {"photo_called": False, "nl_text": None}

    async def _on_message(*_args, **_kwargs):
        return "ok"

    channel = TelegramChannel("test-token", on_message=_on_message, require_auth=False)

    async def _photo_handler(chat_id, user_id, is_group, message):
        seen["photo_called"] = True

    async def _nl_handler(chat_id, user_id, text, is_group, username, metadata):
        seen["nl_text"] = text
        return True

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr(channel, "_handle_photo_vision", _photo_handler)
    monkeypatch.setattr(channel, "_handle_natural_language_request", _nl_handler)
    async def _pending_reply(**_kwargs):
        return False

    async def _intake_reply(**_kwargs):
        return False

    monkeypatch.setattr(channel, "_handle_nl_pending_reply", _pending_reply)
    monkeypatch.setattr(channel, "_handle_intake_reply", _intake_reply)

    await channel._process_update(
        {
            "message": {
                "message_id": 101,
                "chat": {"id": 50, "type": "private"},
                "from": {"id": 77, "username": "u"},
                "caption": "scan this",
                "photo": [{"file_id": "abc", "file_size": 10}],
            }
        }
    )

    assert seen["photo_called"] is True
    assert seen["nl_text"] == "scan this"


async def test_captioned_photo_hands_ocr_and_description_to_metadata(monkeypatch):
    """The OCR/description must travel from the vision handler into metadata,
    so the LLM turn can include them (the agent previously saw the caption only
    and replied "I don't know which subscription/date you mean")."""
    from navig.gateway.channels.telegram import PhotoVisionResult

    seen: dict[str, object] = {"metadata": None}

    async def _on_message(*_args, **_kwargs):
        return "ok"

    channel = TelegramChannel("test-token", on_message=_on_message, require_auth=False)

    async def _photo_handler(chat_id, user_id, is_group, message):
        return PhotoVisionResult(
            "This workspace's Creator Plan renews on October 28.",
            "A billing page",
        )

    async def _nl_handler(chat_id, user_id, text, is_group, username, metadata):
        seen["metadata"] = metadata
        return True

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr(channel, "_handle_photo_vision", _photo_handler)
    monkeypatch.setattr(channel, "_handle_natural_language_request", _nl_handler)

    async def _pending_reply(**_kwargs):
        return False

    async def _intake_reply(**_kwargs):
        return False

    monkeypatch.setattr(channel, "_handle_nl_pending_reply", _pending_reply)
    monkeypatch.setattr(channel, "_handle_intake_reply", _intake_reply)

    await channel._process_update(
        {
            "message": {
                "message_id": 101,
                "chat": {"id": 50, "type": "private"},
                "from": {"id": 77, "username": "u"},
                "caption": "what does this say?",
                "photo": [{"file_id": "abc", "file_size": 10}],
            }
        }
    )

    metadata = seen["metadata"]
    assert metadata is not None
    block = metadata.get("photo_context", "")
    assert "Creator Plan renews on October 28." in block
    assert "A billing page" in block


async def test_handle_talk_prepends_photo_context_to_the_llm_only():
    """The block rides the LLM message; original_text (and everything downstream
    — limits, session recording) stays the clean caption."""
    captured: dict[str, str] = {}

    class _Channel(TelegramChannel):
        async def _stream_reply(
            self, chat_id, user_id, llm_message, metadata, *, original_text, **kwargs
        ):
            captured["llm"] = llm_message
            captured["original"] = original_text
            return ""

    channel = _Channel("test-token")
    metadata = {"photo_context": "[Photo attached — OCR]\nrenews on October 28"}
    await channel._handle_talk(
        "добавь за три дня", 1, 2, metadata, None, None, False
    )

    assert captured["llm"].startswith("[Photo attached — OCR]")
    assert captured["llm"].endswith("добавь за три дня")
    assert captured["original"] == "добавь за три дня"


async def test_handle_reason_prepends_photo_context_to_the_enriched_message():
    captured: dict[str, str] = {}

    class _Channel(TelegramChannel):
        async def _stream_reply(
            self, chat_id, user_id, llm_message, metadata, *, original_text, **kwargs
        ):
            captured["llm"] = llm_message
            captured["original"] = original_text
            return ""

    channel = _Channel("test-token")
    metadata = {"photo_context": "[Photo attached — OCR]\nrenews on October 28"}
    await channel._handle_reason(
        "когда продление?", 1, 2, metadata, None, None, False
    )

    assert captured["llm"].startswith("[Photo attached — OCR]")
    assert "когда продление?" in captured["llm"]
    assert captured["original"] == "когда продление?"


def test_compose_photo_context_block_caps_ocr_and_skips_empty():
    from navig.gateway.channels.telegram import (
        _PHOTO_OCR_AGENT_CHARS,
        PhotoVisionResult,
    )

    channel = TelegramChannel("test-token")
    assert channel._compose_photo_context_block(None) == ""
    assert channel._compose_photo_context_block(PhotoVisionResult(None, None)) == ""
    assert channel._compose_photo_context_block(PhotoVisionResult("  ", "")) == ""

    long_ocr = "x" * (_PHOTO_OCR_AGENT_CHARS + 500)
    block = channel._compose_photo_context_block(
        PhotoVisionResult(long_ocr, "A billing page")
    )
    ocr_part, desc_part = block.split("\n\n", 1)
    assert ocr_part.startswith("[Photo attached — OCR]\n")
    # capped + the ellipsis
    assert len(ocr_part) <= len("[Photo attached — OCR]\n") + _PHOTO_OCR_AGENT_CHARS + 1
    assert desc_part == "[Image description]\nA billing page"


# ── Bare photo (no caption) → one agent turn ─────────────────────────────────


def _bare_photo_update(chat_type: str = "private") -> dict:
    return {
        "message": {
            "message_id": 202,
            "chat": {"id": 50, "type": chat_type},
            "from": {"id": 77, "username": "u"},
            "photo": [{"file_id": "abc", "file_size": 10}],
        }
    }


def _bare_channel(monkeypatch, *, photo_result, has_sessions: bool = False):
    """A channel wired to reach `_dispatch_by_mode` with everything captured."""
    seen: dict[str, object] = {"dispatched": 0, "metadata": None, "clean_text": None}

    async def _on_message(*_args, **_kwargs):
        return "ok"

    channel = TelegramChannel("test-token", on_message=_on_message, require_auth=False)

    async def _photo_handler(chat_id, user_id, is_group, message):
        return photo_result

    async def _dispatch(tg_msg, clean_text, **kwargs):
        seen["dispatched"] += 1
        seen["clean_text"] = clean_text
        seen["metadata"] = kwargs.get("metadata")

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", has_sessions)
    monkeypatch.setattr(channel, "_handle_photo_vision", _photo_handler)
    monkeypatch.setattr(channel, "_dispatch_by_mode", _dispatch)
    return channel, seen


async def test_bare_photo_gets_an_agent_turn_with_photo_context(monkeypatch):
    """The whole feature: a photo with NO caption still reaches the agent, with
    the OCR in metadata — and the OCR must NEVER leak into `text` (that would
    hit ACT/CODE classification, slash hijack, appointments, the NL gate)."""
    from navig.gateway.channels.telegram import PhotoVisionResult

    channel, seen = _bare_channel(
        monkeypatch,
        photo_result=PhotoVisionResult("Creator Plan renews on October 28.", "A billing page"),
    )

    await channel._process_update(_bare_photo_update())

    assert seen["dispatched"] == 1
    block = seen["metadata"].get("photo_context", "")
    assert "Creator Plan renews on October 28." in block
    assert "A billing page" in block
    assert "Invoice" not in seen["clean_text"] and "OCR" not in seen["clean_text"]
    # The synthetic text is the ≤2-word constant that classifies as TALK.
    from navig.gateway.channels.telegram import _BARE_PHOTO_TEXT

    assert seen["clean_text"] == _BARE_PHOTO_TEXT


async def test_bare_photo_without_vision_result_never_dispatches(monkeypatch):
    channel, seen = _bare_channel(monkeypatch, photo_result=None)

    await channel._process_update(_bare_photo_update())

    assert seen["dispatched"] == 0, "an agent turn with no OCR to read is noise"


async def test_bare_photo_with_empty_vision_result_never_dispatches(monkeypatch):
    from navig.gateway.channels.telegram import PhotoVisionResult

    channel, seen = _bare_channel(monkeypatch, photo_result=PhotoVisionResult(None, None))

    await channel._process_update(_bare_photo_update())

    assert seen["dispatched"] == 0


async def test_group_bare_photo_without_a_usable_gate_stays_bubble_only(monkeypatch):
    """Degraded group config (sessions off / bot username unknown): the old
    bubble-only behaviour — never an unprompted group reply."""
    from navig.gateway.channels.telegram import PhotoVisionResult

    channel, seen = _bare_channel(
        monkeypatch, photo_result=PhotoVisionResult("text", "desc"), has_sessions=False
    )
    photo_calls: list[int] = []

    async def _photo(chat_id, user_id, is_group, message):
        photo_calls.append(1)
        return PhotoVisionResult("text", "desc")

    monkeypatch.setattr(channel, "_handle_photo_vision", _photo)

    await channel._process_update(_bare_photo_update(chat_type="supergroup"))

    assert photo_calls, "the vision bubble still posts in groups"
    assert seen["dispatched"] == 0


async def test_group_bare_photo_without_mention_dispatches_nothing(monkeypatch):
    """With the mention gate in force, "Photo attached" carries no @mention →
    no agent turn; the vision bubble (posted earlier, as always) is unchanged."""
    from navig.gateway.channels.telegram import PhotoVisionResult

    channel, seen = _bare_channel(
        monkeypatch, photo_result=PhotoVisionResult("text", "desc"), has_sessions=True
    )
    channel._bot_username = "navig_test_bot"
    photo_calls: list[int] = []

    async def _photo(chat_id, user_id, is_group, message):
        photo_calls.append(1)
        return PhotoVisionResult("text", "desc")

    monkeypatch.setattr(channel, "_handle_photo_vision", _photo)

    await channel._process_update(_bare_photo_update(chat_type="supergroup"))

    assert photo_calls, "the vision bubble still posts"
    assert seen["dispatched"] == 0, "a group photo without a mention must not reply"


async def test_bare_photo_respects_auth(monkeypatch):
    """The bare-photo branch used to run vision (and nothing else) BEFORE the
    access gate; now the agent turn goes through the same auth check as text."""
    from navig.gateway.channels.telegram import PhotoVisionResult

    seen: dict[str, object] = {"dispatched": 0}

    async def _on_message(*_args, **_kwargs):
        return "ok"

    # require_auth=True with no allowed_users → deny everyone.
    channel = TelegramChannel("test-token", on_message=_on_message, require_auth=True)

    async def _photo(chat_id, user_id, is_group, message):
        return PhotoVisionResult("text", "desc")

    async def _dispatch(*_args, **_kwargs):
        seen["dispatched"] += 1

    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)
    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_DECOY", False)
    monkeypatch.setattr(channel, "_handle_photo_vision", _photo)
    monkeypatch.setattr(channel, "_dispatch_by_mode", _dispatch)

    await channel._process_update(_bare_photo_update())

    assert seen["dispatched"] == 0


def test_the_bare_photo_text_is_inert_for_every_gate():
    """The synthetic constant must not trip the gates it can reach.

    classify_mode → TALK (the modes that DON'T prepend photo_context are
    ACT/CODE), ≤2 words keeps the NL-intent gate shut, no system intent, no
    reply-keyword, no slash.
    """
    from navig.gateway.channels.telegram import _BARE_PHOTO_TEXT
    from navig.gateway.channels.telegram_mode_classifier import (
        _match_system_intent,
        classify_mode,
    )
    from navig.telegram import reply_actions

    assert len(_BARE_PHOTO_TEXT.split()) <= 2
    assert classify_mode(_BARE_PHOTO_TEXT) == "TALK"
    assert _match_system_intent(_BARE_PHOTO_TEXT) is None
    assert reply_actions.parse(_BARE_PHOTO_TEXT) == (None, "")
    assert not _BARE_PHOTO_TEXT.startswith("/")


async def test_the_ocr_bubble_is_sent_tagged_for_the_system_sweep(monkeypatch):
    """telegram.system_cleanup sweeps what senders tagged `system` — the OCR
    bubble is a machine extract, not conversation, so it must go out tagged."""
    from navig.telegram import chat_tidy

    seen: dict[str, object] = {}

    class _Channel(TelegramChannel):
        async def send_message(self, chat_id, text, parse_mode="HTML", **kwargs):
            seen["kind"] = chat_tidy._KIND.get()
            seen["routine"] = chat_tidy._ROUTINE.get()
            return {"ok": True, "result": {"message_id": 1, "chat": {"id": chat_id, "type": "private"}, "date": 0}}

        async def _api_call(self, method, data):
            if method == "getFile":
                return {"file_path": "photos/x.jpg"}
            return {"ok": True}

        async def _call_vision_api(self, *args, **kwargs):
            return "Vision summary"

    channel = _Channel("test-token")
    channel._session = _FakeSession(b"fake-image-bytes")
    # Both levels: navig.providers lazily re-exports these, and its __getattr__
    # caches into the package globals — patching only discovery leaves the fake
    # unrecorded there for the rest of the session (patch-hygiene guard).
    monkeypatch.setattr(
        "navig.providers.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr(
        "navig.providers.discovery.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr("navig.providers.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr(
        "navig.providers.discovery.get_vision_api_format", lambda _pid: "openai"
    )
    monkeypatch.setattr(channel, "_extract_photo_ocr_text", lambda _b: "Invoice #123")

    token = chat_tidy._ROUTINE.set(False)
    kind_token = chat_tidy._KIND.set("notice")
    try:
        await channel._handle_photo_vision(
            chat_id=11,
            user_id=22,
            is_group=False,
            message={"photo": [{"file_id": "abc", "file_size": 10}]},
        )
    finally:
        chat_tidy._KIND.reset(kind_token)
        chat_tidy._ROUTINE.reset(token)

    assert seen == {"kind": "system", "routine": True}
