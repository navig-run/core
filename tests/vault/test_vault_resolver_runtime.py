from __future__ import annotations

from pathlib import Path

from navig.vault.resolver import resolve_json_str, resolve_secret
import pytest

pytestmark = pytest.mark.integration


class _FakeVault:
    def __init__(
        self,
        secrets: dict[str, str] | None = None,
        json_blobs: dict[str, str] | None = None,
    ) -> None:
        self.secrets = secrets or {}
        self.json_blobs = json_blobs or {}

    def get_secret(self, label: str) -> str:
        if label not in self.secrets:
            raise KeyError(label)
        return self.secrets[label]

    def get_json_str(self, label: str) -> str:
        if label not in self.json_blobs:
            raise KeyError(label)
        return self.json_blobs[label]


def test_resolve_secret_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _FakeVault(secrets={"openai/api_key": "vault-openai"}),
    )

    assert resolve_secret(["OPENAI_API_KEY"], ["openai/api_key"]) == "env-openai"


def test_resolve_secret_falls_back_to_vault(monkeypatch) -> None:
    monkeypatch.delenv("DEEPGRAM_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _FakeVault(secrets={"deepgram/api_key": "vault-deepgram"}),
    )

    assert (
        resolve_secret(["DEEPGRAM_KEY", "DEEPGRAM_API_KEY"], ["deepgram/api_key"])
        == "vault-deepgram"
    )


def test_resolve_json_str_reads_path_from_env(monkeypatch, tmp_path: Path) -> None:
    service_account = tmp_path / "sa.json"
    service_account.write_text('{"type":"service_account","project_id":"demo"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(service_account))

    assert '"project_id":"demo"' in resolve_json_str(
        ["GOOGLE_APPLICATION_CREDENTIALS"], ["google/vision-service-account"]
    )


def test_resolve_json_str_falls_back_to_vault(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _FakeVault(
            json_blobs={"google/vision-service-account": '{"type":"service_account"}'}
        ),
    )

    assert (
        resolve_json_str(["GOOGLE_APPLICATION_CREDENTIALS"], ["google/vision-service-account"])
        == '{"type":"service_account"}'
    )
