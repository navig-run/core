from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_ai_provider_step_allows_enter_to_keep_existing_env_key(monkeypatch, tmp_path: Path, capsys):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    provider = SimpleNamespace(id="openai", display_name="OpenAI", requires_key=True)
    manifest = SimpleNamespace(env_vars=["OPENAI_API_KEY"])

    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [provider])
    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _pid: manifest)
    monkeypatch.setattr("navig.onboarding.steps._prompt_masked", lambda *_a, **_k: "")
    monkeypatch.setattr("typer.prompt", lambda *_a, **_k: "1")

    from navig.onboarding.steps import _step_ai_provider

    step = _step_ai_provider(navig_dir)
    result = step.run()

    out = capsys.readouterr().out
    assert "already configured: env" in out
    assert result.status == "completed"
    assert result.output["provider"] == "openai"
    assert result.output["keySource"].startswith("existing:")
    assert "env" in result.output["keySource"]
