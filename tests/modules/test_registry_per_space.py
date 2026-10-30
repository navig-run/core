"""Per-space module activation — the second enablement layer (PR 0).

Before this, plugin/module enablement was GLOBAL: a space's ``apps`` key only
filtered which tiles the sidebar showed, and the manifest docstring says so
("a view-filter over the globally-enabled modules — never a second enablement
store"). CLI verbs and agent tools were not space-scoped at all. This proves the
real per-space layer: a module can be ON in one space and OFF in another, and all
three surfaces honour it —

  * routes/tiles  → ``ModuleRegistry.is_enabled`` / ``list_modules``
  * agent tools   → ``disabled_surfaces("toolset")`` (the agent drops those toolsets)
  * CLI verbs     → ``cli.registration._space_disabled_module_ids``

Reverting any of the four edits (manifest accessor, ``is_enabled`` layering, the
``disabled_surfaces`` helper, or the CLI reader) reddens the matching case here.
Config/data dirs are session-isolated by the top-level conftest; the active space
is pinned per-test with ``NAVIG_SPACE``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import navig.modules.registry as reg_mod
from navig.modules.registry import ModuleDef, ModuleKind, ModuleRegistry
from navig.spaces.space_manifest import load_space_manifest


def _write_space(dir_: Path, **manifest) -> Path:
    """Create a space dir with a .navig/space.json carrying *manifest* keys."""
    nav = dir_ / ".navig"
    nav.mkdir(parents=True, exist_ok=True)
    import json

    (nav / "space.json").write_text(
        json.dumps({"id": dir_.name, **manifest}, indent=2) + "\n", encoding="utf-8"
    )
    return dir_


@pytest.fixture(autouse=True)
def _clear_space_cache():
    # The per-space override cache is keyed on manifest mtime; tests rewrite the
    # same path within one clock tick, so drop it around every case.
    reg_mod._SPACE_OVR_CACHE.clear()
    yield
    reg_mod._SPACE_OVR_CACHE.clear()


# ── 1. manifest accessor ─────────────────────────────────────────────────────
def test_manifest_overrides_parse_and_disable_wins(tmp_path):
    s = _write_space(
        tmp_path / "ws",
        modules_enabled=["devnet", "clash"],
        modules_disabled=["finance", "clash"],  # clash in both → OFF wins
    )
    ov = load_space_manifest(s).module_overrides
    assert ov == {"devnet": True, "finance": False, "clash": False}


def test_manifest_absent_fields_inherit(tmp_path):
    s = _write_space(tmp_path / "plain")  # no module fields
    assert load_space_manifest(s).module_overrides == {}


# ── 2. is_enabled layering (routes/tiles surface) ────────────────────────────
def test_is_enabled_space_overrides_global(tmp_path, monkeypatch):
    reg = ModuleRegistry().discover()
    # finance is a built-in, default_enabled=True; devops too. Pin a CLEAN active
    # space so the no-space_dir baseline is deterministic (otherwise it resolves
    # to whatever find_app_root finds in the test harness).
    monkeypatch.setenv("NAVIG_SPACE", str(_write_space(tmp_path / "clean")))
    reg_mod._SPACE_OVR_CACHE.clear()
    space = _write_space(tmp_path / "ws", modules_disabled=["finance"])
    assert reg.is_enabled("finance") is True  # active=clean → global default
    assert reg.is_enabled("finance", space_dir=space) is False  # space turns it off
    assert reg.is_enabled("goals", space_dir=space) is True  # untouched (default-on)


def test_is_enabled_space_can_enable_a_globally_off_module(tmp_path):
    reg = ModuleRegistry().discover()
    # Inject a module that is OFF by default (no global override set).
    reg._defs["lab"] = ModuleDef(
        id="lab", label="Lab", description="", kind=ModuleKind.PLUGIN,
        default_enabled=False,
    )
    space = _write_space(tmp_path / "ws", modules_enabled=["lab"])
    assert reg.is_enabled("lab") is False
    assert reg.is_enabled("lab", space_dir=space) is True


def test_list_modules_reflects_space(tmp_path):
    reg = ModuleRegistry().discover()
    space = _write_space(tmp_path / "ws", modules_disabled=["finance"])
    rows = {m["id"]: m for m in reg.list_modules(space_dir=space)}
    assert rows["finance"]["enabled"] is False


# ── active-space resolution via NAVIG_SPACE (no explicit space_dir) ──────────
def test_active_space_pin_drives_is_enabled(tmp_path, monkeypatch):
    reg = ModuleRegistry().discover()
    space = _write_space(tmp_path / "active", modules_disabled=["finance"])
    monkeypatch.setenv("NAVIG_SPACE", str(space))
    reg_mod._SPACE_OVR_CACHE.clear()
    assert reg.is_enabled("finance") is False  # resolved through the active space


# ── 3. agent-tool surface: disabled_surfaces("toolset") ──────────────────────
def test_disabled_toolsets_for_space_off_module(tmp_path):
    reg = ModuleRegistry().discover()
    reg._defs["devnet"] = ModuleDef(
        id="devnet", label="DevNet", description="", kind=ModuleKind.PLUGIN,
        default_enabled=True, surfaces=["toolset:devnet", "cli:net"],
    )
    space = _write_space(tmp_path / "ws", modules_disabled=["devnet"])
    # membership, not equality: the set also carries surfaces of modules that are
    # globally OFF by default (e.g. a default-disabled built-in's cli verb) — that
    # is correct, those are effectively-disabled too.
    assert "devnet" in reg.disabled_surfaces("toolset", space_dir=space)
    assert "net" in reg.disabled_surfaces("cli", space_dir=space)
    # devnet is default-ON, so when the space does NOT disable it, it drops out.
    other = _write_space(tmp_path / "other")
    assert "devnet" not in reg.disabled_surfaces("toolset", space_dir=other)


# ── 4. CLI surface: _space_disabled_module_ids (cheap, no plugin import) ──────
def test_cli_reader_returns_space_disabled_ids(tmp_path, monkeypatch):
    from navig.cli.registration import _space_disabled_module_ids

    space = _write_space(
        tmp_path / "ws", modules_enabled=["devnet"], modules_disabled=["github"]
    )
    monkeypatch.setenv("NAVIG_SPACE", str(space))
    assert _space_disabled_module_ids() == {"github"}  # only the OFF ids


def test_a_space_disabled_builtin_module_loses_its_cli_verb(monkeypatch):
    """`modules_disabled: [finance]` drops `navig finance` too, not only plugin verbs."""
    import typer

    import navig.cli.registration as reg_mod

    seen: list[str] = []
    monkeypatch.setattr(reg_mod, "_space_disabled_module_ids", lambda: {"finance", "space"})
    monkeypatch.setattr(reg_mod, "_try_register_one", lambda app, name, already: seen.append(name))
    monkeypatch.setattr(reg_mod, "_DISABLED_EP_CMDS", {})

    for target in ("finance", "space", "vault"):
        monkeypatch.setattr(reg_mod, "_resolve_cli_target_from_argv", lambda t=target: t)
        reg_mod._register_external_commands(target_app=typer.Typer())
    assert seen == ["space", "vault"], seen  # `space` is the way back: never gated
    assert reg_mod._DISABLED_EP_CMDS == {"finance": "space:finance"}

    seen.clear()
    monkeypatch.setattr(reg_mod, "_resolve_cli_target_from_argv", lambda: None)
    monkeypatch.setattr(reg_mod, "_entry_point_commands", lambda: {})
    for name in ("_try_register_wire", "_try_register_apply", "_try_register_undo", "_try_register_ahk"):
        monkeypatch.setattr(reg_mod, name, lambda *a, **k: None)
    reg_mod._register_external_commands(target_app=typer.Typer())
    assert "finance" not in seen and "vault" in seen


# ── writer round-trip: set_enabled_in_space ──────────────────────────────────
def test_set_enabled_in_space_roundtrip(tmp_path):
    reg = ModuleRegistry().discover()
    space = _write_space(tmp_path / "ws")

    assert reg.set_enabled_in_space("finance", False, space_dir=space) is True
    reg_mod._SPACE_OVR_CACHE.clear()
    assert reg.is_enabled("finance", space_dir=space) is False

    assert reg.set_enabled_in_space("finance", True, space_dir=space) is True
    reg_mod._SPACE_OVR_CACHE.clear()
    assert reg.is_enabled("finance", space_dir=space) is True
    # not lingering in the disabled list
    assert "finance" not in load_space_manifest(space)._id_list("modules_disabled")

    assert reg.set_enabled_in_space("finance", None, space_dir=space) is True
    reg_mod._SPACE_OVR_CACHE.clear()
    assert load_space_manifest(space).module_overrides == {}  # inherits global again
    assert reg.set_enabled_in_space("no-such-module", True, space_dir=space) is False
