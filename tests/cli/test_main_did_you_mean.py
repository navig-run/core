from __future__ import annotations

import pytest

import navig.main as main_mod

pytestmark = pytest.mark.unit


class _DummyApp:
    def __call__(self):
        raise SystemExit(2)


class _DummyCliModule:
    app = _DummyApp()

    @staticmethod
    def _register_external_commands():
        return None


def test_main_did_you_mean_uses_first_non_global_token(monkeypatch):
    called: dict[str, str] = {}

    monkeypatch.setattr(main_mod, "_normalize_help_compat_args", lambda argv: argv)
    monkeypatch.setattr(main_mod, "_maybe_handle_fast_path", lambda argv: False)
    monkeypatch.setattr(main_mod, "_check_first_run", lambda: None)
    monkeypatch.setattr(main_mod, "_should_skip_plugin_loading", lambda argv: True)
    monkeypatch.setattr(main_mod, "_handle_powershell_parsing_error", lambda argv: None)
    monkeypatch.setattr(main_mod, "_suggest_did_you_mean", lambda token: called.setdefault("token", token))

    monkeypatch.setattr("navig.config.set_config_cache_bypass", lambda _value: None)
    monkeypatch.setattr("navig.config.reset_config_manager", lambda: None)
    monkeypatch.setattr(
        "navig.config.get_config_manager",
        lambda: type("_Cfg", (), {"global_config_dir": "."})(),
    )
    monkeypatch.setattr(
        "navig.migrations.workspace_to_spaces.migrate_workspace_to_spaces",
        lambda _path: None,
    )
    monkeypatch.setattr(
        "navig.migrations.workspace_to_spaces.ensure_no_stale_spaces_registration",
        lambda: None,
    )

    import navig.cli as cli_mod

    monkeypatch.setattr(cli_mod, "app", _DummyApp())
    monkeypatch.setattr(cli_mod, "_register_external_commands", lambda: None)
    monkeypatch.setattr(main_mod.sys, "argv", ["navig", "--host", "prod", "hst", "list"])

    with pytest.raises(SystemExit):
        main_mod.main()

    assert called.get("token") == "hst"
