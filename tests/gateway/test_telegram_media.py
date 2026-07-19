"""Tests for TelegramChannel media senders (send_photo/video/document/animation).

Covers the shared _post_media helper: markdown→HTML captions, special-char
escaping, the 1024-char caption cap, and the parse-error resend that keeps media
from being silently dropped. Uses a fake aiohttp session — no network, no daemon.
"""

from __future__ import annotations

from navig.gateway.channels.base import utf16_len
from navig.gateway.channels.telegram import TelegramChannel

# ── fake aiohttp session ──────────────────────────────────────────────────────

def _fields(form) -> dict:
    """Extract {field_name: value} from an aiohttp FormData."""
    out: dict = {}
    for type_options, _headers, value in form._fields:
        name = type_options.get("name")
        if name is not None:
            out[name] = value
    return out


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, data=None):
        self.posts.append((url, _fields(data)))
        payload = (
            self._payloads.pop(0)
            if self._payloads
            else {"ok": True, "result": {"message_id": 1}}
        )
        return _FakeResp(payload)


def _channel(*payloads) -> tuple[TelegramChannel, _FakeSession]:
    ch = TelegramChannel.__new__(TelegramChannel)  # no daemon / bot token
    session = _FakeSession(*payloads)
    ch._session = session
    ch.base_url = "https://api.telegram.org/botTEST"
    return ch, session


# ── caption rendering ─────────────────────────────────────────────────────────

async def test_photo_caption_markdown_rendered_and_html_parse_mode():
    ch, s = _channel({"ok": True, "result": {"message_id": 5}})
    res = await ch.send_photo(123, b"imgbytes", caption="run **now**")
    assert res == {"message_id": 5}
    assert len(s.posts) == 1
    url, fields = s.posts[0]
    assert url.endswith("/sendPhoto")
    assert fields["parse_mode"] == "HTML"
    assert fields["caption"] == "run <b>now</b>"
    assert fields["photo"] == b"imgbytes"


async def test_photo_plain_caption_special_chars_escaped_no_retry():
    # "Tips & Tricks" / URLs with query strings must be escaped so they are valid
    # HTML — no parse error, no wasteful re-upload.
    ch, s = _channel({"ok": True, "result": {"message_id": 1}})
    await ch.send_photo(1, b"i", caption="Tips & Tricks https://x.io/?a=1&b=2")
    assert len(s.posts) == 1  # single upload
    fields = s.posts[0][1]
    assert "&amp;" in fields["caption"]
    assert "& Tricks" not in fields["caption"]  # bare & was escaped


async def test_prebuilt_html_caption_passthrough():
    ch, s = _channel({"ok": True, "result": {"message_id": 1}})
    await ch.send_photo(1, b"i", caption="<b>Already HTML</b>")
    assert s.posts[0][1]["caption"] == "<b>Already HTML</b>"  # not double-escaped


async def test_parse_mode_none_sends_caption_raw():
    ch, s = _channel({"ok": True, "result": {"message_id": 1}})
    await ch.send_photo(1, b"i", caption="**raw**", parse_mode=None)
    fields = s.posts[0][1]
    assert "parse_mode" not in fields
    assert fields["caption"] == "**raw**"  # untouched


# ── the real bug: media must not be dropped on a caption parse error ───────────

async def test_caption_parse_error_resends_without_parse_mode():
    ch, s = _channel(
        {"ok": False, "description": "Bad Request: can't parse entities: bad tag"},
        {"ok": True, "result": {"message_id": 9}},
    )
    # A pre-built-HTML caption that is actually malformed → parse error on first try.
    res = await ch.send_photo(1, b"i", caption="<b>unclosed")
    assert res == {"message_id": 9}          # media STILL delivered
    assert len(s.posts) == 2                 # resent
    assert s.posts[0][1]["parse_mode"] == "HTML"
    assert "parse_mode" not in s.posts[1][1]  # retry dropped the parse mode
    assert s.posts[1][1]["caption"] == "<b>unclosed"


async def test_non_parse_error_does_not_retry():
    ch, s = _channel({"ok": False, "description": "Bad Request: chat not found"})
    res = await ch.send_photo(1, b"i", caption="<b>x</b>")
    assert res is None
    assert len(s.posts) == 1  # no wasteful re-upload on a non-parse failure


# ── caption length cap (Telegram's 1024 limit) ────────────────────────────────

async def test_long_caption_capped_to_1024():
    ch, s = _channel({"ok": True, "result": {"message_id": 1}})
    await ch.send_photo(1, b"i", caption="x" * 1500)
    assert utf16_len(s.posts[0][1]["caption"]) <= 1024


# ── other senders wire through the same helper ────────────────────────────────

async def test_video_extra_fields_and_caption():
    ch, s = _channel({"ok": True, "result": {"message_id": 3}})
    await ch.send_video(
        7, b"vid", caption="clip **hd**", duration=12, width=1920, height=1080
    )
    url, fields = s.posts[0]
    assert url.endswith("/sendVideo")
    assert fields["caption"] == "clip <b>hd</b>"
    assert fields["parse_mode"] == "HTML"
    assert fields["duration"] == "12"
    assert fields["width"] == "1920"
    assert fields["height"] == "1080"
    assert fields["video"] == b"vid"


async def test_document_uses_filename_and_caption():
    ch, s = _channel({"ok": True, "result": {"message_id": 4}})
    await ch.send_document(2, b"docbytes", filename="report.pdf", caption="see `code`")
    url, fields = s.posts[0]
    assert url.endswith("/sendDocument")
    assert fields["document"] == b"docbytes"
    assert fields["caption"] == "see <code>code</code>"
    assert fields["parse_mode"] == "HTML"


async def test_animation_wires_through_helper():
    ch, s = _channel({"ok": True, "result": {"message_id": 6}})
    await ch.send_animation(3, b"gif", caption="loop it")
    url, fields = s.posts[0]
    assert url.endswith("/sendAnimation")
    assert fields["animation"] == b"gif"
    assert fields["parse_mode"] == "HTML"


async def test_no_session_returns_none():
    ch = TelegramChannel.__new__(TelegramChannel)
    ch._session = None
    ch.base_url = "https://api.telegram.org/botTEST"
    assert await ch.send_photo(1, b"i", caption="x") is None
