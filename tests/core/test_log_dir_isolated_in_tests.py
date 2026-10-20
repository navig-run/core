"""The test session must never write to the operator's REAL debug.log.

`paths.log_dir()` reads NAVIG_LOG_DIR and otherwise resolves to the OS log location
(%LOCALAPPDATA%\\navig\\logs on Windows). The session fixture `_isolate_navig_config_dir`
isolates CONFIG / DATA / CACHE — and now LOG too. Before that, every GoalPlanner /
RemediationEngine / DebugLogger built in a test appended structured OPERATION blocks to
the one real debug.log shared by all xdist workers: a RotatingFileHandler rollover race
on Windows (WinError 32) and pollution of the real audit log — the flakiness the locked
-store tests kept surfacing.

This guard fails if that isolation ever regresses — mirroring the rule the cache/data
isolation already encodes: "config is isolated" does not imply "state is isolated".
"""

from __future__ import annotations

import os
from pathlib import Path

from navig.debug_logger import DebugLogger
from navig.platform import paths


def test_log_dir_env_is_set_during_the_session():
    assert os.environ.get("NAVIG_LOG_DIR"), "NAVIG_LOG_DIR is not isolated during tests"


def test_log_dir_resolves_under_the_isolated_tempdir():
    resolved = str(paths.log_dir()).replace("\\", "/")
    # The fixture creates the root with tempfile.mkdtemp(prefix="navig_cfg_isolated_").
    assert "navig_cfg_isolated_" in resolved, (
        f"log_dir() resolved to {resolved!r} — not the isolated session tempdir; "
        "a test could be writing to the operator's real debug.log"
    )


def test_debug_logger_without_a_path_writes_into_the_isolated_dir():
    dl = DebugLogger()  # no log_path -> resolves via paths.debug_log_path()
    resolved = str(dl.log_path).replace("\\", "/")
    assert "navig_cfg_isolated_" in resolved, (
        f"DebugLogger resolved to {resolved!r} — outside the isolated session tempdir"
    )
    dl.log_operation("isolation-probe", {"ok": 1})
    assert Path(dl.log_path).exists()
    dl.close()
