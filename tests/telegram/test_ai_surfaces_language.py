"""Every AI text surface must honour `user.language` — not just the ones reported.

The operator set a language and got English back. Three separate fixes closed
three separate surfaces (speech-to-text, the TikTok briefing, the catalog
summary), which is the signature of a *class* rather than three bugs: a prompt
written in English by whoever added it, with nothing pinning the setting to it.

These pin the last two:

* **🌍 Translate** hardcoded English as the target. For an operator who told
  navig they read Russian, tapping translate on a Russian message translated it
  *away* from the language they had just asked for.
* **The away-recap** ("what were you working on") was English-only, so a Russian
  conversation was summarised back at them in English.

Both keep today's behaviour when nothing is configured, so an unconfigured
install is unchanged.
"""

from __future__ import annotations

import pytest


class _Cfg:
    store: dict = {}

    def get(self, key, default=None):
        return type(self).store.get(key, default)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _Cfg.store = {}
    monkeypatch.setattr("navig.core.Config", _Cfg)
    yield
    _Cfg.store = {}


# ── 🌍 translate ──────────────────────────────────────────────────────────────


def _capture_prompt(monkeypatch) -> dict:
    seen: dict = {}

    def _fake_generate(messages, *a, **kw):
        seen["system"] = messages[0]["content"]
        return "result"

    monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_generate)
    monkeypatch.setattr(
        "navig.telegram.permissions.can_use", lambda tool, *, is_owner: True
    )
    return seen


class TestTranslateTarget:
    async def test_configured_language_becomes_the_target(self, monkeypatch):
        from navig.telegram import ai_actions

        _Cfg.store["user.language"] = "Russian"
        seen = _capture_prompt(monkeypatch)

        await ai_actions.run_text_action("translate", "hello", is_owner=True)

        assert "Russian" in seen["system"], seen.get("system")

    async def test_an_explicit_argument_still_wins(self, monkeypatch):
        """`translate fr` must beat the global — the user named a target."""
        from navig.telegram import ai_actions

        _Cfg.store["user.language"] = "Russian"
        seen = _capture_prompt(monkeypatch)

        await ai_actions.run_text_action("translate", "hello", is_owner=True, arg="French")

        assert "French" in seen["system"]
        assert "Russian" not in seen["system"]

    async def test_unconfigured_keeps_the_original_english_default(self, monkeypatch):
        """No preference set ⇒ behaviour is exactly what it was before."""
        from navig.telegram import ai_actions

        seen = _capture_prompt(monkeypatch)

        await ai_actions.run_text_action("translate", "привет", is_owner=True)

        assert "English" in seen["system"]

    async def test_the_other_actions_already_mirror_the_source(self):
        """summarize/context/explain say "same language as the input" — they need
        no wiring, and this fails if someone removes that instruction."""
        from navig.telegram.ai_actions import _SYSTEM

        for tool in ("summarize", "context", "explain"):
            assert "same language" in _SYSTEM[tool], f"{tool} lost its language instruction"


# ── away recap ────────────────────────────────────────────────────────────────


class TestAwayRecapLanguage:
    def test_configured_language_is_requested(self):
        from navig.gateway.channels.away_summary import _recap_system_prompt

        _Cfg.store["user.language"] = "Russian"
        assert "in Russian" in _recap_system_prompt()

    def test_auto_asks_for_the_conversation_language(self):
        from navig.gateway.channels.away_summary import _recap_system_prompt

        prompt = _recap_system_prompt()
        assert "same language the conversation is in" in prompt

    def test_the_base_instruction_survives(self):
        """The language directive is appended, never a replacement."""
        from navig.gateway.channels.away_summary import (
            _RECAP_SYSTEM_PROMPT,
            _recap_system_prompt,
        )

        assert _RECAP_SYSTEM_PROMPT in _recap_system_prompt()

    def test_unreadable_config_degrades_to_the_source_language(self, monkeypatch):
        def _boom():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("navig.core.Config", _boom)
        from navig.gateway.channels.away_summary import _recap_system_prompt

        assert "same language" in _recap_system_prompt()
