from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

runner = CliRunner()


def _prepare_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()


def test_normalize_route_tier_aliases():
    from navig.commands.mode import _normalize_route_tier

    assert _normalize_route_tier("small") == "small"
    assert _normalize_route_tier("s") == "small"
    assert _normalize_route_tier("big") == "big"
    assert _normalize_route_tier("c") == "coder_big"
    assert _normalize_route_tier("code") == "coder_big"


def test_mode_route_set_persists_small_slot(tmp_path: Path, monkeypatch):
    _prepare_config(tmp_path, monkeypatch)

    from navig.commands.mode import mode_app
    from navig.config import get_config_manager

    result = runner.invoke(
        mode_app,
        [
            "route",
            "set",
            "small",
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
        ],
    )

    assert result.exit_code == 0

    cfg = get_config_manager(force_new=True).global_config
    slot = (((cfg.get("ai") or {}).get("routing") or {}).get("models") or {}).get("small") or {}
    assert slot.get("provider") == "openai"
    assert slot.get("model") == "gpt-4o-mini"


def test_mode_route_set_rejects_invalid_tier(tmp_path: Path, monkeypatch):
    _prepare_config(tmp_path, monkeypatch)

    from navig.commands.mode import mode_app

    result = runner.invoke(mode_app, ["route", "set", "unknown", "--provider", "openai"])
    assert result.exit_code == 1
    assert "Tier must be one of" in result.stdout
