"""`tyler` on a photo: the trigger vanished and nothing happened.

Reported 2026-09-29. The gateway log showed the owner's `tyler`, then its deletion,
then nothing — no send, no warning, no error. Three causes, all fixed here:

1. **Vision never ran.** The image engine looked the OpenAI key up by
   env-variable name only; the operator's key is stored under the provider id
   (`navig vault add openai`, "openai: key configured (vault:openai)" in doctor).
   A photo with no text in it therefore read as nothing at all, and the role gave
   up. Reproduced on the live install: description None before, a real
   description (HTTP 200) after. Same class as #642.
2. **The failure was cached.** Every image analysed while vision was dead was
   stored as "no description", so retrying on the same photo kept failing.
3. **The trigger's own deletion was reported as a deletion** — `business
   deletion: chat=429106811 ids=1` right after the `tyler` — and would have
   appeared in the digest as a message someone deleted.

And the detached role tasks logged only on an exception, at debug: now every run
records its outcome, and a refused send tells the owner instead of vanishing.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.autoreply as ar
import navig.telegram.deletions as d
from navig.gateway.channels.media_engine import credentials
from navig.gateway.channels.media_engine import image as im

# ── 1. the key is found where the operator put it ─────────────────────────────


def _no_env_secret(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("navig.vault.resolver.resolve_secret", lambda names: None)


def test_a_key_stored_by_provider_id_is_found(monkeypatch):
    _no_env_secret(monkeypatch)
    vault = MagicMock()
    vault.get_api_key = MagicMock(return_value="sk-from-vault")
    monkeypatch.setattr("navig.vault.get_vault", lambda: vault)

    assert credentials.provider_key("openai", "OPENAI_API_KEY") == "sk-from-vault"
    assert vault.get_api_key.call_args.args[0] == "openai", "looked up by PROVIDER id"


def test_an_explicit_env_key_still_wins(monkeypatch):
    monkeypatch.setattr("navig.vault.resolver.resolve_secret", lambda names: "sk-env")
    vault = MagicMock()
    monkeypatch.setattr("navig.vault.get_vault", lambda: vault)
    assert credentials.provider_key("openai", "OPENAI_API_KEY") == "sk-env"
    vault.get_api_key.assert_not_called()


def test_no_key_anywhere_is_none_not_an_error(monkeypatch):
    _no_env_secret(monkeypatch)

    def _locked():
        raise RuntimeError("vault locked")

    monkeypatch.setattr("navig.vault.get_vault", _locked)
    assert credentials.provider_key("openai", "OPENAI_API_KEY") is None


def test_both_openai_stages_use_the_shared_lookup():
    """Vision AND the audio engine's Whisper made the same miss."""
    import inspect

    from navig.gateway.channels.media_engine import audio

    for mod, fn in ((im, im._stage_vision), (audio, audio._stage_whisper)):
        src = inspect.getsource(fn)
        assert 'provider_key("openai"' in src, f"{mod.__name__}.{fn.__name__} bypasses it"
        assert '_env("OPENAI_API_KEY")' not in src


# ── 2. a failed analysis is neither stored nor trusted ───────────────────────


class _Cache:
    def __init__(self, seeded=None):
        self.data = dict(seeded or {})
        self.puts = []

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.puts.append(key)
        self.data[key] = value


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stages(monkeypatch):
    vision = AsyncMock(return_value=None)
    monkeypatch.setattr(im, "_stage_vision", vision)
    monkeypatch.setattr(im, "_stage_ocr", lambda b: None)
    monkeypatch.setattr(im, "_stage_serpapi", AsyncMock(return_value=None))
    monkeypatch.setattr(im, "_stage_landmark", AsyncMock(return_value=None))
    return vision


def test_an_undescribed_result_is_not_cached(stages):
    cache = _Cache()
    out = asyncio.run(im.ImageEngine(cache=cache).analyze(_png()))
    assert out.get("description") is None
    assert cache.puts == [], "a failed vision run must not become this image's answer"


def test_a_described_result_is_cached(stages):
    stages.return_value = "a red square"
    cache = _Cache()
    asyncio.run(im.ImageEngine(cache=cache).analyze(_png()))
    assert len(cache.puts) == 1


def test_a_poisoned_cache_entry_is_re_analysed(stages):
    """Entries stored while vision was dead must not keep failing the same photo."""
    data = _png()
    key = im.MediaCache.key(data)
    stages.return_value = "a red square"
    cache = _Cache({key: {"description": None, "ocr_text": None, "card": "x"}})

    out = asyncio.run(im.ImageEngine(cache=cache).analyze(data))

    assert out["description"] == "a red square"
    stages.assert_awaited_once()


# ── 3. NAVIG's own deletions are not news ─────────────────────────────────────


@pytest.fixture
def own(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_own_deletions_path", lambda: tmp_path / "own.json")


def test_a_noted_deletion_is_recognised_once(own):
    d.note_own_deletion("bc1", [90])
    assert d.is_own_deletion("bc1", 90) is True
    assert d.is_own_deletion("bc1", 90) is False, "consumed — a second report is real"
    assert d.is_own_deletion("bc2", 90) is False, "scoped by connection"


def test_a_note_is_scoped_by_chat(own):
    """A message id is unique only within a chat; one connection spans many chats.
    A real deletion of the same id elsewhere must not consume — or hide behind — ours."""
    d.note_own_deletion("bc1", [5], chat_id=111)
    assert d.is_own_deletion("bc1", 5, chat_id=222) is False, "another chat's id 5 is real"
    assert d.is_own_deletion("bc1", 5, chat_id=111) is True, "our note was not consumed"


def test_an_old_note_does_not_hide_a_real_deletion(own):
    d.note_own_deletion("bc1", [90], now=1000.0)
    assert d.is_own_deletion("bc1", 90, now=1000.0 + d.OWN_DELETION_TTL_SEC + 1) is False


async def test_the_role_trigger_is_noted_before_it_is_deleted(own):
    """Noted BEFORE the call, so Telegram's report can never outrun it."""
    order = []
    ch = MagicMock()

    async def api(method, data=None):
        order.append(("api", method, d.is_own_deletion("bc1", 90, chat_id=555)))
        return True

    ch._api_call = AsyncMock(side_effect=api)
    await ar._delete(ch, "bc1", 555, 90)
    assert order == [("api", "deleteBusinessMessages", True)]


async def test_a_reply_keyword_trigger_is_noted_too(own):
    from navig.telegram import reply_actions

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)
    await reply_actions._delete_business_trigger(ch, "bc1", 91, chat_id=555)
    assert d.is_own_deletion("bc1", 91, chat_id=555)


async def test_a_chat_command_trigger_is_noted_too(own):
    from navig.telegram import biz_commands

    ch = MagicMock()
    ch._api_call = AsyncMock(return_value=True)
    await biz_commands._delete(ch, 555, "bc1", 92)
    assert d.is_own_deletion("bc1", 92, chat_id=555)


# ── the role run always ends in something the owner can see ──────────────────


async def test_a_refused_role_reply_is_told_to_the_owner(monkeypatch):
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Хорошо."))
    ch = MagicMock()
    ch.send_message = AsyncMock()

    async def api(method, data=None):
        # Telegram refuses both the in-place edit and the fallback send.
        return None if method in ("sendMessage", "editMessageText") else True

    ch._api_call = AsyncMock(side_effect=api)
    msg = {
        "chat": {"id": 555},
        "message_id": 90,
        "business_connection_id": "bc1",
        "from": {"id": 777},
        "text": "tyler",
        "reply_to_message": {"message_id": 80, "text": "привет", "from": {"id": 555}},
    }
    await ar.maybe_role_reply(ch, msg, is_owner=True, owner_id=777)
    await asyncio.gather(*list(ar._bg_tasks))

    assert "could not be sent" in ch.send_message.await_args.args[1]
