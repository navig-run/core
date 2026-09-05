"""`/lang` must actually write the setting — not merely say that it did.

Before this command existed, `/lang Russian` was an unregistered slash command,
so it fell through to the chat agent. The agent has no way to change
configuration and every incentive to sound like it does: the operator got
"Язык переключён на русский" (with web-search "Read" buttons attached, because
the agent had treated it as a question *about* Russian) while `user.language`
stayed unset. A confirmation indistinguishable from a real one is worse than an
error — so these tests pin that the command writes, reads back, and tells the
truth when the write does not stick.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_commands import (
    _SLASH_REGISTRY,
    TelegramCommandsMixin,
)


class _Chan:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append(text)
        return {"message_id": 1}


class _Cfg:
    """A config that actually stores, so read-back is meaningful."""

    store: dict = {}

    def get(self, key, default=None):
        return type(self).store.get(key, default)

    def set_global(self, key, value):
        type(self).store[key] = value


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    _Cfg.store = {}
    monkeypatch.setattr("navig.config.ConfigManager", _Cfg)
    yield
    _Cfg.store = {}


async def _run(text: str) -> _Chan:
    ch = _Chan()
    await TelegramCommandsMixin._handle_lang(ch, 1, 42, text)
    return ch


def test_lang_is_a_registered_command():
    """Unregistered is exactly how the agent got to fake a confirmation."""
    assert any(e.command == "lang" for e in _SLASH_REGISTRY), (
        "an unregistered /lang falls through to the model, which will invent a reply"
    )


async def test_setting_a_language_persists_it():
    ch = await _run("/lang Russian")

    assert _Cfg.store.get("user.language") == "Russian", (
        "the command reported success; the config must actually hold the value"
    )
    assert "Russian" in ch.messages[0]


async def test_auto_is_stored_and_described_as_following_the_content():
    await _run("/lang Russian")
    ch = await _run("/lang auto")

    assert _Cfg.store.get("user.language") == "auto"
    assert "auto" in ch.messages[0].lower()


async def test_bare_lang_reports_the_current_value_without_changing_it():
    _Cfg.store["user.language"] = "Russian"

    ch = await _run("/lang")

    assert "Russian" in ch.messages[0]
    assert _Cfg.store["user.language"] == "Russian", "a query must not write"


async def test_bare_lang_on_a_fresh_install_says_auto():
    ch = await _run("/lang")
    assert "auto" in ch.messages[0].lower()


async def test_an_unwritable_config_is_reported_not_celebrated(monkeypatch):
    class _Readonly(_Cfg):
        def set_global(self, key, value):
            raise OSError("config is read-only")

    monkeypatch.setattr("navig.config.ConfigManager", _Readonly)

    ch = await _run("/lang Russian")

    assert "Couldn't save" in ch.messages[0], ch.messages
    assert "set to" not in ch.messages[0]


async def test_a_write_that_does_not_stick_is_reported(monkeypatch):
    """Read-back is the point: a silently-dropped write must not read as success."""

    class _Amnesiac(_Cfg):
        def set_global(self, key, value):
            pass  # accepts and forgets

    monkeypatch.setattr("navig.config.ConfigManager", _Amnesiac)

    ch = await _run("/lang Russian")

    assert "did not persist" in ch.messages[0], ch.messages


# ── the claim "/lang applies to summaries" has to be true ─────────────────────


class TestSummaryLanguage:
    """`/lang` tells the operator summaries follow the setting. They must."""

    async def test_configured_language_reaches_the_catalog_summary(self, monkeypatch):
        from navig.gateway.channels import telegram_catalog_analyzer as an

        _Cfg.store["user.language"] = "Russian"
        seen: dict = {}

        def _fake_generate(messages, *a, **kw):
            seen["system"] = messages[0]["content"]
            return "summary"

        monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_generate)
        monkeypatch.setattr("navig.core.Config", _Cfg)

        await an._summarize("some transcript text")

        assert "Russian" in seen["system"], seen.get("system")

    async def test_auto_asks_for_the_content_language(self, monkeypatch):
        from navig.gateway.channels import telegram_catalog_analyzer as an

        _Cfg.store.clear()
        seen: dict = {}

        def _fake_generate(messages, *a, **kw):
            seen["system"] = messages[0]["content"]
            return "summary"

        monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_generate)
        monkeypatch.setattr("navig.core.Config", _Cfg)

        await an._summarize("some transcript text")

        assert "same language as the content" in seen["system"], seen.get("system")
