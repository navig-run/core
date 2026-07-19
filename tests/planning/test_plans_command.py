"""`navig plans` CLI.

Every test here runs in ``temp_dir``, NOT ``tmp_path`` — and that is load-bearing, not
style. ``pytest.ini`` pins ``--basetemp=.pytest_tmp``, which lives *inside* ``core/``, so
every ``tmp_path`` sits under NAVIG's own checkout. Space discovery walks UP from the
given path looking for a project ``.navig/`` (``spaces/resolver.py``) and duly finds
``core/.navig`` — so a plans command invoked under ``tmp_path`` discovers a real
project-scope space called ``core``, belonging to the repo we are testing.

That made ``test_plans_status_no_spaces`` / ``test_plans_summary_no_spaces`` assert the
impossible ("no spaces discovered" while standing inside a NAVIG project), and left the
``next`` / ``briefing`` tests one repo edit away from ranking that stray space first.
``temp_dir`` is the system temp dir — no ``.navig`` ancestor, no ``.git`` ancestor — and
is the sanctioned fixture for anything that walks upward. Both halves of the trap are
pinned in ``tests/core/test_tmp_dir_repo_boundary.py``.
"""

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.plans import plans_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _set_global_config_dir(monkeypatch, root: Path) -> Path:
    config_dir = root / ".navig-global"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config_dir))
    return config_dir


def test_plans_status_no_spaces(temp_dir, monkeypatch):
    _set_global_config_dir(monkeypatch, temp_dir)

    result = runner.invoke(plans_app, ["status", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "No spaces discovered" in result.stdout


def test_plans_status_renders_rows(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    global_space = config_dir / "spaces" / "finance"
    global_space.mkdir(parents=True, exist_ok=True)
    (global_space / "VISION.md").write_text("---\ngoal: Save emergency fund\n---\n")
    (global_space / "CURRENT_PHASE.md").write_text("---\ncompletion_pct: 35\n---\n")

    result = runner.invoke(plans_app, ["status", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "finance" in result.stdout
    assert "35.0%" in result.stdout


def test_plans_add_creates_entry(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".navig").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        plans_app,
        [
            "add",
            "Ship onboarding wizard",
            "--space",
            "finance",
            "--path",
            str(repo),
        ],
    )
    assert result.exit_code == 0
    assert "Added goal" in result.stdout

    dev_plan = repo / ".navig" / "plans" / "DEV_PLAN.md"
    assert dev_plan.exists()
    content = dev_plan.read_text(encoding="utf-8")
    assert "- [ ] [finance] Ship onboarding wizard" in content


def test_plans_run_alias_routes_to_add(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".navig").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        plans_app,
        [
            "run",
            "Improve docs",
            "--space",
            "finance",
            "--path",
            str(repo),
        ],
    )
    assert result.exit_code == 0
    assert "deprecated" in result.stdout.lower()

    dev_plan = repo / ".navig" / "plans" / "DEV_PLAN.md"
    assert dev_plan.exists()
    assert "- [ ] [finance] Improve docs" in dev_plan.read_text(encoding="utf-8")


def test_plans_sync_no_inbox(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    (repo / ".navig" / "plans").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(plans_app, ["sync", "--path", str(repo)])
    assert result.exit_code == 0
    assert "No inbox files found" in result.stdout


def test_plans_sync_dry_run_keeps_inbox_file(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    inbox = repo / ".navig" / "plans" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / "raw-roadmap.md"
    source.write_text(
        "# Launch Roadmap\n\nMilestone 1\nMilestone 2\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        plans_app,
        ["sync", "--no-llm", "--dry-run", "--path", str(repo)],
    )
    assert result.exit_code == 0
    assert "Dry-run summary: 1 previews" in result.stdout
    assert source.exists()
    assert not (inbox / ".processed").exists()


def test_plans_sync_writes_and_moves_source(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    inbox = repo / ".navig" / "plans" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / "raw-roadmap.md"
    source.write_text(
        "# Launch Roadmap\n\nMilestone 1\nMilestone 2\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        plans_app,
        ["sync", "--no-llm", "--space", "health", "--path", str(repo)],
    )
    assert result.exit_code == 0
    assert "Sync summary: 1 routed" in result.stdout

    routed = [
        path
        for path in (repo / ".navig" / "plans").glob("*.md")
        if path.name not in {"DEV_PLAN.md", "CURRENT_PHASE.md"}
    ]
    assert len(routed) == 1
    content = routed[0].read_text(encoding="utf-8")
    assert "space: health" in content

    moved_source = inbox / ".processed" / "raw-roadmap.md"
    assert moved_source.exists()
    assert not source.exists()


def test_plans_update_writes_frontmatter_completion(temp_dir, monkeypatch):
    repo = temp_dir / "repo"
    plans_dir = repo / ".navig" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    target = plans_dir / "CURRENT_PHASE.md"
    target.write_text(
        "# Current Phase\n\n- [x] Task A\n- [ ] Task B\n- [x] Task C\n", encoding="utf-8"
    )

    result = runner.invoke(plans_app, ["update", "CURRENT_PHASE.md", "--path", str(repo)])
    assert result.exit_code == 0
    assert "completion_pct=66.7%" in result.stdout

    content = target.read_text(encoding="utf-8")
    assert "completion_pct: 66.7" in content
    assert f"last_updated: {datetime.now().strftime('%Y-%m-%d')}" in content


def test_plans_next_selects_lowest_progress_with_pending_task(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    low = config_dir / "spaces" / "health"
    low.mkdir(parents=True, exist_ok=True)
    (low / "VISION.md").write_text("---\ngoal: Restore energy\n---\n", encoding="utf-8")
    (low / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 20\n---\n\n- [ ] Sleep 8h tonight\n",
        encoding="utf-8",
    )

    high = config_dir / "spaces" / "finance"
    high.mkdir(parents=True, exist_ok=True)
    (high / "VISION.md").write_text("---\ngoal: Save monthly\n---\n", encoding="utf-8")
    (high / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 80\n---\n\n- [ ] Review subscriptions\n",
        encoding="utf-8",
    )

    result = runner.invoke(plans_app, ["next", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "Space: health" in result.stdout
    assert "Sleep 8h tonight" in result.stdout


def test_plans_briefing_includes_action_focus(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    health = config_dir / "spaces" / "health"
    health.mkdir(parents=True, exist_ok=True)
    (health / "VISION.md").write_text("---\ngoal: Restore energy\n---\n", encoding="utf-8")
    (health / "CURRENT_PHASE.md").write_text(
        "---\ncompletion_pct: 10\n---\n\n- [ ] Sleep by 10pm\n",
        encoding="utf-8",
    )

    result = runner.invoke(plans_app, ["briefing", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "Spaces Progress:" in result.stdout
    assert "Action Focus:" in result.stdout
    assert "Sleep by 10pm" in result.stdout


# ─────────────────────────────────────────────────────────────
# plans summary
# ─────────────────────────────────────────────────────────────


def test_plans_summary_no_spaces(temp_dir, monkeypatch):
    _set_global_config_dir(monkeypatch, temp_dir)

    result = runner.invoke(plans_app, ["summary", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "No spaces discovered" in result.stdout


def test_plans_summary_single_space(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    space = config_dir / "spaces" / "devops"
    space.mkdir(parents=True, exist_ok=True)
    (space / "CURRENT_PHASE.md").write_text(
        "---\ntitle: CI Pipeline\nphase: 1\nstatus: active\ncompletion_pct: 60\n---\n\n# CI Pipeline\n",
        encoding="utf-8",
    )

    result = runner.invoke(plans_app, ["summary", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "CI Pipeline" in result.stdout


def test_plans_summary_missing_phase_shows_dash_row(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    good = config_dir / "spaces" / "finance"
    good.mkdir(parents=True, exist_ok=True)
    (good / "CURRENT_PHASE.md").write_text(
        "---\nstatus: active\ncompletion_pct: 30\nlast_updated: 2025-07-01\n---\n\n# Budget",
        encoding="utf-8",
    )

    missing = config_dir / "spaces" / "empty"
    missing.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(plans_app, ["summary", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "finance" in result.stdout
    assert "empty" in result.stdout
    assert "—" in result.stdout


def test_plans_summary_all_spaces_table(temp_dir, monkeypatch):
    config_dir = _set_global_config_dir(monkeypatch, temp_dir)

    for sname in ("alpha", "beta"):
        space = config_dir / "spaces" / sname
        space.mkdir(parents=True, exist_ok=True)
        (space / "CURRENT_PHASE.md").write_text(
            f"---\nstatus: active\ncompletion_pct: 50\nlast_updated: 2025-07-0{1 if sname == 'alpha' else 2}\n---\n\n# {sname.title()} Phase\n",
            encoding="utf-8",
        )

    result = runner.invoke(plans_app, ["summary", "--path", str(temp_dir / "repo")])
    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout
