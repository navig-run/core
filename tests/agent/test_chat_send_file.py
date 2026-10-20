"""`chat_send_file` — the agent can hand a generated file back to the chat.

Owner ask 2026-10-15: "wire music/sound generation to Telegram". Generation
already existed (`navig audio gen`); what never existed was a chat-send tool —
the model could produce a file and had nowhere to put it. These tests pin the
three things that make it real rather than written:

1. it is REGISTERED in the `core` toolset (offered every turn — the
   "written, tested, never wired" class of defect),
2. routing comes from the injected `_session_id`, never from the model
   (absent from the schema, uns spoofable),
3. the receipt is honest — a transport failure is reported as a failure.
"""

from __future__ import annotations

import pytest

from navig.agent.tools.chat_media_tools import (
    MAX_SEND_BYTES,
    ChatSendFileTool,
    attachment_kind,
    split_session,
)

pytestmark = pytest.mark.integration


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeReceipt:
    def __init__(self, ok: bool, error: str | None = None, message_id: str | None = None):
        self.ok = ok
        self.error = error
        self.message_id = message_id


class _FakeAdapter:
    """Stands in for the Telegram messaging adapter, capturing the send."""

    def __init__(self, ok: bool = True, error: str | None = None):
        self.ok = ok
        self.error = error
        self.calls: list[tuple[str, str, list[dict]]] = []

    async def send_message(self, thread_id, text, attachments=None):
        self.calls.append((str(thread_id), text, attachments or []))
        if self.ok:
            return _FakeReceipt(True, message_id="42")
        return _FakeReceipt(False, error=self.error or "transport rejected")


class _FakeRegistry:
    def __init__(self, adapter):
        self._adapter = adapter

    def get(self, name):
        return self._adapter if name == "telegram" else None


@pytest.fixture
def adapter(monkeypatch):
    """Install a fake adapter registry the tool will find."""
    fake = _FakeAdapter()
    import navig.messaging.adapter_registry as ar

    monkeypatch.setattr(ar, "get_adapter_registry", lambda: _FakeRegistry(fake))
    return fake


def _args(path, session="telegram:user:12345", **extra):
    return {"path": str(path), "_session_id": session, **extra}


# ── wiring ────────────────────────────────────────────────────────────────────


def test_registered_in_core_and_offered_every_turn():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_all_tools

    register_all_tools()
    names = _AGENT_REGISTRY.available_names()
    assert "chat_send_file" in names, (
        "an unregistered chat_send_file is the written-tested-never-wired defect"
    )
    entry = _AGENT_REGISTRY._entries["chat_send_file"]
    assert entry.toolset == "core", "core is the every-turn toolset"


def test_schema_hides_the_session_key_and_declares_needs_session():
    tool = ChatSendFileTool()
    assert tool.needs_session is True, (
        "without needs_session the agent loop never injects _session_id "
        "and every send degrades to 'no chat session'"
    )
    param_names = {p["name"] for p in tool.parameters}
    assert param_names == {"path", "caption"}
    assert "_session_id" not in param_names, "the model must not see or spoof routing"


# ── routing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("telegram:user:12345", ("telegram", "12345")),
        ("telegram:group:-100123", ("telegram", "-100123")),
        ("cli:default", ("cli", "default")),
        ("", None),
        ("telegram", None),  # no thread segment
        ("telegram:user:abc", None),  # telegram threads are numeric — validated here
        (None, None),
    ],
)
def test_split_session(key, expected):
    assert split_session(key) == expected


def test_kind_mapping():
    assert attachment_kind("/x/song.mp3") == "audio"
    assert attachment_kind("/x/SHOT.PNG") == "photo"
    assert attachment_kind("/x/clip.mp4") == "video"
    assert attachment_kind("/x/anim.gif") == "animation"
    assert attachment_kind("/x/notes.txt") == "document"


# ── delivery ──────────────────────────────────────────────────────────────────


async def test_happy_path_delivers_to_the_sessions_chat(tmp_path, adapter):
    track = tmp_path / "dark-horror.mp3"
    track.write_bytes(b"ID3fakeaudio")

    result = await ChatSendFileTool().run(_args(track, caption="horror drone"))

    assert result.success, result.output
    assert "dark-horror.mp3" in result.output and "12345" in result.output
    thread, text, attachments = adapter.calls[0]
    assert thread == "12345"
    assert text == "horror drone"
    assert attachments[0]["kind"] == "audio"
    assert attachments[0]["filename"] == "dark-horror.mp3"
    assert attachments[0]["path"] == str(track)


async def test_a_missing_file_fails_before_touching_the_adapter(tmp_path, adapter):
    result = await ChatSendFileTool().run(_args(tmp_path / "nope.mp3"))
    assert not result.success and result.error == "file not found"
    assert adapter.calls == []


async def test_no_chat_session_says_so(tmp_path, adapter):
    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")
    # A session key with no channel:thread shape at all (not `cli:default` —
    # that one has an address, and its honest answer is the adapter-missing
    # message; this is the no-address path).
    result = await ChatSendFileTool().run(_args(track, session=""))
    assert not result.success
    assert "no chat address" in result.output
    assert adapter.calls == [], "a send with no address must not guess a chat"


async def test_disconnected_adapter_is_an_honest_failure(tmp_path, monkeypatch):
    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")
    import navig.messaging.adapter_registry as ar

    monkeypatch.setattr(ar, "get_adapter_registry", lambda: _FakeRegistry(None))
    result = await ChatSendFileTool().run(_args(track))
    assert not result.success
    assert "NOT sent" in result.output


async def test_transport_failure_is_reported_as_failure(tmp_path, monkeypatch):
    fake = _FakeAdapter(ok=False, error="rate limit")
    import navig.messaging.adapter_registry as ar

    monkeypatch.setattr(ar, "get_adapter_registry", lambda: _FakeRegistry(fake))
    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")
    result = await ChatSendFileTool().run(_args(track))
    assert not result.success
    assert "rate limit" in result.output, "the operator sees WHY it failed"
    assert "delivered" not in result.output.lower()


async def test_oversize_refuses_before_reading(tmp_path, adapter, monkeypatch):
    from navig.agent.tools import chat_media_tools as mod

    monkeypatch.setattr(mod, "MAX_SEND_BYTES", 8)
    track = tmp_path / "big.mp3"
    track.write_bytes(b"0123456789abcdef")
    result = await ChatSendFileTool().run(_args(track))
    assert not result.success
    assert "limit" in result.output
    assert adapter.calls == []


def test_the_cap_is_the_telegram_bot_limit():
    assert MAX_SEND_BYTES == 50 * 1024 * 1024
