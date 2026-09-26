"""Shipped code must not `import click` — Typer 0.27 vendors it and installs no `click`.

Measured 2026-09-14 on a clean venv: `pip install -e core` resolves Typer 0.27.2, whose
`requires_dist` no longer lists click, and `import click` raises ModuleNotFoundError.
The maintainer's box never saw it because `requirements.lock` pins Typer 0.24 + click.

What it broke, by site:
  * navig/operation_recorder.py — `claim_cli_operation` swallowed the ImportError and
    returned "no middleware record", so EVERY enriched command (`config set`, `host use`,
    `env set`) wrote two ledger lines: the command's own (with undo_data) and the
    middleware's. Seen as `15 chained operations` for 14 commands in a recording.
  * navig/commands/init.py — `raise click.exceptions.Exit(1)` would itself raise
    NameError on the abort path.
  * navig/onboarding/engine.py — Ctrl+C at a wizard prompt stopped being recognised as
    an abort.
  * plugins/navig-github — `navig github …` crashed on import.

Use `typer.Exit` / `typer.Abort`, or `navig.core.click_compat` for anything else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import typer

REPO = Path(__file__).resolve().parents[3]
CORE_PKG = REPO / "core" / "navig"
PLUGIN_PKGS = sorted(p for p in (REPO / "plugins").glob("navig-*/navig_*") if p.is_dir())


def _click_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:  # pragma: no cover - another guard's job
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "click" or a.name.startswith("click.") for a in node.names):
                hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and (mod == "click" or mod.startswith("click.")):
                hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return hits


def _scan(roots: list[Path]) -> list[str]:
    hits: list[str] = []
    files = 0
    for root in roots:
        for py in sorted(root.rglob("*.py")):
            files += 1
            hits += _click_imports(py)
    assert files > 100, f"scan floor: only {files} files seen under {roots}"
    return hits


def test_core_never_imports_click_directly() -> None:
    hits = _scan([CORE_PKG])
    assert not hits, (
        "These import `click` directly; Typer 0.27 ships no `click` package, so they fail "
        "on a fresh install. Use typer.Exit/typer.Abort or navig.core.click_compat:\n  "
        + "\n  ".join(hits)
    )


def test_plugins_never_import_click_directly() -> None:
    assert PLUGIN_PKGS, "no plugin packages found — the scan would be vacuous"
    hits = _scan(PLUGIN_PKGS)
    assert not hits, "\n  ".join(["plugin code imports `click` directly:", *hits])


def test_the_detector_sees_both_import_shapes(tmp_path: Path) -> None:
    p = tmp_path / "probe.py"
    p.write_text("import click\nfrom click.exceptions import Exit\nimport typer\n", encoding="utf-8")
    assert len(_click_imports(p)) == 2
    p.write_text("import typer\nfrom navig.core.click_compat import click_module\n", encoding="utf-8")
    assert _click_imports(p) == []


def test_click_compat_resolves_the_click_typer_runs_on() -> None:
    from navig.core.click_compat import click_module, click_submodule, get_current_context

    mod = click_module()
    assert typer.Context.__mro__[1] is click_submodule("core").Context
    assert mod.__name__ in {"click", "typer._click"}
    assert hasattr(click_submodule("exceptions"), "ClickException")
    assert get_current_context(silent=True) is None  # no command is running here


def test_claim_cli_operation_finds_the_record_inside_a_typer_command() -> None:
    """The user-visible consequence: exactly one ledger record per enriched command."""
    from typer.testing import CliRunner

    from navig.operation_recorder import OperationRecord, claim_cli_operation

    app = typer.Typer()
    seen: dict[str, object] = {}

    @app.callback()
    def _root(ctx: typer.Context) -> None:
        ctx.ensure_object(dict)
        ctx.obj["_operation_record"] = OperationRecord(
            id="op-test", timestamp="t", command="navig config set a b", operation_type="config_change"
        )

    @app.command()
    def set_it(ctx: typer.Context) -> None:
        record, _start = claim_cli_operation(match=("config set",))
        seen["claimed"] = record is not None
        seen["detached"] = "_operation_record" not in ctx.obj

    result = CliRunner().invoke(app, ["set-it"])
    assert result.exit_code == 0, result.output
    assert seen == {"claimed": True, "detached": True}


@pytest.mark.parametrize("name", ["Exit", "Abort"])
def test_typer_exposes_the_exceptions_we_rely_on(name: str) -> None:
    assert hasattr(typer, name)
