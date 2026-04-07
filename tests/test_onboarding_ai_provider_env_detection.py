"""Tests for early env-var detection in the ai-provider onboarding step.

Verifies that OPENAI_API_KEY (and other provider keys) are detected and
used at step 8 (ai-provider), not delayed until step 16 (runtime-secrets).
"""
from __future__ import annotations

import json
from pathlib import Path

from navig.onboarding.engine import EngineConfig
from navig.onboarding.genesis import load_or_create
from navig.onboarding.steps import build_step_registry

# All env vars that the ai-provider step may read so tests can isolate them.
_ALL_PROVIDER_ENV_VARS = [
    "NAVIG_LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "NIM_API_KEY",
    "XAI_API_KEY",
    "GROK_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "MISTRAL_API_KEY",
]


class _FakeVault:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def put(self, label: str, payload: bytes, **kwargs) -> str:
        self.secrets[label] = json.loads(payload.decode("utf-8"))["value"]
        return label

    def get_secret(self, label: str) -> str:
        if label not in self.secrets:
            raise KeyError(label)
        return self.secrets[label]


def _ai_provider_step(tmp_path: Path):
    config = EngineConfig(navig_dir=tmp_path, node_name="test-node")
    genesis = load_or_create(tmp_path, "test-node")
    steps = build_step_registry(config, genesis)
    return next(step for step in steps if step.id == "ai-provider")


def _clear_provider_env(monkeypatch) -> None:
    """Remove all provider-related env vars to ensure test isolation."""
    for var in _ALL_PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_ai_provider_step_uses_openai_key_from_env(monkeypatch, tmp_path: Path) -> None:
    """When OPENAI_API_KEY is set, the step should auto-select OpenAI and use the env key."""
    _clear_provider_env(monkeypatch)
    fake_vault = _FakeVault()
    step = _ai_provider_step(tmp_path)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core.get_vault", lambda: fake_vault)

    # Simulate user accepting the pre-selected default (first detected provider)
    # and skipping the fallback provider prompt.
    def _mock_prompt(text: str, default: str = "", **kwargs) -> str:
        return default

    monkeypatch.setattr("typer.prompt", _mock_prompt)

    result = step.run()

    assert result.status == "completed"
    assert result.output["provider"] == "openai"
    key_source = str(result.output["keySource"])
    assert key_source == "environment" or key_source.startswith("existing:")
    assert "env" in key_source
    assert fake_vault.secrets.get("openai/api_key") == "sk-test-openai-key"
    # Confirm the marker file was written
    assert (tmp_path / ".ai_provider_configured").exists()


def test_ai_provider_step_uses_anthropic_key_from_env(monkeypatch, tmp_path: Path) -> None:
    """When ANTHROPIC_API_KEY is set, the step should auto-select Anthropic and use it."""
    _clear_provider_env(monkeypatch)
    fake_vault = _FakeVault()
    step = _ai_provider_step(tmp_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core.get_vault", lambda: fake_vault)

    def _mock_prompt(text: str, default: str = "", **kwargs) -> str:
        return default

    monkeypatch.setattr("typer.prompt", _mock_prompt)

    result = step.run()

    assert result.status == "completed"
    assert result.output["provider"] == "anthropic"
    key_source = str(result.output["keySource"])
    assert key_source == "environment" or key_source.startswith("existing:")
    assert "env" in key_source
    assert fake_vault.secrets.get("anthropic/api_key") == "sk-ant-test-key"


def test_ai_provider_step_no_env_key_prompts_user(monkeypatch, tmp_path: Path) -> None:
    """When no env key is set, the step should prompt the user for a key."""
    _clear_provider_env(monkeypatch)
    fake_vault = _FakeVault()
    step = _ai_provider_step(tmp_path)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core.get_vault", lambda: fake_vault)

    # Simulate user selecting the default provider (index 1) and skipping fallback.
    def _mock_prompt(text: str, default: str = "", **kwargs) -> str:
        if "Provider" in text:
            return "1"
        return default

    monkeypatch.setattr("typer.prompt", _mock_prompt)
    # _prompt_masked needs to return a non-empty key to avoid "no key entered" skip.
    monkeypatch.setattr(
        "navig.onboarding.steps._prompt_masked", lambda *a, **kw: "interactive-key"
    )

    result = step.run()

    assert result.status == "completed"
    assert result.output["keySource"] == "interactive"


def test_ai_provider_step_env_key_not_reimported_at_runtime_secrets(
    monkeypatch, tmp_path: Path
) -> None:
    """OPENAI_API_KEY handled at step 8 should NOT be offered again at step 16."""
    from navig.onboarding.steps import _ENV_KEY_IMPORTS

    env_vars_in_step16 = [entry[0] for entry in _ENV_KEY_IMPORTS]
    assert "OPENAI_API_KEY" not in env_vars_in_step16, (
        "OPENAI_API_KEY should be handled at the ai-provider step (step 8), "
        "not re-offered at the runtime-secrets step (step 16)."
    )
    assert "ANTHROPIC_API_KEY" not in env_vars_in_step16, (
        "ANTHROPIC_API_KEY should be handled at the ai-provider step (step 8), "
        "not re-offered at the runtime-secrets step (step 16)."
    )

