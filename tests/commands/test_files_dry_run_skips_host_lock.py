"""A `--dry-run` preview of `navig file remove/mkdir/edit --mode/--owner` took the host lock.

The commands claimed the session's host lock (navig.core.host_lock) BEFORE their
dry-run branch returned, so merely previewing a change could block another session's
real operation on that server for the lock's whole TTL. A preview mutates nothing and
must not contend with real work; the real path still claims the host.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import navig.commands.files_advanced as fa
import navig.console_helper as ch


def _run(fn, *args, options):
    cm = MagicMock()
    cm.load_server_config.return_value = {"host": "srv"}
    ops = MagicMock()
    ops.execute_command.side_effect = lambda cmd, _cfg: SimpleNamespace(
        returncode=0, stdout="exists file", stderr=""
    )
    guard = MagicMock()
    with (
        patch("navig.config.get_config_manager", return_value=cm),
        patch("navig.remote.RemoteOperations", return_value=ops),
        patch("navig.cli.recovery.require_active_server", return_value="vps-1"),
        patch("navig.core.host_lock.guard_remote", guard),
        patch.object(ch, "error"),
        patch.object(ch, "success"),
        patch.object(ch, "warning"),
        patch.object(ch, "info"),
        patch.object(ch, "raw_print"),
        patch.object(ch, "confirm_action", return_value=True),
    ):
        fn(*args, options)
    return guard


CASES = [
    (fa.delete_file_cmd, ("/tmp/f.txt",)),
    (fa.mkdir_cmd, ("/tmp/d",)),
    (fa.chmod_cmd, ("/tmp/f.txt", "644")),
    (fa.chown_cmd, ("/tmp/f.txt", "www-data")),
]


@pytest.mark.parametrize(("fn", "args"), CASES, ids=[c[0].__name__ for c in CASES])
def test_dry_run_does_not_claim_the_host(fn, args) -> None:
    guard = _run(fn, *args, options={"dry_run": True})

    guard.assert_not_called()


@pytest.mark.parametrize(("fn", "args"), CASES, ids=[c[0].__name__ for c in CASES])
def test_the_real_operation_still_claims_the_host(fn, args) -> None:
    guard = _run(fn, *args, options={"force": True})

    guard.assert_called_once()
    assert guard.call_args.args[1] == "vps-1"
