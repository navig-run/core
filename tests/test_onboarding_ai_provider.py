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


def test_ai_provider_step_prompts_llamacpp_url_and_saves_override(monkeypatch, tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    provider = SimpleNamespace(id="llamacpp", display_name="llama.cpp", requires_key=False)
    manifest = SimpleNamespace(env_vars=[], local_probe="127.0.0.1:8080")

    prompts: list[tuple[str, str]] = []
    responses = iter(["1", "http://127.0.0.1:8088", "s"])

    def _fake_prompt(text, default=""):
        prompts.append((str(text), str(default)))
        return next(responses)

    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [provider])
    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _pid: manifest)
    monkeypatch.setattr("typer.prompt", _fake_prompt)

    from navig.onboarding.steps import _step_ai_provider

    step = _step_ai_provider(navig_dir)
    result = step.run()

    assert result.status == "completed"
    assert result.output["provider"] == "llamacpp"
    assert result.output["base_url"] == "http://127.0.0.1:8088"
    assert result.output["baseUrlSaved"] == "config"

    cfg_path = navig_dir / "config.yaml"
    cfg = cfg_path.read_text(encoding="utf-8")
    assert "llm_router:" in cfg
    assert "provider_base_urls:" in cfg
    assert "llamacpp: http://127.0.0.1:8088" in cfg

    llama_prompt = [p for p in prompts if "llama.cpp URL" in p[0]]
    assert llama_prompt
    assert llama_prompt[0][1] == "http://127.0.0.1:8080"
