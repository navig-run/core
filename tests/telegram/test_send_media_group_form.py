"""`sendMediaGroup` builds the multipart form Telegram actually accepts.

The album path shipped with tests that stubbed `send_media_group` itself, so the
form it builds — the `media` JSON, the `attach://` references, the per-file field
names — was covered by nothing. That is the half the Bot API validates.

The shape here is not guessed. It was verified against **api.telegram.org** with a
`chat_id` that cannot exist: Telegram parses and validates `media` and resolves
every `attach://` reference *before* it looks up the chat, so

    {"ok": false, "error_code": 400, "description": "Bad Request: chat not found"}

is proof the request shape is right — and nothing is delivered to anyone. A
malformed `media` array answers with a different 400 ("can't parse media" / "wrong
type of the web page content"), which is what makes the probe conclusive.

These tests pin that shape offline so a future edit cannot silently break it; the
network probe is the one-off that established what to pin.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
)


class _CapturedPost:
    """Stands in for `session.post`, keeping the FormData it was handed."""

    def __init__(self, result: dict):
        self.result = result
        self.forms: list = []
        self.urls: list[str] = []

    def __call__(self, url, data=None, **kw):
        self.urls.append(url)
        self.forms.append(data)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self.result


def _fields(form) -> dict[str, object]:
    """Field name → value out of an aiohttp FormData, without sending it."""
    out: dict[str, object] = {}
    for type_options, _headers, value in form._fields:
        out[type_options["name"]] = value
    return out


def _channel(result: dict):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    post = _CapturedPost(result)
    channel._session = type("S", (), {"post": staticmethod(post)})()
    return channel, post


async def test_the_form_matches_what_the_api_accepted():
    channel, post = _channel({"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]})

    out = await channel.send_media_group(42, [_PNG, _PNG], caption="four slides")

    assert out == [{"message_id": 1}, {"message_id": 2}]
    assert post.urls[0].endswith("/sendMediaGroup")

    fields = _fields(post.forms[0])
    assert fields["chat_id"] == "42"

    media = json.loads(fields["media"])
    assert [m["type"] for m in media] == ["photo", "photo"]
    # Every attach:// reference must name a field that is actually in the form —
    # this is precisely what Telegram resolves before it looks at the chat.
    for i, item in enumerate(media):
        key = f"file{i}"
        assert item["media"] == f"attach://{key}"
        assert fields[key] == _PNG, f"{key} is missing from the multipart body"

    # The caption rides the FIRST item; that is where Telegram renders an album's.
    assert media[0].get("caption")
    assert "caption" not in media[1]


async def test_a_rejected_group_returns_None_rather_than_raising():
    """The caller counts what came back to decide whether to fall back to
    singles, so a rejection has to be a value, not an exception.

    ⚠ Honest limitation: this cannot distinguish "returns None" from "raises and
    the method's own `except Exception` converts it to None" — mutation testing
    confirmed that swapping the return for a `raise` changes nothing observable,
    because the raise happens inside that same `try`. The CONTRACT the caller
    depends on (a value, never an exception) holds either way, which is what is
    being pinned here; an equivalent mutant is not a gap to paper over.
    """
    channel, _post = _channel({"ok": False, "description": "Bad Request: chat not found"})

    assert await channel.send_media_group(42, [_PNG, _PNG]) is None


@pytest.mark.parametrize("count", [0, 1, 11])
async def test_only_two_to_ten_photos_are_a_group(count):
    """Telegram accepts 2..10. A single photo is not a group — the caller keeps
    using send_photo for that, and sending nothing at all must not look like a
    delivered album."""
    channel, post = _channel({"ok": True, "result": []})

    assert await channel.send_media_group(42, [_PNG] * count) is None
    assert post.forms == [], "a request was built for an invalid group size"


async def test_a_caption_parse_error_is_retried_without_parse_mode():
    """Same contract as `_post_media`: a caption Telegram's HTML parser rejects
    must not cost the whole upload."""
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE")
    results = [
        {"ok": False, "description": "Bad Request: can't parse entities: unexpected tag"},
        {"ok": True, "result": [{"message_id": 5}, {"message_id": 6}]},
    ]
    post = _CapturedPost({})

    async def _json(self=None):
        return results.pop(0)

    post.json = _json  # type: ignore[assignment]
    channel._session = type("S", (), {"post": staticmethod(post)})()

    out = await channel.send_media_group(42, [_PNG, _PNG], caption="<b>unclosed")

    assert out == [{"message_id": 5}, {"message_id": 6}]
    assert len(post.forms) == 2, "the retry did not happen"
    # aiohttp FormData is single-use, so the retry must build a FRESH form.
    assert post.forms[0] is not post.forms[1]
    retried = json.loads(_fields(post.forms[1])["media"])
    assert "parse_mode" not in retried[0], "the retry kept the parse mode that failed"
