"""A `--dry-run` deploy must not report work it did not do.

`RollbackManager.create_snapshot()` deliberately returns a record on a dry run
DESCRIBING the snapshot it would take, without touching the remote — that contract is
pinned by `test_snapshot_dry_run_returns_record` and `test_dry_run_snapshot_no_remote_call`.
The record is the plan, not a result.

`_phase_backup` then reported it as `Snapshot → <path>`, telling the operator a snapshot
exists at a path that does not. It was the one phase of seven whose dry-run output
claimed completed work: `_phase_push` emits `[DRY RUN] rsync …` and `_phase_apply` logs
`[DRY RUN] apply: …`.

The parameter was already there and simply unread — which is what a scan for
"functions that accept `dry_run` and never look at it" surfaced.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from navig.deploy.engine import DeployEngine
from navig.deploy.models import (
    ApplyConfig,
    BackupConfig,
    DeployConfig,
    DeployPhase,
    HealthConfig,
    PushConfig,
    RestartConfig,
)

SERVER = {"name": "prod", "user": "deploy", "host": "10.0.0.10", "port": 22}


def _engine(tmp_path: Path) -> DeployEngine:
    cfg = DeployConfig()
    cfg.push = PushConfig(source="./dist/", target="/var/www/app/")
    cfg.backup = BackupConfig(enabled=True)
    cfg.apply = ApplyConfig(commands=[])
    cfg.restart = RestartConfig(adapter="systemd", service="app")
    cfg.health = HealthConfig(url=None)
    cfg.app = "testapp"
    return DeployEngine(
        config=cfg,
        server_config=SERVER,
        remote_ops=MagicMock(),
        cache_dir=tmp_path,
        project_root=Path("/tmp"),
    )


def _run_backup_phase(tmp_path: Path, *, dry_run: bool):
    """Drive `_phase_backup` with a live RollbackManager, capturing what it emits."""
    from navig.deploy.rollback import RollbackManager

    engine = _engine(tmp_path)
    remote = MagicMock()
    # A bare MagicMock returns a MagicMock `returncode`, which is not 0 — the real
    # backup path would fail for the wrong reason and the non-dry-run test would prove
    # nothing. Mirrors `_ok_proc` in the sibling suites.
    remote.execute_command.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine._rollback_mgr = RollbackManager(
        backup_cfg=BackupConfig(enabled=True),
        deploy_target="/var/www/app",
        app_name="testapp",
        server_config=SERVER,
        remote_ops=remote,
        cache_dir=tmp_path,
        dry_run=dry_run,
    )

    emitted: list[tuple] = []
    result = engine._phase_backup(
        lambda phase, state, msg: emitted.append((phase, state, msg)), dry_run
    )
    return result, emitted, remote


def test_a_dry_run_backup_does_not_claim_a_snapshot_exists(tmp_path: Path):
    result, emitted, remote = _run_backup_phase(tmp_path, dry_run=True)

    remote.execute_command.assert_not_called()  # premise: nothing was actually copied
    assert result.success is True
    assert "[DRY RUN]" in result.message, (
        "the operator was told a snapshot exists at a path that was never created"
    )

    ok_lines = [msg for phase, state, msg in emitted if state == "ok"]
    assert ok_lines and "[DRY RUN]" in ok_lines[0]
    assert DeployPhase.BACKUP in {phase for phase, _s, _m in emitted}


def test_a_real_backup_still_reports_the_snapshot_plainly(tmp_path: Path):
    """The success path must not grow a marker it should not have."""
    result, _emitted, _remote = _run_backup_phase(tmp_path, dry_run=False)

    assert result.success is True
    assert "[DRY RUN]" not in result.message
    assert "Snapshot" in result.message


def test_the_dry_run_message_still_names_the_planned_path(tmp_path: Path):
    """Marking it must not cost the operator the information — the path is the whole
    point of previewing a backup."""
    result, _emitted, _remote = _run_backup_phase(tmp_path, dry_run=True)

    assert "/var/backups/testapp" in result.message or "testapp" in result.message


def test_a_disabled_backup_is_unchanged_in_both_modes(tmp_path: Path):
    """`backup.enabled=false` returns no record at all; that branch has no snapshot to
    describe and must read the same either way."""
    from navig.deploy.rollback import RollbackManager

    for dry_run in (True, False):
        engine = _engine(tmp_path)
        engine._rollback_mgr = RollbackManager(
            backup_cfg=BackupConfig(enabled=False),
            deploy_target="/var/www/app",
            app_name="testapp",
            server_config=SERVER,
            remote_ops=MagicMock(),
            cache_dir=tmp_path,
            dry_run=dry_run,
        )
        result = engine._phase_backup(lambda *_a: None, dry_run)

        assert result.success is True
        assert "backup.enabled=false" in result.message
