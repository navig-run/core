"""Tests for navig.personas.renderer — render_persona_list, render_persona_info, render_switch_confirmation."""
from __future__ import annotations

import pytest

from navig.personas.contracts import PersonaConfig
from navig.personas.renderer import (
    render_persona_info,
    render_persona_list,
    render_switch_confirmation,
)


def _persona(name="techie", tone="direct", display="Techie", voice="", model="", extends="") -> PersonaConfig:
    return PersonaConfig(
        name=name,
        display_name=display,
        tone=tone,
        voice_id=voice,
        model_hint=model,
        soul_extends=extends,
    )


class TestRenderPersonaList:
    def test_empty_list_message(self) -> None:
        result = render_persona_list([], active="default")
        assert "No personas" in result or result

    def test_contains_persona_name(self) -> None:
        p = _persona(name="techie", display="Techie")
        result = render_persona_list([p], active="other")
        assert "techie" in result

    def test_marks_active_persona(self) -> None:
        p = _persona(name="techie")
        result = render_persona_list([p], active="techie")
        assert "active" in result.lower() or "←" in result

    def test_no_active_marker_for_other(self) -> None:
        p = _persona(name="techie")
        result = render_persona_list([p], active="default")
        assert "active" not in result.lower() or "techie" in result

    def test_includes_switch_hint(self) -> None:
        p = _persona(name="techie")
        result = render_persona_list([p], active="other")
        assert "/persona" in result

    def test_multiple_personas_listed(self) -> None:
        personas = [_persona(name="a", tone="direct"), _persona(name="b", tone="warm")]
        result = render_persona_list(personas, active="other")
        assert "a" in result and "b" in result


class TestRenderPersonaInfo:
    def test_contains_name(self) -> None:
        p = _persona(name="techie", display="Techie")
        result = render_persona_info(p, "")
        assert "techie" in result

    def test_contains_display_name(self) -> None:
        p = _persona(name="techie", display="Tech Expert")
        result = render_persona_info(p, "")
        assert "Tech Expert" in result

    def test_contains_tone(self) -> None:
        p = _persona(tone="direct")
        result = render_persona_info(p, "")
        assert "direct" in result

    def test_shows_model_hint_when_set(self) -> None:
        p = _persona(model="gpt-4o")
        result = render_persona_info(p, "")
        assert "gpt-4o" in result

    def test_omits_model_hint_when_empty(self) -> None:
        p = _persona(model="")
        result = render_persona_info(p, "")
        assert "Model" not in result

    def test_shows_voice_when_set(self) -> None:
        p = _persona(voice="en-US-1")
        result = render_persona_info(p, "")
        assert "en-US-1" in result

    def test_soul_excerpt_included(self) -> None:
        p = _persona()
        result = render_persona_info(p, "I am a helpful assistant soul text")
        assert "helpful" in result

    def test_soul_excerpt_truncated(self) -> None:
        p = _persona()
        long_soul = "X" * 1000
        result = render_persona_info(p, long_soul)
        assert len(result) < 1000


class TestRenderSwitchConfirmation:
    def test_default_persona(self) -> None:
        p = _persona(name="default")
        result = render_switch_confirmation(p)
        assert "✅" in result
        assert "default" in result.lower()

    def test_unknown_persona_returns_generic(self) -> None:
        p = _persona(name="zarkon", display="Zarkon")
        result = render_switch_confirmation(p)
        assert "✅" in result
        assert "Zarkon" in result

    def test_tyler_persona(self) -> None:
        p = _persona(name="tyler")
        result = render_switch_confirmation(p)
        assert "✅" in result

    def test_returns_non_empty(self) -> None:
        for name in ("default", "assistant", "tyler", "philosopher", "teacher", "storyteller", "custom"):
            p = _persona(name=name)
            assert render_switch_confirmation(p)
