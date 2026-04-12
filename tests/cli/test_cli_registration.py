from __future__ import annotations

import types

import typer

import navig.cli.registration as reg
import pytest

pytestmark = pytest.mark.integration


def test_resolve_cli_target_from_argv_ignores_non_navig_process_argv():
    target = reg._resolve_cli_target_from_argv(["pytest", "-k", "vault"])
    assert target is None


def test_resolve_cli_target_from_argv_reads_navig_argv():
    target = reg._resolve_cli_target_from_argv(["navig", "vault", "list"])
    assert target == "vault"


def test_resolve_cli_target_from_argv_skips_global_flags_and_values():
    target = reg._resolve_cli_target_from_argv([
        "navig",
        "--host",
        "prod",
        "--json",
        "vault",
        "list",
    ])
    assert target == "vault"


def test_extract_non_global_tokens_skips_consumed_values_and_global_flags():
    tokens = reg.extract_non_global_tokens(
        ["--host", "prod", "--json", "vault", "list", "--debug-log"]
    )
    assert tokens == ["vault", "list"]


def test_register_external_commands_embedded_mode_registers_all(monkeypatch):
    fake_map = {
        "alpha": ("fake.alpha", "alpha_app"),
        "beta": ("fake.beta", "beta_app"),
    }

    monkeypatch.setattr(reg, "_EXTERNAL_CMD_MAP", fake_map)
    reg._clear_registration_cache()

    app = typer.Typer()
    alpha_app = typer.Typer()
    beta_app = typer.Typer()

    fake_modules = {
        "fake.alpha": types.SimpleNamespace(alpha_app=alpha_app),
        "fake.beta": types.SimpleNamespace(beta_app=beta_app),
    }

    def _fake_import(module_name: str):
        return fake_modules[module_name]

    monkeypatch.setattr("importlib.import_module", _fake_import)
    monkeypatch.setattr(reg.sys, "argv", ["pytest", "-k", "something"])

    reg._register_external_commands(target_app=app)

    registered_names = {group.name for group in app.registered_groups}
    assert {"alpha", "beta"}.issubset(registered_names)


def test_register_external_commands_registers_target_after_global_flags(monkeypatch):
    fake_map = {
        "alpha": ("fake.alpha", "alpha_app"),
        "beta": ("fake.beta", "beta_app"),
    }

    monkeypatch.setattr(reg, "_EXTERNAL_CMD_MAP", fake_map)
    reg._clear_registration_cache()

    app = typer.Typer()
    alpha_app = typer.Typer()
    beta_app = typer.Typer()

    fake_modules = {
        "fake.alpha": types.SimpleNamespace(alpha_app=alpha_app),
        "fake.beta": types.SimpleNamespace(beta_app=beta_app),
    }

    imported_modules: list[str] = []

    def _fake_import(module_name: str):
        imported_modules.append(module_name)
        return fake_modules[module_name]

    monkeypatch.setattr("importlib.import_module", _fake_import)
    monkeypatch.setattr(
        reg.sys,
        "argv",
        ["navig", "--host", "prod", "--json", "alpha", "run"],
    )

    reg._register_external_commands(target_app=app)

    registered_names = {group.name for group in app.registered_groups}
    assert "alpha" in registered_names
    assert "beta" not in registered_names
    assert imported_modules == ["fake.alpha"]
