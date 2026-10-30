"""`navig --dry-run contribute scan` forked, cloned and synced anyway.

`--dry-run` is a GLOBAL flag delivered in ``ctx.obj``; ``scan`` only read its own
subcommand-local ``--dry-run`` option, so the root flag was ignored and the real
fork / clone / sync / branch path ran.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from navig.commands import contribute
from navig.selfheal import git_manager, pr_builder, scanner


def _wire(monkeypatch, tmp_path) -> list[str]:
    calls: list[str] = []

    def _side_effect(name: str):
        def _fn(*_a, **_k):
            calls.append(name)
            raise RuntimeError(f"{name} must not run under --dry-run")

        return _fn

    monkeypatch.setattr(contribute, "_get_config", lambda: {"enabled": True})
    monkeypatch.setattr(pr_builder, "_resolve_token", lambda _cfg: "tok")
    monkeypatch.setattr(git_manager, "get_github_username", lambda _tok: "someone")
    monkeypatch.setattr(git_manager, "core_repo_dir", lambda: tmp_path)
    for name in ("fork_repo", "clone_or_update", "sync_fork", "create_branch"):
        monkeypatch.setattr(git_manager, name, _side_effect(name))
    monkeypatch.setattr(scanner, "scan_files", lambda *_a, **_k: [])
    return calls


def _root() -> typer.Typer:
    app = typer.Typer()

    @app.callback()
    def _cb(ctx: typer.Context, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
        ctx.obj = {"dry_run": dry_run}

    app.add_typer(contribute.contribute_app, name="contribute")
    return app


def test_global_dry_run_skips_fork_and_sync(monkeypatch, tmp_path) -> None:
    calls = _wire(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        _root(), ["--dry-run", "contribute", "scan", "--path", str(tmp_path)]
    )

    assert calls == [], result.output
    assert result.exit_code == 0, result.output


def test_without_dry_run_the_real_path_still_runs(monkeypatch, tmp_path) -> None:
    calls = _wire(monkeypatch, tmp_path)

    result = CliRunner().invoke(_root(), ["contribute", "scan", "--path", str(tmp_path)])

    assert calls == ["fork_repo"]
    assert result.exit_code == 1


def test_dry_run_needs_no_github_token(monkeypatch, tmp_path) -> None:
    """A local `scan --dry-run --path X` neither forks nor submits — no PAT required."""
    _wire(monkeypatch, tmp_path)

    def _no_token(_cfg):
        raise ValueError("no GitHub token configured")

    monkeypatch.setattr(pr_builder, "_resolve_token", _no_token)

    result = CliRunner().invoke(
        _root(), ["contribute", "scan", "--dry-run", "--path", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert "GitHub token error" not in result.output
