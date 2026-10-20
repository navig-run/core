"""Phase titles (measured shapes), the CLI over the same model, and store isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.projects.model import phase_title


@pytest.mark.parametrize(("text", "expected"), [
    ("# Current Phase: Foundation\n**Phase**: 1 — Space Foundation\n", "Foundation"),
    ("# Current Phase: v2.0.0 Complete — Operational\n", "v2.0.0 Complete — Operational"),
    ("# Research Space — Current Phase\n## Phase: Pipeline Bring-Up\n", "Pipeline Bring-Up"),
    ("# Current Phase\n> Last updated: x\n## Active Goal\nBootstrap the android space\n", "Bootstrap the android space"),
    ("# CURRENT_PHASE.md\n## ✅ Session Addendum — 2026-10-08 (in-app browser)\nbody\n",
     "Session Addendum — 2026-10-08 (in-app browser)"),
    ("# Current Phase\n> What are you working on right now? Navig reads this first.\n", ""),
    ("# Payments rollout\n", "Payments rollout"),
])
def test_phase_title_never_returns_the_boilerplate(text: str, expected: str) -> None:
    assert phase_title(text) == expected


def test_frontmatter_title_wins() -> None:
    assert phase_title("# Current Phase\n", {"title": "Plan Engine"}) == "Plan Engine"


def test_cli_list_show_scripts_and_context_json(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from navig.commands.project import project_app
    from navig.projects.model import add_project

    pid = add_project(project)["id"]
    runner = CliRunner()

    res = runner.invoke(project_app, ["list", "--json"])
    assert res.exit_code == 0, res.output
    assert any(p["id"] == pid for p in json.loads(res.output)["projects"])

    res = runner.invoke(project_app, ["scripts", pid, "--json"])
    assert res.exit_code == 0 and json.loads(res.output)["count"] == 7

    res = runner.invoke(project_app, ["context", pid, "--json"])
    assert res.exit_code == 0
    assert "Sell things fast." in json.loads(res.output)["markdown"]

    res = runner.invoke(project_app, ["todos", pid, "--json"])
    assert res.exit_code == 0 and json.loads(res.output)["counts"]["open"] >= 3

    res = runner.invoke(project_app, ["show", "nope"])
    assert res.exit_code == 1  # an unknown project is a failure, not a quiet empty view


def test_cli_scan_does_not_write_without_add(isolated: Path, project: Path) -> None:
    from typer.testing import CliRunner

    from navig.commands.project import project_app

    bare = isolated / "apps" / "tool"
    bare.mkdir()
    (bare / "package.json").write_text("{}", encoding="utf-8")
    res = CliRunner().invoke(project_app, ["scan", str(isolated / "apps"), "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert {c["name"] for c in data["candidates"]} == {"shop", "tool"}
    assert data["added"] == [] and not (bare / ".navig").exists()


def test_the_project_store_honours_navig_data_dir(isolated: Path) -> None:
    from navig.projects.store import _default_db_path, get_project_store

    assert _default_db_path() == isolated / "data" / "store" / "projects.db"
    get_project_store().set_prefs(isolated, {"pinned": True})
    assert (isolated / "data" / "store" / "projects.db").exists()
