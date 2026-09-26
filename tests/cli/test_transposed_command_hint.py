"""``navig init space`` → *Did you mean: navig space init*.

The existing "did you mean" fires only for an UNKNOWN first token — by design, so
``navig github --badflag`` cannot masquerade as a missing plugin. But the most
common way to get a real command's usage error is to transpose ``<command>
<group>``, and that produced a bare *"Got unexpected extra argument (space)"* with
no way forward.

⚠ The trap that made the first cut a no-op: ``_register_external_commands`` is
argv-driven. For ``navig init space`` the target is ``init``, an inline command, so
it returns without importing anything — and ``space`` is simply NOT on the app at
the moment the error is being explained. A resolver that only walks what is already
registered says "no such path" for exactly the case it exists to catch. The hint has
to register the candidate group itself (``ensure_group_registered``, one module
import). The subprocess test at the bottom is the one that proves that end to end;
the unit tests above it would pass with the lazy-registration bug intact.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import typer

import navig.main as main_mod
from navig.cli import registration as reg

ROOT = Path(__file__).resolve().parent.parent.parent


# ── a small real Typer tree: `demo` is a group with `init`; `init` is a flat command ──


def _tree() -> typer.Typer:
    app = typer.Typer()
    demo = typer.Typer()

    @demo.command("init")
    def _demo_init() -> None:  # pragma: no cover - never invoked
        pass

    @demo.command("list")
    def _demo_list() -> None:  # pragma: no cover
        pass

    app.add_typer(demo, name="demo")

    @app.command("init")
    def _init() -> None:  # pragma: no cover
        pass

    @app.command("version")
    def _version() -> None:  # pragma: no cover
        pass

    return app


@pytest.fixture
def app() -> typer.Typer:
    return _tree()


@pytest.fixture
def printed(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(main_mod, "_eprint", lambda s: lines.append(str(s)))
    return lines


# ── the resolver ─────────────────────────────────────────────────────────────


def test_resolves_a_group_command_path(app) -> None:
    assert main_mod._resolves_as_command_path(app, ["demo", "init"]) is True
    assert main_mod._resolves_as_command_path(app, ["demo", "list"]) is True


def test_a_flat_command_is_not_a_group(app) -> None:
    """`init demo` is not a path: `init` has no subcommands."""
    assert main_mod._resolves_as_command_path(app, ["init", "demo"]) is False


def test_a_group_alone_is_not_a_command_path(app) -> None:
    """The last token must be a COMMAND — `navig demo` is a group, not a path."""
    assert main_mod._resolves_as_command_path(app, ["demo"]) is False


def test_unknown_tokens_never_resolve(app) -> None:
    assert main_mod._resolves_as_command_path(app, ["nope", "init"]) is False
    assert main_mod._resolves_as_command_path(app, ["demo", "nope"]) is False
    assert main_mod._resolves_as_command_path(app, []) is False
    assert main_mod._resolves_as_command_path(None, ["demo", "init"]) is False


def test_is_registered_command_still_sees_groups_and_commands(app) -> None:
    """The refactor onto the shared helpers must not change the older predicate."""
    assert main_mod._is_registered_command(app, "demo") is True
    assert main_mod._is_registered_command(app, "init") is True
    assert main_mod._is_registered_command(app, "nope") is False
    assert main_mod._is_registered_command(None, "demo") is False


# ── the suggester ────────────────────────────────────────────────────────────


def test_transposed_tokens_produce_the_hint(app, printed) -> None:
    assert main_mod._suggest_transposed_command(app, ["init", "demo"]) is True
    assert any("navig demo init" in line for line in printed)


def test_trailing_positionals_are_carried_across(app, printed) -> None:
    assert main_mod._suggest_transposed_command(app, ["init", "demo", "myproj"]) is True
    assert any("navig demo init myproj" in line for line in printed)


def test_a_flag_is_never_transposed(app, printed) -> None:
    """`navig init --badflag` is a bad flag, not a transposition."""
    assert main_mod._suggest_transposed_command(app, ["init", "--badflag"]) is False
    assert main_mod._suggest_transposed_command(app, ["--x", "demo"]) is False
    assert printed == []


def test_no_hint_when_the_swap_is_not_a_real_path(app, printed) -> None:
    """`navig version extra` → `extra version` names nothing; stay silent."""
    assert main_mod._suggest_transposed_command(app, ["version", "extra"]) is False
    assert main_mod._suggest_transposed_command(app, ["init"]) is False
    assert printed == []


def test_a_typo_inside_a_group_is_not_a_transposition(app, printed) -> None:
    """`navig demo innit` → swap is `innit demo`; `innit` is no group. Click's own
    'No such command' hint handles that case; this one must not add noise."""
    assert main_mod._suggest_transposed_command(app, ["demo", "innit"]) is False
    assert printed == []


# ── on-demand registration of the candidate group ────────────────────────────


def test_ensure_group_registered_imports_a_known_external_group(monkeypatch) -> None:
    """The half that makes the hint work in the real CLI: `space` is in
    `_EXTERNAL_CMD_MAP` but not on a fresh app, and becomes present on request."""
    app = typer.Typer()
    assert main_mod._subgroup_named(app, "space") is None
    assert reg.ensure_group_registered(app, "space") is True
    assert main_mod._resolves_as_command_path(app, ["space", "init"]) is True


def test_ensure_group_registered_is_idempotent(monkeypatch) -> None:
    app = typer.Typer()
    assert reg.ensure_group_registered(app, "space") is True
    before = len(app.registered_groups)
    assert reg.ensure_group_registered(app, "space") is True
    assert len(app.registered_groups) == before


def test_ensure_group_registered_rejects_an_unknown_name() -> None:
    app = typer.Typer()
    assert reg.ensure_group_registered(app, "definitely-not-a-command") is False
    assert app.registered_groups == []


def test_the_suggester_registers_the_missing_group_itself(monkeypatch, printed) -> None:
    """Mirror of the real failure: `space` absent from the app at hint time."""
    app = typer.Typer()

    @app.command("init")
    def _init() -> None:  # pragma: no cover
        pass

    assert main_mod._subgroup_named(app, "space") is None
    assert main_mod._suggest_transposed_command(app, ["init", "space"]) is True
    assert any("navig space init" in line for line in printed)


# ── end to end, through the real entry point and its lazy registration ───────


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / ".navig")
    env["NAVIG_DATA_DIR"] = str(tmp_path / ".navig" / "data")
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    env["NAVIG_LAUNCHER"] = "fuzzy"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


@pytest.mark.integration
def test_navig_init_space_gets_the_hint_end_to_end(tmp_path: Path) -> None:
    """The operator's exact keystrokes, through main.py's argv-driven registration.

    This is the test the unit tests cannot stand in for: with the lazy-registration
    bug intact every unit test above passes, and this one prints no hint.
    """
    r = _run(["init", "space"], tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out  # still a usage error — the hint does not "fix" it
    assert "Did you mean?" in out, out
    assert "navig space init" in out, out


@pytest.mark.integration
def test_a_bad_flag_on_a_real_command_gets_no_hint_end_to_end(tmp_path: Path) -> None:
    r = _run(["init", "--definitely-not-a-flag"], tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "Did you mean?" not in out, out
