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


def test_runtime_secrets_step_marks_configured_when_blank(
    monkeypatch, tmp_path: Path
) -> None:
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


def test_runtime_secrets_step_imports_env_into_vault(
    monkeypatch, tmp_path: Path
) -> None:
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
