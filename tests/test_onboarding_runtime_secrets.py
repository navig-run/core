from __future__ import annotations

import json
from pathlib import Path

from navig.onboarding.engine import EngineConfig
from navig.onboarding.genesis import load_or_create
from navig.onboarding.steps import build_step_registry


class _FakeVault:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}
        self.json_blobs: dict[str, str] = {}

    def put(self, label: str, payload: bytes, **kwargs) -> str:
        self.secrets[label] = json.loads(payload.decode("utf-8"))["value"]
        return label

    def put_json_file(self, label: str, source: str, **kwargs) -> str:
        json.loads(source)
        self.json_blobs[label] = source
        return label

    def get_secret(self, label: str) -> str:
        if label not in self.secrets:
            raise KeyError(label)
        return self.secrets[label]

    def get_json_str(self, label: str) -> str:
        if label not in self.json_blobs:
            raise KeyError(label)
        return self.json_blobs[label]


def _runtime_step(tmp_path: Path):
    config = EngineConfig(navig_dir=tmp_path, node_name="test-node")
    genesis = load_or_create(tmp_path, "test-node")
    steps = build_step_registry(config, genesis)
    return next(step for step in steps if step.id == "runtime-secrets")


def test_runtime_secrets_step_marks_configured_when_blank(monkeypatch, tmp_path: Path) -> None:
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    assert (tmp_path / ".runtime_secrets_configured").exists()
    assert fake_vault.secrets == {}
    assert fake_vault.json_blobs == {}


def test_runtime_secrets_step_imports_env_into_vault(monkeypatch, tmp_path: Path) -> None:
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    assert fake_vault.secrets["openai/api_key"] == "env-openai"
    assert "OpenAI API key" in result.output["importedFromEnv"]


def test_runtime_secrets_emits_retroactive_update_for_openai_key(
    monkeypatch, tmp_path: Path
) -> None:
    """Importing OPENAI_API_KEY should emit a retroactive update for ai-provider."""
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    # Retroactive update should be present
    updates = result.output.get("_retroactiveUpdates", [])
    assert len(updates) == 1
    upd = updates[0]
    assert upd["id"] == "ai-provider"
    assert upd["status"] == "completed"
    assert upd["output"]["provider"] == "openai"
    assert upd["output"]["keySource"] == "environment"

    # ai-provider marker file should be written
    assert (tmp_path / ".ai_provider_configured").exists()
    assert (tmp_path / ".ai_provider_configured").read_text(encoding="utf-8") == "openai"


def test_runtime_secrets_emits_retroactive_update_for_anthropic_key(
    monkeypatch, tmp_path: Path
) -> None:
    """Importing ANTHROPIC_API_KEY should emit a retroactive update for ai-provider."""
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    updates = result.output.get("_retroactiveUpdates", [])
    assert len(updates) == 1
    assert updates[0]["id"] == "ai-provider"
    assert updates[0]["output"]["provider"] == "anthropic"

    assert (tmp_path / ".ai_provider_configured").read_text(encoding="utf-8") == "anthropic"


def test_runtime_secrets_no_retroactive_update_for_non_ai_keys(
    monkeypatch, tmp_path: Path
) -> None:
    """Importing SerpAPI or Deepgram keys should NOT emit an ai-provider retroactive update."""
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setenv("SERPAPI_KEY", "serpapi-test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    assert "_retroactiveUpdates" not in result.output
    assert not (tmp_path / ".ai_provider_configured").exists()


def test_runtime_secrets_only_first_ai_provider_emits_retroactive_update(
    monkeypatch, tmp_path: Path
) -> None:
    """When both OPENAI and ANTHROPIC keys are set, only one retroactive update is emitted."""
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "completed"
    updates = result.output.get("_retroactiveUpdates", [])
    # Only one retroactive update regardless of how many AI keys were imported
    assert len(updates) == 1
    # Should be the first AI provider encountered (openai appears before anthropic in _ENV_KEY_IMPORTS)
    assert updates[0]["output"]["provider"] == "openai"


def test_runtime_secrets_step_stores_google_json_in_both_labels(
    monkeypatch, tmp_path: Path
) -> None:
    fake_vault = _FakeVault()
    step = _runtime_step(tmp_path)
    json_blob = '{"type":"service_account","project_id":"demo"}'
    input_values = iter([json_blob, "END"])

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(input_values))

    result = step.run()

    assert result.status == "completed"
    assert fake_vault.json_blobs["google/vision-service-account"] == json_blob
    assert fake_vault.json_blobs["google/tts-service-account"] == json_blob
