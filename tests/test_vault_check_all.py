"""
Tests for `navig vault check-all` (vault_check_all command).

Covers:
- empty vault exits 0 with a warning
- all-pass: table printed, exit 0
- any-fail: table printed, exit 1
- --json flag: JSON output, exit 1 on failure
- ElevenLabsValidator is registered and can be instantiated
- deepgram + elevenlabs are in PROVIDER_DEFAULTS
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

# ── imports under test ──────────────────────────────────────────────────────
from navig.commands.vault import PROVIDER_DEFAULTS, cred_app, vault_app

runner = CliRunner()


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_cred(provider: str, enabled: bool = True):
    """Return a minimal mock Credential object."""
    cred = MagicMock()
    cred.id = f"id-{provider}"
    cred.provider = provider
    cred.label = provider.capitalize()
    cred.enabled = enabled
    cred.credential_type.value = "api_key"
    cred.last_used_at = None
    cred.data = {"api_key": "test-key"}
    cred.metadata = {}
    return cred


def _make_test_result(success: bool, message: str = "", details: dict | None = None):
    result = MagicMock()
    result.success = success
    result.message = message
    result.details = details or {}
    return result


# ── PROVIDER_DEFAULTS ────────────────────────────────────────────────────────


def test_deepgram_in_provider_defaults():
    """deepgram shortcut was added to PROVIDER_DEFAULTS."""
    assert "deepgram" in PROVIDER_DEFAULTS
    canonical, ctype, data_key, label = PROVIDER_DEFAULTS["deepgram"]
    assert canonical == "deepgram"
    assert ctype == "api_key"
    assert data_key == "api_key"
    assert "deepgram" in label.lower()


def test_elevenlabs_in_provider_defaults():
    """elevenlabs shortcut was added to PROVIDER_DEFAULTS."""
    assert "elevenlabs" in PROVIDER_DEFAULTS
    canonical, ctype, data_key, label = PROVIDER_DEFAULTS["elevenlabs"]
    assert canonical == "elevenlabs"
    assert ctype == "api_key"
    assert data_key == "api_key"
    assert "elevenlabs" in label.lower()


# ── ElevenLabsValidator ──────────────────────────────────────────────────────


def test_elevenlabs_validator_is_registered():
    """ElevenLabsValidator is in the VALIDATORS registry."""
    from navig.vault.validators import VALIDATORS, ElevenLabsValidator

    assert "elevenlabs" in VALIDATORS
    assert VALIDATORS["elevenlabs"] is ElevenLabsValidator


def test_elevenlabs_validator_empty_key_fails():
    from navig.vault.validators import ElevenLabsValidator

    v = ElevenLabsValidator()
    cred = MagicMock()
    cred.data = {"api_key": ""}
    result = v.validate(cred)
    assert result.success is False
    assert "empty" in result.message.lower()


def test_elevenlabs_validator_valid_key():
    """ElevenLabsValidator returns success=True on 200 response."""
    import httpx

    from navig.vault.validators import ElevenLabsValidator

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "subscription": {
            "tier": "free",
            "character_limit": 10000,
            "character_count": 500,
        }
    }

    v = ElevenLabsValidator()
    cred = MagicMock()
    cred.data = {"api_key": "sk-test-key"}

    with patch("httpx.get", return_value=mock_response):
        result = v.validate(cred)

    assert result.success is True
    assert result.details["tier"] == "free"
    assert result.details["character_limit"] == 10000


def test_elevenlabs_validator_invalid_key():
    """ElevenLabsValidator returns success=False on 401."""
    import httpx

    from navig.vault.validators import ElevenLabsValidator

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401

    v = ElevenLabsValidator()
    cred = MagicMock()
    cred.data = {"api_key": "bad-key"}

    with patch("httpx.get", return_value=mock_response):
        result = v.validate(cred)

    assert result.success is False
    assert "invalid" in result.message.lower()


# ── vault check-all command ──────────────────────────────────────────────────


def _mock_vault_for_check_all(creds, validator_results: dict[str, bool]):
    """Return (mock_vault_module, mock_validators_module) for check-all tests."""
    vault_mod = MagicMock()
    vault_instance = MagicMock()
    vault_instance.list_creds.return_value = creds
    vault_mod.get_vault.return_value = vault_instance

    validators_mod = MagicMock()

    def _get_validator(provider):
        success = validator_results.get(provider, True)
        v = MagicMock()
        v.validate.return_value = _make_test_result(
            success=success,
            message="Valid" if success else "Invalid key",
        )
        v.__class__.__name__ = "RemoteValidator"
        return v

    validators_mod.get_validator.side_effect = _get_validator
    return vault_mod, validators_mod


def test_check_all_empty_vault():
    """check-all on empty vault: exit 0, warning shown."""
    vault_mod = MagicMock()
    vault_instance = MagicMock()
    vault_instance.list_creds.return_value = []
    vault_mod.get_vault.return_value = vault_instance

    validators_mod = MagicMock()

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all"])

    assert result.exit_code == 0
    assert "empty" in result.output.lower()


def test_check_all_all_pass():
    """check-all with all passing creds: exit 0, table with ✅."""
    creds = [_make_cred("openai"), _make_cred("anthropic")]
    vault_mod, validators_mod = _mock_vault_for_check_all(
        creds, {"openai": True, "anthropic": True}
    )

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all"])

    assert result.exit_code == 0
    assert "2" in result.output  # "All 2 credential(s) passed"


def test_check_all_one_fails_exits_1():
    """check-all with one failing cred: exit 1."""
    creds = [_make_cred("openai"), _make_cred("groq")]
    vault_mod, validators_mod = _mock_vault_for_check_all(
        creds, {"openai": True, "groq": False}
    )

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all"])

    assert result.exit_code == 1


def test_check_all_json_output_on_failure():
    """check-all --json with failure: JSON list, exit 1."""
    creds = [_make_cred("openai"), _make_cred("telegram")]
    vault_mod, validators_mod = _mock_vault_for_check_all(
        creds, {"openai": True, "telegram": False}
    )

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all", "--json"])

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    providers = {r["provider"] for r in data}
    assert providers == {"openai", "telegram"}
    openai_row = next(r for r in data if r["provider"] == "openai")
    assert openai_row["success"] is True
    telegram_row = next(r for r in data if r["provider"] == "telegram")
    assert telegram_row["success"] is False


def test_check_all_json_output_all_pass():
    """check-all --json all-pass: JSON list, exit 0."""
    creds = [_make_cred("deepgram")]
    vault_mod, validators_mod = _mock_vault_for_check_all(creds, {"deepgram": True})

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["success"] is True


def test_check_all_disabled_creds_skipped():
    """Disabled credentials are not checked."""
    active = _make_cred("openai", enabled=True)
    disabled = _make_cred("anthropic", enabled=False)
    creds = [active, disabled]

    vault_mod = MagicMock()
    vault_instance = MagicMock()
    vault_instance.list_creds.return_value = creds
    vault_mod.get_vault.return_value = vault_instance

    validators_mod = MagicMock()
    v = MagicMock()
    v.validate.return_value = _make_test_result(success=True, message="OK")
    v.__class__.__name__ = "RemoteValidator"
    validators_mod.get_validator.return_value = v

    with (
        patch("navig.commands.vault._vault_mod", vault_mod),
        patch("navig.commands.vault._validators_mod", validators_mod),
    ):
        result = runner.invoke(vault_app, ["check-all"])

    assert result.exit_code == 0
    # get_validator should only have been called once (openai only)
    assert validators_mod.get_validator.call_count == 1


# ── voice-provider onboarding step ───────────────────────────────────────────


def test_voice_provider_step_is_registered():
    """voice-provider step exists in the onboarding registry."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    cfg = MagicMock()
    cfg.navig_dir = Path("/tmp/navig_test")
    cfg.reset = False

    genesis = MagicMock()

    from navig.onboarding.steps import build_step_registry

    steps = build_step_registry(cfg, genesis)
    ids = [s.id for s in steps]
    assert "voice-provider" in ids


def test_voice_provider_step_position():
    """voice-provider comes after web-search-provider and before first-host."""
    from pathlib import Path
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.navig_dir = Path("/tmp/navig_test")
    cfg.reset = False
    genesis = MagicMock()

    from navig.onboarding.steps import build_step_registry

    steps = build_step_registry(cfg, genesis)
    ids = [s.id for s in steps]
    web_idx = ids.index("web-search-provider")
    voice_idx = ids.index("voice-provider")
    host_idx = ids.index("first-host")
    assert web_idx < voice_idx < host_idx


def test_voice_provider_step_skips_without_tty(tmp_path):
    """voice-provider step returns skipped when no TTY."""
    from navig.onboarding.steps import _step_voice_provider

    step = _step_voice_provider(tmp_path)
    # Simulate no-TTY (sys.stdin.isatty() returns False)
    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = step.run()

    assert result.status in ("skipped", "completed")


# ── /provider_voice Telegram command ─────────────────────────────────────────


def test_provider_voice_in_slash_registry():
    """/provider_voice is in the _SLASH_REGISTRY."""
    from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

    cmds = {e.command for e in _SLASH_REGISTRY}
    assert "provider_voice" in cmds
    assert "voice_provider" in cmds  # hidden alias


def test_provider_voice_handler_is_wired():
    """/provider_voice points to _handle_provider_voice."""
    from navig.gateway.channels.telegram_commands import _SLASH_REGISTRY

    entry = next(e for e in _SLASH_REGISTRY if e.command == "provider_voice")
    assert entry.handler == "_handle_provider_voice"
    assert entry.category == "voice"


def test_settings_hub_keyboard_has_voice_keys_button():
    """build_settings_hub_keyboard includes the Voice API Keys button."""
    from navig.gateway.channels.telegram_keyboards import build_settings_hub_keyboard

    rows = build_settings_hub_keyboard()
    all_callbacks = [btn["callback_data"] for row in rows for btn in row]
    assert "st_goto_voice_provider" in all_callbacks


def test_settings_settings_nav_has_voice_provider():
    """st_goto_voice_provider is wired in _NAV to _handle_provider_voice."""
    # The _NAV dict is local to _handle_settings_callback; verify by reading
    # the source text rather than calling the method.
    import pathlib

    src = pathlib.Path(
        "navig/gateway/channels/telegram_keyboards.py"
    ).read_text(encoding="utf-8")
    assert "st_goto_voice_provider" in src
    assert "_handle_provider_voice" in src
