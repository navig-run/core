"""`navig miniapp status` must not print the deck api_key, and must CHECK it.

The Mini App button URL embeds the RAW `deck.api_key` (see `_connect_url`). That
key is the install's identity: it bypasses Telegram auth on the deck API, and the
lighthouse edge derives its Durable Object tenant from `sha256(api_key)`.

`miniapp status` is a read-only diagnostic people run repeatedly and paste into
issues and screenshots, and it printed that key in full every time -- for no
diagnostic gain, since what it actually judges is the origin and the `v=`
cache-bust. Setup flows that deliberately hand the operator their magic link
(`navig cloud connect`, "save this -- full key shown once") are a different
contract and are deliberately untouched.

The same button URL was also never CHECKED against the current key. Rotation is
supposed to re-point the button, but when that half fails the button keeps a
perfect origin and a perfect v=, so both existing checks pass while every Mini
App session resolves to a retired tenant with no brain attached.
"""

from __future__ import annotations

import pytest

from navig.commands.miniapp import redact_key_in_url

_KEY = "navig_SUPERSECRETKEYVALUE_0123456789"


def test_the_key_value_is_gone_but_the_diagnosis_survives() -> None:
    url = f"https://deck.example.workers.dev/connect?key={_KEY}&v=abc123"
    out = redact_key_in_url(url)

    assert _KEY not in out, out
    # Everything the command is actually judging must still be readable.
    assert "deck.example.workers.dev" in out
    assert "/connect" in out
    assert "v=abc123" in out
    assert "redacted" in out.lower()


def test_a_url_with_no_key_is_untouched() -> None:
    url = "https://deck.example.workers.dev/connect?v=abc123"
    assert redact_key_in_url(url) == url


def test_a_url_with_no_query_is_untouched() -> None:
    url = "https://deck.example.workers.dev/connect"
    assert redact_key_in_url(url) == url


def test_empty_is_untouched() -> None:
    assert redact_key_in_url("") == ""


@pytest.mark.parametrize(
    "url",
    [
        f"https://d.example.dev/connect?key={_KEY}",
        f"https://d.example.dev/connect?v=1&key={_KEY}",
        f"https://d.example.dev/connect?key={_KEY}&v=1&x=2",
        f"https://d.example.dev/connect?key={_KEY}#frag",
    ],
)
def test_the_key_never_survives_any_ordering(url: str) -> None:
    assert _KEY not in redact_key_in_url(url)


def test_status_output_does_not_contain_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end at the print site: whatever Telegram returns, the key must not
    reach the terminal. Asserting on the helper alone would not prove it is WIRED."""
    from typer.testing import CliRunner

    from navig.commands import miniapp

    live = f"https://deck.example.workers.dev/connect?key={_KEY}&v=abc123"
    monkeypatch.setattr(miniapp, "_bot_token", lambda: "123:FAKE")

    def fake_tg(token, method, payload, timeout=None):
        if method == "getMe":
            return {"ok": True, "result": {"username": "bot", "id": 1}}
        if method == "getChatMenuButton":
            return {
                "ok": True,
                "result": {"type": "web_app", "text": "Deck", "web_app": {"url": live}},
            }
        return {"ok": False, "description": "unexpected"}

    monkeypatch.setattr(miniapp, "_tg_call", fake_tg)
    monkeypatch.setattr(miniapp, "miniapp_button_health", lambda **kw: (True, "ok", False))

    result = CliRunner().invoke(miniapp.app, ["status"])

    assert _KEY not in result.output, (
        "`navig miniapp status` printed the deck api_key in full -- it is the "
        f"install's identity and its sha256 is the edge tenant. output={result.output!r}"
    )
    # ...while still answering the question the command exists to answer.
    assert "deck.example.workers.dev" in result.output, result.output
