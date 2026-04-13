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
        "navig.providers.discovery.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr("navig.providers.discovery.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr(channel, "_extract_photo_ocr_text", lambda _b: "Invoice #123")

    await channel._handle_photo_vision(
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
        "navig.providers.discovery.resolve_vision_model",
        lambda _overrides: ("openai", "gpt-4o", "cfg"),
    )
    monkeypatch.setattr("navig.providers.discovery.get_vision_api_format", lambda _pid: "openai")
    monkeypatch.setattr(channel, "_extract_photo_ocr_text", lambda _b: "Only OCR text")

    await channel._handle_photo_vision(
        chat_id=11,
        user_id=22,
        is_group=False,
        message={"photo": [{"file_id": "abc", "file_size": 10}]},
    )

    assert sent
    _chat_id, text, _parse_mode = sent[-1]
    assert "Only OCR text" in text
    assert "Vision analysis failed" not in text


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
