"""One fixture Answer rendered by every surface: the same facts, no LLM call."""

from __future__ import annotations

import json

import pytest

from navig import console_helper as ch
from navig.ui.answer import (
    ALEXA_MAX_SSML_CHARS,
    ALICE_MAX_BUTTON_CHARS,
    ALICE_MAX_TEXT_CHARS,
    TELEGRAM_MAX_MESSAGE_CHARS,
    Answer,
    render_cli,
    to_alexa,
    to_alice,
    to_deck,
    to_json,
    to_openai,
    to_telegram,
    validate_answer,
)

DEVICE = "Desk lamp"
STATE = "off"
SUGGESTION = "navig endpoints show govee:desk-lamp"


def fixture_answer(**overrides) -> Answer:
    """The one answer every surface renders: the desk lamp was turned off."""
    fields = {
        "speech": "Desk lamp is off.",
        "display_text": "Desk lamp: off",
        "ok": True,
        "table": {"columns": ["Device", "State"], "rows": [[DEVICE, STATE]]},
        "json": {"device": "govee:desk-lamp", "power": "off"},
        "directives": [{"type": "open", "params": {"target": "govee:desk-lamp"}}],
        "suggestions": [SUGGESTION],
        "data": {"device": DEVICE, "state": STATE},
        "plan": {"token": "plan-abc123", "tool": "home.off",
                 "blast_radius": {"kind": "single", "count": 1}},
        "intent": "home.off",
    }
    fields.update(overrides)
    return Answer(**fields)


@pytest.fixture()
def no_llm(monkeypatch) -> None:
    """Every renderer must work with an LLM call being a hard failure."""
    from navig.ai import AIAssistant
    from navig.llm.routing.router import UnifiedRouter

    def _fail(*_args, **_kwargs):
        raise AssertionError("an Answer renderer must never call an LLM")

    monkeypatch.setattr(AIAssistant, "ask", _fail)
    monkeypatch.setattr(UnifiedRouter, "route", _fail)


def test_the_fixture_answer_is_valid() -> None:
    assert validate_answer(fixture_answer()) == []


def test_cli_renders_the_same_facts(no_llm) -> None:
    with ch.console.capture() as captured:
        render_cli(fixture_answer())
    text = captured.get()
    assert DEVICE in text
    assert STATE in text
    assert "next:" in text
    assert "[green]" not in text and "[red]" not in text, "data is escaped, not markup"


def test_json_carries_the_wire_version_and_facts() -> None:
    payload = json.loads(to_json(fixture_answer()))
    assert validate_answer(payload) == []
    assert payload["schema_version"] == "1"
    assert payload["display_text"] == "Desk lamp: off"
    assert payload["json"] == {"device": "govee:desk-lamp", "power": "off"}
    assert payload["data"] == {"device": DEVICE, "state": STATE}


def test_alexa_speaks_the_state_and_cards_the_display_text(no_llm) -> None:
    body = to_alexa(fixture_answer())
    response = body["response"]
    ssml = response["outputSpeech"]["ssml"]
    assert ssml.startswith("<speak>") and ssml.endswith("</speak>")
    assert DEVICE in ssml and STATE in ssml
    assert response["outputSpeech"]["type"] == "SSML"
    assert response["card"]["content"] == "Desk lamp: off"
    assert response["reprompt"]["outputSpeech"]["ssml"]
    assert SUGGESTION in response["reprompt"]["outputSpeech"]["ssml"]
    assert response["directives"] == [
        {"type": "open", "params": {"target": "govee:desk-lamp"}}]
    assert response["shouldEndSession"] is False, "a pending plan keeps the session open"


def test_alexa_escapes_ssml_and_caps_the_speech() -> None:
    answer = fixture_answer(
        speech="A & B <say> 'quoted' \"double\"",
        plan=None,
        suggestions=[],
        directives=[],
    )
    body = to_alexa(answer)
    ssml = body["response"]["outputSpeech"]["ssml"]
    assert "&amp;" in ssml and "&lt;" in ssml and "&apos;" in ssml and "&quot;" in ssml
    assert "<say>" not in ssml
    assert body["response"]["shouldEndSession"] is True
    long = to_alexa(fixture_answer(speech="word " * 3000))
    assert len(long["response"]["outputSpeech"]["ssml"]) <= ALEXA_MAX_SSML_CHARS


def test_alice_carries_text_tts_buttons_directives_and_session_state(no_llm) -> None:
    body = to_alice(fixture_answer())
    response = body["response"]
    assert response["text"] == "Desk lamp: off"
    assert "Desk lamp is off" in response["tts"]
    assert response["buttons"] == [
        {"title": SUGGESTION, "payload": {}, "hide": True}]
    assert response["directives"] == [
        {"type": "open", "params": {"target": "govee:desk-lamp"}}]
    assert body["session_state"]["token"] == "plan-abc123"
    assert response["end_session"] is False


def test_alice_buttons_are_capped_and_truncated() -> None:
    suggestions = [f"suggestion-{index}-" + "x" * 100 for index in range(8)]
    body = to_alice(fixture_answer(suggestions=suggestions))
    buttons = body["response"]["buttons"]
    assert len(buttons) == 5
    assert all(len(button["title"]) <= ALICE_MAX_BUTTON_CHARS for button in buttons)


def test_alice_strips_markup_tags_from_tts() -> None:
    body = to_alice(fixture_answer(speech="<p>Desk lamp is off.</p>"))
    assert "<p>" not in body["response"]["tts"]
    assert "Desk lamp is off." in body["response"]["tts"]


def test_alice_text_is_capped() -> None:
    body = to_alice(fixture_answer(display_text="x" * 5000))
    assert len(body["response"]["text"]) <= ALICE_MAX_TEXT_CHARS


def test_openai_returns_display_text_and_metadata(no_llm) -> None:
    body = to_openai(fixture_answer())
    assert body["content"] == "Desk lamp: off"
    assert body["metadata"]["data"] == {"device": DEVICE, "state": STATE}
    assert body["metadata"]["suggestions"] == [SUGGESTION]
    assert body["metadata"]["schema_version"] == "1"


def test_deck_returns_directives_table_and_json_without_suggestions() -> None:
    body = to_deck(fixture_answer())
    assert body["directives"] == [
        {"type": "open", "params": {"target": "govee:desk-lamp"}}]
    assert body["table"]["rows"] == [[DEVICE, STATE]]
    assert body["json"] == {"device": "govee:desk-lamp", "power": "off"}
    assert "suggestions" not in body, "the deck renders directives, not follow-ups"


def test_telegram_returns_text_and_buttons() -> None:
    body = to_telegram(fixture_answer())
    assert body["text"] == "Desk lamp: off"
    assert body["buttons"] == [{"text": SUGGESTION}]
    long = to_telegram(fixture_answer(display_text="x" * 5000))
    assert len(long["text"]) <= TELEGRAM_MAX_MESSAGE_CHARS


def test_every_surface_agrees_on_device_and_state(no_llm) -> None:
    answer = fixture_answer()
    surfaces = {
        "json": json.loads(to_json(answer)),
        "alexa": to_alexa(answer),
        "alice": to_alice(answer),
        "openai": to_openai(answer),
        "deck": to_deck(answer),
        "telegram": to_telegram(answer),
    }
    assert DEVICE in json.dumps(surfaces["json"])
    assert STATE in str(surfaces["json"]["data"]["state"])
    assert DEVICE in surfaces["alexa"]["response"]["outputSpeech"]["ssml"]
    assert STATE in surfaces["alexa"]["response"]["outputSpeech"]["ssml"]
    assert DEVICE in surfaces["alice"]["response"]["text"]
    assert STATE in surfaces["alice"]["response"]["text"]
    assert DEVICE in surfaces["openai"]["content"]
    assert STATE in surfaces["openai"]["content"]
    assert DEVICE in surfaces["deck"]["display_text"]
    assert STATE in surfaces["telegram"]["text"]


def test_from_home_status_projects_the_plugin_payload() -> None:
    from navig.ui.answer import Answer as AnswerClass

    payload = {
        "ok": True,
        "count": 2,
        "devices": [
            {"device": "desk-lamp", "name": "Desk lamp", "room": "office",
             "provider": "govee", "ok": True, "state": {"power": "on"}},
            {"device": "living-room-ac", "name": "Living room AC", "room": "living room",
             "provider": "ha", "ok": False, "state": {"power": "unknown"}},
        ],
        "providers": [
            {"provider": "govee", "ready": True, "detail": "", "hint": ""},
            {"provider": "ha", "ready": False, "detail": "HA_URL unset", "hint": "set it"},
        ],
        "paths": {"devices": "devices.yaml"},
    }
    answer = AnswerClass.from_home_status(payload)
    assert validate_answer(answer) == []
    assert answer.ok is True
    assert answer.speech == "2 device(s), 1 online"
    assert answer.table["columns"] == ["Device", "Room", "Provider", "Status"]
    assert answer.table["rows"][0] == ["Desk lamp", "office", "govee", "online"]
    assert answer.data["providers"] == {"govee": True, "ha": False}
    assert answer.json == payload

    failed = AnswerClass.from_home_status({"ok": False, "devices": []})
    assert failed.ok is False
    assert validate_answer(failed) == []


def test_from_plan_keeps_the_plan_and_points_at_the_commit_command() -> None:
    plan = {
        "token": "plan-abc123",
        "tool": "home.off",
        "command": ["home", "off"],
        "profile": "fast",
        "risk": "low",
        "targets": [{"endpoint": "govee:desk-lamp", "home_id": "desk-lamp"}],
        "blast_radius": {"kind": "single", "count": 1, "targets": ["govee:desk-lamp"]},
    }
    answer = Answer.from_plan(plan)
    assert validate_answer(answer) == []
    assert answer.plan == plan
    assert answer.data["tool"] == "home.off"
    assert answer.suggestions == ["navig ask --commit plan-abc123"]
    assert "plan-abc123" in answer.speech
