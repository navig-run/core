"""Representative commands from the typed-path sweep, driven for real.

The shape is pinned tree-wide by ``tests/quality/test_typed_paths_are_resolved.py``; these
drive actual commands from a directory that is NOT the process cwd (``main.py`` has
chdir'd into the active space), covering a write (``tools schema -o``), the two ``cdp``
outputs the static guard cannot see, and the plugin shim's fallback against a core that
predates ``resolve_user_path``.
"""

from __future__ import annotations

import builtins
import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.fixtures.module_eviction import evicted_modules

_runner = CliRunner()


@pytest.fixture
def standing_elsewhere(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    standing = tmp_path / "here"
    space = tmp_path / "active-space"
    standing.mkdir()
    space.mkdir()
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(standing))
    monkeypatch.chdir(space)
    return standing.resolve(), space.resolve()


def test_tools_schema_output_lands_where_typed(standing_elsewhere, monkeypatch) -> None:
    from navig.commands import tools as tools_cmd

    class _Registry:
        def to_openapi_schema(self, available_only: bool = True) -> dict:
            return {"openapi": "3.0.0"}

    monkeypatch.setattr(tools_cmd, "_get_registry", lambda: _Registry())
    standing, space = standing_elsewhere
    result = _runner.invoke(tools_cmd.tools_app, ["schema", "-o", "schema.json"])
    assert result.exit_code == 0, result.output
    assert (standing / "schema.json").is_file()
    assert not (space / "schema.json").exists()


def test_cdp_record_output_lands_where_typed(standing_elsewhere, monkeypatch) -> None:
    from navig.browser import cdp_actions
    from navig.commands import cdp as cdp_cmd

    seen: dict = {}

    async def _record(port, out=None, **kw):
        seen["out"] = out
        return {"ok": True, "path": out}

    monkeypatch.setattr(cdp_actions, "record", _record)
    monkeypatch.setattr(cdp_cmd, "_resolve_port", lambda _a, p: p)
    standing, _ = standing_elsewhere
    result = _runner.invoke(cdp_cmd.cdp_app, ["record", "-o", "clip.mp4", "--secs", "1"])
    assert result.exit_code == 0, result.output
    assert Path(seen["out"]) == standing / "clip.mp4"


@pytest.mark.parametrize(
    ("typed", "expect_resolved"),
    [("shot.png", False), ("out/shot.png", True)],
)
def test_cdp_screenshot_bare_name_keeps_home_path_resolves_here(
    standing_elsewhere, monkeypatch, typed: str, expect_resolved: bool
) -> None:
    from navig.browser import cdp_actions
    from navig.commands import cdp as cdp_cmd

    seen: dict = {}

    async def _shot(port, out=None, **kw):
        seen["out"] = out
        return {"ok": True, "via": "cdp", "path": out}

    monkeypatch.setattr(cdp_actions, "screenshot", _shot)
    monkeypatch.setattr(cdp_cmd, "_resolve_port", lambda _a, p: p)
    standing, _ = standing_elsewhere
    result = _runner.invoke(cdp_cmd.cdp_app, ["screenshot", "-o", typed])
    assert result.exit_code == 0, result.output
    if expect_resolved:
        assert Path(seen["out"]) == standing / "out" / "shot.png"
    else:
        # a bare name keeps its documented home (~/.navig/screenshots)
        assert seen["out"] == "shot.png"


def test_plugin_shim_works_against_a_core_without_the_helper(standing_elsewhere, monkeypatch) -> None:
    pytest.importorskip("navig_explore")
    real_import = builtins.__import__

    def _no_helper(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "navig.platform.paths" and fromlist and "resolve_user_path" in fromlist:
            raise ImportError("simulated navig core 3.25.0 (published before the helper)")
        return real_import(name, globals, locals, fromlist, level)

    standing, _ = standing_elsewhere
    with evicted_modules(monkeypatch, ("navig_explore._typed_path",)):
        monkeypatch.setattr(builtins, "__import__", _no_helper)
        shim = importlib.import_module("navig_explore._typed_path")
        monkeypatch.setattr(builtins, "__import__", real_import)
        assert shim.resolve_user_path.__module__ == "navig_explore._typed_path", "fallback not taken"
        assert shim.resolve_user_path("photos") == standing / "photos"
