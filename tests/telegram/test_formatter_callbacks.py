"""The /format settings card must actually change a setting.

Every `fmt:` button was emitted by `telegram_formatter.py` and routed by NOTHING.
`CallbackHandler.handle` fell through to `self.store.get(cb_data)`, missed, and
answered "Button expired" -- for the whole life of the card. Worse,
`FormatterStore.save()` had never been called from anywhere in the repo, so a
preference could not have persisted even if the buttons had worked, and three of
the four picker builders had zero call sites.

The class is now gated by `test_every_emitted_prefix_is_actually_routed`
(tests/quality/test_telegram_callback_prefixes_are_gated.py). These are the
behaviour tests for the handler that closes it.
"""

from __future__ import annotations

from typing import Any

import pytest

from navig.gateway.channels import telegram_formatter as tf


class _Channel:
    """Records API calls instead of making them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict:
        self.calls.append((method, payload))
        return {"ok": True}

    def keyboard_of(self, index: int = -1) -> list[list[dict]]:
        return self.calls[index][1]["reply_markup"]["inline_keyboard"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real FormatterStore on a throwaway database."""
    st = tf.FormatterStore(tmp_path / "fmt.db")
    monkeypatch.setattr(tf, "get_formatter_store", lambda: st)
    return st


async def test_a_symbol_tap_persists_and_returns_to_the_card(store) -> None:
    """The headline: tapping a symbol must survive the tap.

    `FormatterStore.save` had never been called from anywhere. This is its first
    caller, so "the preference persists" is the assertion that matters -- reading it
    back through a FRESH `get`, not from the in-memory object we just mutated.
    """
    ch = _Channel()
    symbol = tf.SYMBOL_POOL[3]

    toast = await tf.handle_fmt_callback(ch, f"fmt:sym:h2:{symbol}", 1, 2, user_id=42)

    assert store.get(42).h2_symbol == symbol, "the preference did not persist"
    assert symbol in toast
    assert ch.calls and ch.calls[-1][0] == "editMessageText", (
        "after setting a value the card must be redrawn, not left on the picker"
    )


async def test_every_picker_opens(store) -> None:
    """The three builders with zero call sites gain one here.

    `build_symbol_picker_keyboard`, `build_bullet_picker_keyboard`,
    `build_numbered_picker_keyboard` and `build_outfmt_picker_keyboard` were
    unreachable code that no coverage tool could distinguish from live code.
    """
    for payload in ("fmt:h1", "fmt:h2", "fmt:h3", "fmt:h4", "fmt:bullet", "fmt:nums", "fmt:outfmt"):
        ch = _Channel()
        await tf.handle_fmt_callback(ch, payload, 1, 2, user_id=42)
        assert ch.calls, f"{payload} opened no picker"
        assert ch.calls[-1][0] == "editMessageReplyMarkup"
        assert ch.keyboard_of(), f"{payload} produced an empty keyboard"


@pytest.mark.parametrize(
    ("payload", "field", "expected"),
    [
        ("fmt:bul:▸", "bullet_style", "▸"),
        ("fmt:numstyle:plain", "numbered_style", tf.NUMBERED_STYLE_PLAIN),
        ("fmt:of:html", "output_format", tf.OUTPUT_FORMAT_HTML),
    ],
)
async def test_each_setter_persists(store, payload: str, field: str, expected: str) -> None:
    ch = _Channel()
    await tf.handle_fmt_callback(ch, payload, 1, 2, user_id=7)
    assert getattr(store.get(7), field) == expected


@pytest.mark.parametrize(
    "payload",
    [
        "fmt:sym:h9:■",  # level not in the table
        "fmt:sym:h1:NOT_A_SYMBOL",  # symbol not in the pool
        "fmt:bul:NOPE",
        "fmt:numstyle:sideways",
        "fmt:of:carrier-pigeon",
    ],
)
async def test_an_invalid_value_writes_nothing(store, payload: str) -> None:
    """A payload is remote input. Anything off the keyboard must be refused.

    In particular the level is never used to BUILD an attribute name --
    `setattr(prefs, f"{level}_symbol", ...)` on a hand-made payload is how a
    dataclass grows a field nothing reads.
    """
    before = store.get(9)
    ch = _Channel()

    toast = await tf.handle_fmt_callback(ch, payload, 1, 2, user_id=9)

    assert store.get(9) == before, f"{payload} mutated preferences"
    assert toast.startswith("Unknown"), f"{payload} was accepted silently: {toast!r}"


async def test_an_anonymous_tap_writes_nothing(store) -> None:
    """Preferences are keyed by user; a missing user_id must not write under 0.

    `CallbackHandler.handle` reads `user.get("id")`, which can be absent.
    """
    ch = _Channel()
    toast = await tf.handle_fmt_callback(ch, "fmt:of:html", 1, 2, user_id=None)

    assert store.get(0).output_format != tf.OUTPUT_FORMAT_HTML
    assert "Can't save" in toast
    assert not ch.calls, "an anonymous tap should not redraw anything"


async def test_an_unknown_payload_is_inert_not_a_crash(store) -> None:
    """An old card from a previous release must not raise inside the router."""
    ch = _Channel()
    assert await tf.handle_fmt_callback(ch, "fmt:from-a-future-version", 1, 2, 42) == ""


async def test_done_clears_the_keyboard(store) -> None:
    """Omitting reply_markup is NOT the same as clearing it — send it empty."""
    ch = _Channel()
    await tf.handle_fmt_callback(ch, "fmt:done", 1, 2, user_id=42)

    method, payload = ch.calls[-1]
    assert method == "editMessageText"
    assert payload["reply_markup"] == {"inline_keyboard": []}


async def test_the_handler_accepts_every_button_the_pickers_emit(store) -> None:
    """The whitelists must match the real keyboards.

    A validation table that drifts from the buttons rejects genuine taps and reads
    to the operator exactly like the dead-callback bug this replaced.
    """
    prefs = store.get(42)
    keyboards = [
        tf.build_formatter_settings_keyboard(prefs),
        tf.build_symbol_picker_keyboard("h2", prefs.h2_symbol),
        tf.build_bullet_picker_keyboard(prefs.bullet_style),
        tf.build_numbered_picker_keyboard(prefs.numbered_style),
        tf.build_outfmt_picker_keyboard(prefs.output_format),
    ]
    payloads = [
        b["callback_data"]
        for kb in keyboards
        for row in kb
        for b in row
        if str(b.get("callback_data", "")).startswith("fmt:")
    ]
    assert len(payloads) >= 15, f"only {len(payloads)} fmt: buttons found — pickers changed?"

    for payload in payloads:
        toast = await tf.handle_fmt_callback(_Channel(), payload, 1, 2, user_id=42)
        assert not toast.startswith("Unknown"), (
            f"the card emits {payload!r} but the handler rejects it as unknown"
        )
