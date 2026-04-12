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
import pytest

pytestmark = pytest.mark.integration


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
        "terminal-setup",
        "workspace-templates",
        "config-file",
        "configure-ssh",
        "verify-network",
        "sigil-genesis",
        "core-navig",
        "ai-provider",
        "vault-init",
        "web-search-provider",
        "voice-provider",
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


def test_web_search_provider_empty_enter_skips_on_fresh_init(monkeypatch, tmp_path: Path) -> None:
    """Pressing Enter (default 's') on fresh init should skip and write 'auto'."""
    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Simulate pressing Enter — typer.prompt returns the default "s"
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: kwargs.get("default", "s"))

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "skipped by user"
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["web"]["search"]["provider"] == "auto"


def test_web_search_provider_empty_enter_preserves_existing_on_reconfigure(
    monkeypatch, tmp_path: Path
) -> None:
    """Pressing Enter in reconfigure mode should preserve the existing provider."""
    # Pre-write an existing provider config
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"web": {"search": {"provider": "brave"}}}), encoding="utf-8")

    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Simulate pressing Enter — typer.prompt returns the default "s"
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: kwargs.get("default", "s"))

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "skipped by user"
    # Existing provider must NOT be overwritten to "auto"
    config_after = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config_after["web"]["search"]["provider"] == "brave"


def test_ai_provider_empty_enter_skips_on_fresh_init(monkeypatch, tmp_path: Path) -> None:
    """Pressing Enter (default 's') on AI provider prompt should skip the step."""
    step = next(step for step in _registry(tmp_path) if step.id == "ai-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "navig.providers.source_scan.detect_provider_sources",
        lambda provider_id, navig_dir=None: [],
    )
    # Simulate pressing Enter — typer.prompt returns the default "s"
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: kwargs.get("default", "s"))

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "skipped by user"
    # Marker must NOT be written when the step is skipped
    assert not (tmp_path / ".ai_provider_configured").exists()


def test_ai_provider_empty_enter_skips_on_reconfigure(monkeypatch, tmp_path: Path) -> None:
    """Pressing Enter in reconfigure mode preserves the existing AI provider."""
    marker = tmp_path / ".ai_provider_configured"
    marker.write_text("anthropic", encoding="utf-8")

    step = next(step for step in _registry(tmp_path) if step.id == "ai-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Simulate pressing Enter — typer.prompt returns the default "s"
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: kwargs.get("default", "s"))

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "skipped by user"
    # Marker must retain the existing value, not be overwritten
    assert marker.read_text(encoding="utf-8").strip() == "anthropic"


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
    monkeypatch.setattr("navig.vault.core.get_vault", lambda: fake_vault)
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


def test_matrix_step_rerun_with_skipped_marker_stays_skipped(monkeypatch, tmp_path: Path) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "matrix")
    (tmp_path / ".matrix_configured").write_text("skipped", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "non-interactive environment"


def test_email_step_rerun_with_skipped_marker_stays_skipped(monkeypatch, tmp_path: Path) -> None:
    step = next(step for step in _registry(tmp_path) if step.id == "email")
    (tmp_path / ".email_configured").write_text("skipped", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "non-interactive environment"


def test_social_step_rerun_with_skipped_marker_stays_skipped(monkeypatch, tmp_path: Path) -> None:
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


# ── Regression: false-positive [✓] completed on --reconfigure ────────────────


def test_matrix_step_reconfigure_with_skipped_marker_re_prompts(
    monkeypatch, tmp_path: Path
) -> None:
    """On --reconfigure, a previously-skipped matrix step must re-prompt, not return completed."""
    marker = tmp_path / ".matrix_configured"
    marker.write_text("skipped", encoding="utf-8")

    step = next(step for step in _registry(tmp_path) if step.id == "matrix")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # User presses Enter again — still no URL.
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "no homeserver URL provided"


def test_email_step_reconfigure_with_skipped_marker_re_prompts(monkeypatch, tmp_path: Path) -> None:
    """On --reconfigure, a previously-skipped email step must re-prompt, not return completed."""
    marker = tmp_path / ".email_configured"
    marker.write_text("skipped", encoding="utf-8")

    step = next(step for step in _registry(tmp_path) if step.id == "email")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # User presses Enter — blank SMTP host.
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "")

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "no SMTP host provided"


def test_social_networks_step_reconfigure_with_skipped_marker_re_prompts(
    monkeypatch, tmp_path: Path
) -> None:
    """On --reconfigure, a previously-skipped social-networks step must re-prompt."""
    marker = tmp_path / ".social_configured"
    marker.write_text("skipped", encoding="utf-8")

    step = next(step for step in _registry(tmp_path) if step.id == "social-networks")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # User declines again.
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    result = step.run()

    assert result.status == "skipped"
    assert result.output["reason"] == "user declined"


def test_web_search_provider_empty_api_key_returns_skipped(monkeypatch, tmp_path: Path) -> None:
    """Selecting a catalog provider but leaving the API key blank must return skipped."""
    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.write", lambda *a: None)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    # typer.prompt is only called once (for the provider-index selection).
    # The API key prompt goes through _prompt_masked, which is mocked separately.
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "1")  # choose perplexity

    # _prompt_masked returns "" — user pressed Enter for the API key.
    monkeypatch.setattr("navig.onboarding.steps._prompt_masked", lambda *a, **kw: "")

    result = step.run()

    assert result.status == "skipped"
    assert "API key" in result.output.get("reason", "")
    assert result.output.get("provider") == "perplexity"


def test_web_search_provider_with_api_key_returns_completed(monkeypatch, tmp_path: Path) -> None:
    """Selecting a catalog provider and supplying a valid API key must return completed."""
    step = next(step for step in _registry(tmp_path) if step.id == "web-search-provider")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.write", lambda *a: None)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    # typer.prompt is only called once (for the provider-index selection).
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "2")  # choose brave
    monkeypatch.setattr("navig.onboarding.steps._prompt_masked", lambda *a, **kw: "my-brave-key")

    result = step.run()

    assert result.status == "completed"
    assert result.output.get("provider") == "brave"
