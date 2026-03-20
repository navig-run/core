from __future__ import annotations

import json
from pathlib import Path

import yaml

from navig.onboarding.engine import EngineConfig
from navig.onboarding.genesis import load_or_create
from navig.onboarding.steps import build_step_registry
from navig.onboarding.validators import (
    validate_matrix,
    validate_smtp,
    validate_telegram,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response

    def post(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


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


class _FakeSMTP:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return 250, b"mx.example.test Hello"


class _TimeoutSMTP:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        raise TimeoutError("timed out")


def _registry(tmp_path: Path):
    config = EngineConfig(navig_dir=tmp_path, node_name="test-node")
    genesis = load_or_create(tmp_path, "test-node")
    return build_step_registry(config, genesis)


def test_build_step_registry_includes_new_integration_steps(tmp_path: Path) -> None:
    step_ids = [step.id for step in _registry(tmp_path)]

    assert step_ids == [
        "workspace-init",
        "workspace-templates",
        "config-file",
        "configure-ssh",
        "verify-network",
        "sigil-genesis",
        "core-navig",
        "ai-provider",
        "vault-init",
        "first-host",
        "matrix",
        "telegram-bot",
        "email",
        "social-networks",
        "runtime-secrets",
        "skills-activation",
        "review",
    ]


def test_validate_matrix_success_and_auth_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.onboarding.validators._http_client",
        lambda: _FakeClient(_FakeResponse(200, {"user_id": "@neo:matrix.org"})),
    )
    ok = validate_matrix("https://matrix.org", "token")
    assert ok.ok is True
    assert ok.info["user_id"] == "@neo:matrix.org"

    monkeypatch.setattr(
        "navig.onboarding.validators._http_client",
        lambda: _FakeClient(_FakeResponse(401, {"error": "M_UNKNOWN_TOKEN"})),
    )
    bad = validate_matrix("https://matrix.org", "bad")
    assert bad.ok is False
    assert bad.errors[0]["field"] == "access_token"


def test_validate_telegram_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.onboarding.validators._http_client",
        lambda: _FakeClient(exc=TimeoutError("timeout")),
    )

    result = validate_telegram("123:abc")

    assert result.ok is False
    assert "Could not reach Telegram API" in result.errors[0]["message"]


def test_validate_smtp_success_and_timeout(monkeypatch) -> None:
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    ok = validate_smtp("smtp.example.test", 587)
    assert ok.ok is True
    assert ok.info["port"] == 587

    monkeypatch.setattr("smtplib.SMTP", _TimeoutSMTP)
    bad = validate_smtp("smtp.example.test", 587)
    assert bad.ok is False
    assert "Could not reach SMTP server" in bad.errors[0]["message"]


def test_matrix_step_writes_config_and_vault(monkeypatch, tmp_path: Path) -> None:
    fake_vault = _FakeVault()
    step = next(step for step in _registry(tmp_path) if step.id == "matrix")
    prompts = iter([
        "https://matrix.org",
        "matrix-token",
        "!room:matrix.org",
    ])

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("navig.vault.core_v2.get_vault_v2", lambda: fake_vault)
    monkeypatch.setattr(
        "navig.onboarding.validators.validate_matrix",
        lambda homeserver_url, token: type(
            "ValidationResultStub",
            (),
            {"ok": True, "errors": [], "info": {"user_id": "@neo:matrix.org"}},
        )(),
    )

    result = step.run()
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))

    assert result.status == "completed"
    assert fake_vault.secrets["matrix/access_token"] == "matrix-token"
    assert config["matrix"]["homeserver_url"] == "https://matrix.org"
    assert config["matrix"]["default_room_id"] == "!room:matrix.org"


def test_review_step_returns_jump_target_when_user_declines(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "onboarding.json"
    artifact.write_text(
        json.dumps(
            {
                "version": 1,
                "nodeId": "node-test",
                "startedAt": "2026-03-17T00:00:00+00:00",
                "completedAt": "",
                "engineVersion": "2.0.0",
                "steps": [
                    {"id": "core-navig", "title": "Core", "status": "completed", "completed_at": "", "duration_ms": 1, "output": {}},
                    {"id": "matrix", "title": "Matrix", "status": "skipped", "completed_at": "", "duration_ms": 1, "output": {}},
                ],
            }
        ),
        encoding="utf-8",
    )

    step = next(step for step in _registry(tmp_path) if step.id == "review")
    prompts = iter(["matrix"])

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(prompts))

    result = step.run()

    assert result.status == "skipped"
    assert result.output["jumpTo"] == "matrix"
