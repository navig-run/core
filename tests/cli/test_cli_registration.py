from __future__ import annotations

import types

import pytest
import typer

import navig.cli.registration as reg

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


# ── plugin twins: hide the OLD name only while the NEW one is registered ──────
# `navig pipeline` is `navig content` (ADR-0002). Hiding it unconditionally would
# strand an install whose navig-pipeline predates the `content` entry point: the
# twin would be gone from `--help` and the new name would not exist either.
def test_a_plugin_twin_is_hidden_only_while_its_replacement_is_registered():
    ep = types.SimpleNamespace(name="ep")
    assert reg._ep_hidden("pipeline", {"pipeline": ep, "content": ep}) is True
    assert reg._ep_hidden("pipeline", {"pipeline": ep}) is False  # old install: keep it
    assert reg._ep_hidden("content", {"content": ep}) is False


def test_the_static_hidden_sets_still_win_and_plain_verbs_stay_visible():
    ep = types.SimpleNamespace(name="ep")
    assert reg._ep_hidden("media", {}) is True       # static alias — hidden regardless
    assert reg._ep_hidden("skills", {"skills": ep}) is True
    assert reg._ep_hidden("audio", {"audio": ep}) is False


def test_the_shipped_manifest_publishes_content_and_not_pipeline():
    """The hide must reach the surface operators read: completions, `navig help`, docs."""
    import json
    from pathlib import Path

    generated = Path(__file__).resolve().parents[2] / "generated" / "commands.json"
    paths = {c["path"] for c in json.loads(generated.read_text(encoding="utf-8"))["commands"]}
    assert any(p.startswith("navig content ") for p in paths)
    assert not any(p.startswith("navig pipeline") for p in paths)
