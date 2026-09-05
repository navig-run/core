"""The card's buttons: every one reaches a worker, exactly once, and a bot-wall
is named rather than flattened into a generic failure.

Two defects sat here. `_do_download` was the only action with no
`TikTokBlocked` handler — and its three siblings' handlers were **dead anyway**,
because `engine.fetch_file` never raised that type (only the metadata paths
classified). And nothing stopped a second tap: Telegram's "Working…" toast fades
in a couple of seconds while these run for tens, so tapping again is the natural
thing to do, and each action starts by downloading the clip again — wasted
bandwidth plus an extra request at exactly the moment TikTok is deciding whether
we look like a bot.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("navig_download", reason="tiktok actions need navig-download")

from navig.telegram import tiktok_actions  # noqa: E402

_URL = "https://www.tiktok.com/@x/video/1"
_CB = f"tk:{{}}:{-100}:{7}"


class _Channel:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def send_rich_message(self, chat_id, markdown=None, **kw):
        self.messages.append(markdown or "")
        return {"message_id": 2}


@pytest.fixture(autouse=True)
def _allow_everything(monkeypatch):
    monkeypatch.setattr(tiktok_actions.permissions, "can_use", lambda *a, **kw: True)
    # Module-level state: a leftover entry would silently disable a button for
    # every later test in the process.
    tiktok_actions._IN_FLIGHT.clear()
    yield
    tiktok_actions._IN_FLIGHT.clear()


# ── the table and the keyboard must agree ─────────────────────────────────────


class TestButtonTable:
    def test_every_card_button_has_a_worker(self):
        """A button whose action is missing from the table does NOTHING — no
        error, no reply, just a tap that vanishes."""
        import re

        src = __import__("inspect").getsource(tiktok_actions.offer_card)
        actions = set(re.findall(r'"callback_data": f"tk:(\w+):', src))
        assert actions, "could not read the keyboard — the regex is stale"
        assert actions == set(tiktok_actions._WORKERS), (
            f"keyboard {sorted(actions)} vs table {sorted(tiktok_actions._WORKERS)}"
        )

    def test_an_unknown_action_is_ignored_not_crashed(self):
        assert tiktok_actions._worker_for("zz") is None


# ── in-flight de-duplication ──────────────────────────────────────────────────


class TestDoubleTap:
    async def test_a_second_tap_does_not_start_a_second_download(self, monkeypatch):
        started = {"n": 0}
        gate = asyncio.Event()

        async def _slow(channel, chat_id, url):
            started["n"] += 1
            await gate.wait()

        monkeypatch.setattr(tiktok_actions, "_do_transcript", _slow)
        monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _URL)
        ch = _Channel()

        first = asyncio.create_task(
            tiktok_actions.handle_callback(ch, _CB.format("tr"), 5, 9, 1, source_text=_URL)
        )
        await asyncio.sleep(0)  # let the first tap claim the slot
        await tiktok_actions.handle_callback(
            ch, _CB.format("tr"), 5, 9, 1, source_text=_URL
        )
        gate.set()
        await first

        assert started["n"] == 1, "the second tap ran the worker again"
        assert any("Already working" in m for m in ch.messages), ch.messages

    async def test_the_slot_is_released_after_it_finishes(self, monkeypatch):
        runs = {"n": 0}

        async def _fast(channel, chat_id, url):
            runs["n"] += 1

        monkeypatch.setattr(tiktok_actions, "_do_transcript", _fast)
        monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _URL)
        ch = _Channel()

        for _ in range(2):
            await tiktok_actions.handle_callback(
                ch, _CB.format("tr"), 5, 9, 1, source_text=_URL
            )

        assert runs["n"] == 2, "a sequential re-tap must work"
        assert not tiktok_actions._IN_FLIGHT

    async def test_a_crashing_worker_does_not_wedge_the_button(self, monkeypatch):
        """A stuck entry would disable that button for the rest of the process."""

        async def _boom(channel, chat_id, url):
            raise RuntimeError("nope")

        monkeypatch.setattr(tiktok_actions, "_do_transcript", _boom)
        monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _URL)

        with pytest.raises(RuntimeError):
            await tiktok_actions.handle_callback(
                _Channel(), _CB.format("tr"), 5, 9, 1, source_text=_URL
            )
        assert not tiktok_actions._IN_FLIGHT

    async def test_a_different_action_on_the_same_clip_is_not_blocked(self, monkeypatch):
        """Transcript running must not stop the user asking for the audio."""
        gate = asyncio.Event()
        ran: list[str] = []

        async def _slow(channel, chat_id, url):
            ran.append("tr")
            await gate.wait()

        async def _other(channel, chat_id, url):
            ran.append("au")

        monkeypatch.setattr(tiktok_actions, "_do_transcript", _slow)
        monkeypatch.setattr(tiktok_actions, "_do_audio", _other)
        monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _URL)
        ch = _Channel()

        first = asyncio.create_task(
            tiktok_actions.handle_callback(ch, _CB.format("tr"), 5, 9, 1, source_text=_URL)
        )
        await asyncio.sleep(0)
        await tiktok_actions.handle_callback(
            ch, _CB.format("au"), 5, 9, 1, source_text=_URL
        )
        gate.set()
        await first

        assert ran == ["tr", "au"]

    async def test_another_chat_is_not_blocked(self, monkeypatch):
        gate = asyncio.Event()
        chats: list[int] = []

        async def _slow(channel, chat_id, url):
            chats.append(chat_id)
            await gate.wait()

        async def _fast(channel, chat_id, url):
            chats.append(chat_id)

        monkeypatch.setattr(tiktok_actions, "_do_transcript", _slow)
        monkeypatch.setattr(tiktok_actions.engine, "extract_url", lambda t: _URL)
        ch = _Channel()

        first = asyncio.create_task(
            tiktok_actions.handle_callback(ch, _CB.format("tr"), 5, 9, 1, source_text=_URL)
        )
        await asyncio.sleep(0)
        monkeypatch.setattr(tiktok_actions, "_do_transcript", _fast)
        await tiktok_actions.handle_callback(
            ch, _CB.format("tr"), 6, 9, 1, source_text=_URL
        )
        gate.set()
        await first

        assert chats == [5, 6], "a different chat must get its own slot"


# ── bot-wall honesty on the download button ───────────────────────────────────


class TestDownloadBotWall:
    async def test_a_bot_wall_is_named_not_flattened(self, monkeypatch):
        async def _blocked(url, **kw):
            raise tiktok_actions.engine.TikTokBlocked("HTTP Error 403: Forbidden")

        monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _blocked)
        ch = _Channel()

        await tiktok_actions._do_download(ch, 1, _URL)

        body = "\n".join(ch.messages)
        assert "blocked" in body.lower(), body
        assert "Couldn't download that video." not in body, (
            "a transient, fixable wall reported as a generic failure"
        )

    async def test_a_real_failure_still_reads_as_a_real_failure(self, monkeypatch):
        async def _boom(url, **kw):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _boom)
        ch = _Channel()

        await tiktok_actions._do_download(ch, 1, _URL)

        assert any("Couldn't download" in m for m in ch.messages), ch.messages

    @pytest.mark.parametrize("worker", ["_do_download", "_do_audio", "_do_transcript"])
    async def test_every_fetching_action_handles_the_wall(self, monkeypatch, worker):
        """All three take the same fetch path, so all three must say the same
        thing — and until `fetch_file` classified, none of them could."""

        async def _blocked(url, **kw):
            raise tiktok_actions.engine.TikTokBlocked("captcha required")

        monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _blocked)
        ch = _Channel()

        await getattr(tiktok_actions, worker)(ch, 1, _URL)

        body = "\n".join(ch.messages)
        assert "blocked" in body.lower(), f"{worker}: {body}"
