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
        "web-search-provider",
        "first-host",
        "matrix",
        "telegram-bot",
        "email",
        "social-networks",
        "runtime-secrets",
        "skills-activation",
        "review",
    ]


def test_web_search_provider_step_accepts_env_duckduckgo(monkeypatch, tmp_path: Path) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setenv("NAVIG_WEB_SEARCH_PROVIDER", "ddg")
    result = step.run()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert result.status == "completed"
    assert result.output["provider"] == "duckduckgo"
    assert config["web"]["search"]["provider"] == "duckduckgo"


def test_web_search_provider_step_invalid_env_falls_back_to_auto(
    monkeypatch, tmp_path: Path
) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setenv("NAVIG_WEB_SEARCH_PROVIDER", "unsupported-provider")
    result = step.run()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert result.status == "skipped"
    assert result.output["provider"] == "auto"
    assert "invalid" in result.output["reason"]
    assert config["web"]["search"]["provider"] == "auto"


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
    prompts = iter(
        [
            "https://matrix.org",
            "matrix-token",
            "!room:matrix.org",
        ]
    )

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


def test_matrix_step_skips_when_no_url_entered(monkeypatch, tmp_path: Path) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "matrix")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "no homeserver URL provided"
    assert (tmp_path / ".matrix_configured").read_text(encoding="utf-8") == "skipped"


def test_matrix_step_rerun_with_skipped_marker_stays_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "matrix")
    (tmp_path / ".matrix_configured").write_text("skipped", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "non-interactive environment"


def test_email_step_rerun_with_skipped_marker_stays_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "email")
    (tmp_path / ".email_configured").write_text("skipped", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "non-interactive environment"


def test_social_step_rerun_with_skipped_marker_stays_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "social-networks")
    (tmp_path / ".social_configured").write_text("skipped", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "non-interactive environment"


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
                    {
                        "id": "core-navig",
                        "title": "Core",
                        "status": "completed",
                        "completed_at": "",
                        "duration_ms": 1,
                        "output": {},
                    },
                    {
                        "id": "matrix",
                        "title": "Matrix",
                        "status": "skipped",
                        "completed_at": "",
                        "duration_ms": 1,
                        "output": {},
                    },
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


def test_review_step_rejects_unknown_id_and_reprompts(monkeypatch, tmp_path: Path) -> None:
    """Review step must loop on unknown IDs and accept a valid one on retry."""
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
                    {
                        "id": "core-navig",
                        "title": "Core",
                        "status": "completed",
                        "completed_at": "",
                        "duration_ms": 1,
                        "output": {},
                    },
                    {
                        "id": "ai-provider",
                        "title": "AI Provider",
                        "status": "skipped",
                        "completed_at": "",
                        "duration_ms": 1,
                        "output": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    step = next(step for step in _registry(tmp_path) if step.id == "review")
    # First two prompts return unknown IDs, third returns a valid one.
    prompts = iter(["mail", "email", "ai-provider"])

    def _fake_prompt(*args, **kwargs):
        return next(prompts)

    captured_output: list[str] = []

    def _fake_write(text: str) -> None:
        captured_output.append(text)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr("typer.prompt", _fake_prompt)
    monkeypatch.setattr("sys.stdout.write", _fake_write)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    result = step.run()

    assert result.status == "skipped"
    # Should have iterated until a valid ID was entered.
    assert result.output["jumpTo"] == "ai-provider"
    # Error message should have been printed for each invalid entry.
    error_lines = [line for line in captured_output if "Unknown step ID" in line]
    assert len(error_lines) == 2  # "mail" and "email" were both rejected


def test_review_step_keyboard_interrupt_propagates(monkeypatch, tmp_path: Path) -> None:
    """Ctrl+C during the step-ID prompt must propagate as KeyboardInterrupt."""
    step = next(step for step in _registry(tmp_path) if step.id == "review")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    def _raise_ki(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("typer.prompt", _raise_ki)
    monkeypatch.setattr("sys.stdout.write", lambda *a: None)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    import pytest as _pytest
    with _pytest.raises(KeyboardInterrupt):
        step.run()


def test_review_step_eoferror_exits_cleanly(monkeypatch, tmp_path: Path) -> None:
    """EOF during the step-ID prompt (e.g. piped input) must return empty jumpTo."""
    step = next(step for step in _registry(tmp_path) if step.id == "review")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    def _raise_eof(*args, **kwargs):
        raise EOFError

    monkeypatch.setattr("typer.prompt", _raise_eof)
    monkeypatch.setattr("sys.stdout.write", lambda *a: None)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    result = step.run()
    assert result.status == "skipped"
    assert result.output["jumpTo"] == ""
