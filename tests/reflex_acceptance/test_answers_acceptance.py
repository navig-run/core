"""Acceptance C — Unified Answers (plan §4, §4.1).

One core ``Answer`` rendered to CLI / JSON / Alexa / Alice / OpenAI (+ deck, Telegram),
agreeing on the same facts; the reflex registry returns valid Answers; a bare dict fails.
"""

from __future__ import annotations

import importlib
import json

import _support as S
import pytest

pytestmark = pytest.mark.integration

#: The canonical fixture: desk lamp turned off — device name + new state in every channel.
DEVICE = "Desk lamp"
DEVICE_ID = "govee:desk-lamp"
NEW_STATE = "off"
SENTENCE = "Turning off the desk lamp. It is now off."
DISPLAY = "Desk lamp turned off."
SUGGESTION = "Turn it back on"

#: One sample utterance per shipped tool (registry-driven Answer validation).
TOOL_SAMPLE = {
    "home.on": "turn on the desk lamp",
    "home.off": "turn off the desk lamp",
    "home.toggle": "toggle the desk lamp",
    "home.set": "set the desk lamp brightness to 60",
    "home.status": "what is the status of the desk lamp",
    "tv.power": "turn on the tv",
    "tv.volume": "turn the tv volume up",
    "tv.play": "play something on the tv",
    "assistant.status": "assistant status",
    "campaigns.status": "campaign status",
}


def _answer_module():
    return S.require("navig.ui.answer")


RENDERER_NAMES = ("render_cli", "to_json", "to_alexa", "to_alice", "to_openai", "to_deck", "to_telegram")


def _renderers(mod) -> dict[str, object]:
    """Resolve the documented renderers (they may live beside Answer in a render module)."""
    api = mod
    if any(not hasattr(api, name) for name in RENDERER_NAMES):
        for module_name in ("navig.ui.answer_render", "navig.ui"):
            try:
                candidate = importlib.import_module(module_name)
            except ImportError:
                continue
            if all(hasattr(candidate, name) for name in RENDERER_NAMES):
                api = candidate
                break
    missing = [name for name in RENDERER_NAMES if not hasattr(api, name)]
    assert not missing, (
        f"Answer renderers missing from navig.ui.answer (plan §4): {missing}"
    )
    rendered = {}
    for name in RENDERER_NAMES:
        if name == "render_cli":
            continue
        rendered[name.removeprefix("to_")] = getattr(api, name)
    return rendered


def _sample_utterances() -> dict[str, str]:
    """One sample per tool: the owner's golden fixture wins; TOOL_SAMPLE is the fallback."""
    fixture = S.CORE_ROOT / "tests" / "reflex" / "golden_utterances.json"
    samples: dict[str, str] = {}
    if fixture.exists():
        try:
            data = json.loads(fixture.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            pytest.fail(f"golden fixture is not valid JSON: {exc}", pytrace=False)
        rows = data.get("utterances", data) if isinstance(data, dict) else data
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            tool = item.get("tool")
            text = item.get("text")
            if tool and text and tool not in samples:
                samples[str(tool)] = str(text)
    for tool, text in TOOL_SAMPLE.items():
        samples.setdefault(tool, text)
    return samples


def _render_cli(mod):
    api = mod
    if not hasattr(api, "render_cli"):
        for module_name in ("navig.ui.answer_render", "navig.ui"):
            try:
                candidate = importlib.import_module(module_name)
            except ImportError:
                continue
            if hasattr(candidate, "render_cli"):
                return candidate.render_cli
    assert hasattr(mod, "render_cli"), "render_cli is required by plan §4"
    return mod.render_cli


def _fixture_answer(mod):
    return mod.Answer(
        speech=SENTENCE,
        display_text=DISPLAY,
        ok=True,
        table={"columns": ["Device", "State"], "rows": [[DEVICE, NEW_STATE]]},
        json={"device": DEVICE_ID, "name": DEVICE, "state": NEW_STATE},
        directives=[{"type": "highlight", "params": {"device": DEVICE_ID}}],
        suggestions=[SUGGESTION],
        data={"device": DEVICE_ID, "name": DEVICE, "state": NEW_STATE},
        intent="home.off",
    )


def _serialised(rendered) -> str:
    if isinstance(rendered, str):
        return rendered.lower()
    return json.dumps(rendered, default=str).lower()


def _assert_agrees(rendered, channel: str) -> None:
    text = _serialised(rendered)
    assert DEVICE.lower() in text or "desk-lamp" in text, f"{channel}: device name missing: {text[:700]}"
    assert NEW_STATE in text, f"{channel}: new state 'off' missing: {text[:700]}"


def test_answer_roundtrip_validates():
    mod = _answer_module()
    answer = _fixture_answer(mod)
    obj = answer.as_dict()
    assert mod.validate_answer(obj) == []
    restored = mod.Answer.from_dict(obj)
    assert restored.speech == SENTENCE
    assert restored.suggestions == [SUGGESTION]
    assert restored.table == answer.table


def test_answer_schema_file_is_the_wire_contract():
    schema_path = S.require_path("core/navig/ui/answer.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    properties = schema.get("properties") or {}
    version = (properties.get("schema_version") or {}).get("const")
    if version is None:
        version = schema.get("version")
    assert str(version) == "1", (
        f"Answer wire contract must pin version '1' (schema_version.const), got {version!r}"
    )
    required = set(schema.get("required", ()))
    assert {"ok", "speech", "display_text", "data"} <= required, (
        f"schema must require the documented Answer fields, got {sorted(required)}"
    )


def test_one_answer_renders_every_channel_consistently(capsys):
    mod = _answer_module()
    answer = _fixture_answer(mod)

    render_cli = _render_cli(mod)
    render_cli(answer)
    cli_text = capsys.readouterr().out
    assert DEVICE.lower() in cli_text.lower(), f"cli: device name missing: {cli_text[:700]}"
    assert NEW_STATE in cli_text.lower(), f"cli: new state missing: {cli_text[:700]}"

    for channel, renderer in _renderers(mod).items():
        _assert_agrees(renderer(answer), channel)


def test_suggestions_land_where_the_surface_supports_them():
    mod = _answer_module()
    answer = _fixture_answer(mod)
    renderers = _renderers(mod)
    alexa = renderers["alexa"](answer)
    assert SUGGESTION in json.dumps(alexa, default=str), "Alexa reprompt must carry the first suggestion"
    alice = renderers["alice"](answer)
    assert SUGGESTION in json.dumps(alice, default=str), "Alice buttons must carry the suggestion"
    telegram = renderers["telegram"](answer)
    assert SUGGESTION in json.dumps(telegram, default=str), "Telegram buttons must carry the suggestion"


def test_no_llm_call_while_rendering(monkeypatch, capsys):
    import navig.commands.ai as ai_mod
    import navig.llm.routing.router as router_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("an LLM was called while rendering an Answer — plan §4 says never")

    monkeypatch.setattr(ai_mod, "ask_ai", boom)
    monkeypatch.setattr(router_mod.UnifiedRouter, "route", boom)

    mod = _answer_module()
    answer = _fixture_answer(mod)
    for renderer in _renderers(mod).values():
        renderer(answer)
    _render_cli(mod)(answer)
    capsys.readouterr()
    mod.validate_answer(answer.as_dict())


def test_bare_dict_is_not_an_answer():
    mod = _answer_module()
    problems = mod.validate_answer({"foo": "bar"})
    assert problems, "a bare dict without the Answer fields must fail validate_answer"
    incomplete = mod.validate_answer({"speech": "hi", "ok": True})
    assert incomplete, "an Answer missing display_text/data/... must fail structural validation"


def test_from_home_status_adapter():
    mod = _answer_module()
    payload = {
        "ok": True,
        "count": 1,
        "devices": [
            {
                "device": "desk-lamp",
                "name": DEVICE,
                "room": "office",
                "kind": "light",
                "provider": "govee",
                "ok": True,
                "state": {"power": False},
                "power": "off",
            }
        ],
        "providers": [],
    }
    answer = mod.Answer.from_home_status(payload)
    assert mod.validate_answer(answer.as_dict()) == []
    serialised = json.dumps(answer.as_dict(), default=str).lower()
    assert DEVICE.lower() in serialised or "desk-lamp" in serialised
    # The adapter reports the plugin's facts (readiness/state); the channel-agreement
    # assertion for the new state lives in the fixture-Answer renderer test above.
    assert "desk lamp" in serialised or "desk-lamp" in serialised


def test_registry_tools_return_valid_answers(fake_home, capsys):
    manifest = S.require("navig.reflex.manifest")
    entries = S.tool_entries(manifest)
    names = {name for name, _entry in entries}
    assert S.EXPECTED_TOOLS <= names, f"reflex registry is missing tools: {sorted(S.EXPECTED_TOOLS - names)}"

    for name, entry in entries:
        description = S.entry_field(entry, "description")
        assert isinstance(description, str) and description.strip(), (
            f"tool {name!r} must carry a mandatory description (plan §3.2)"
        )
        command = S.entry_command(entry)
        assert command, f"tool {name!r} must bind to an existing CLI command (plan §3.2)"

    samples = _sample_utterances()
    for tool in sorted(S.EXPECTED_TOOLS):
        utterance = samples[tool]
        exit_code, stdout, stderr = S.invoke_cli(["ask", utterance, "--plan", "--json"], capsys)
        text = S.combined(stdout, stderr)
        assert exit_code == 0, f"tool {tool!r} sample {utterance!r} failed (exit {exit_code}): {text[-1200:]}"
        envelope = S.json_payload(text)
        S.answer_envelope_ok(envelope)
        plan = envelope.get("plan")
        assert isinstance(plan, dict) and plan, (
            f"tool {tool!r}: sample {utterance!r} produced no plan — "
            f"envelope={json.dumps(envelope, default=str)[:500]}"
        )
        assert plan.get("tool") == tool, f"{utterance!r} routed to {plan.get('tool')!r}, expected {tool!r}"
        assert fake_home.mutations == [], "a --plan Answer must never mutate"
