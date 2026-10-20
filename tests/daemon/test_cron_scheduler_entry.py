"""The supervisor must never spawn `python -m navig.scheduler.cron_service`.

The scheduler child registered by `NavigDaemon.add_scheduler` was a silent
no-op: `navig.scheduler.cron_service` has no `if __name__ == "__main__"` runner,
so the module imported and exited 0 — the supervisor respawned it forever with
back-off while believing a scheduler was supervised. The real scheduler is
gateway-attached: `NavigGateway._start_cron()` builds THE `CronService`
singleton (registered via `_register_live_service`) and starts its loop.

Three facts pinned here:

1. `DEFAULT_DAEMON_CONFIG` no longer carries a `scheduler` toggle.
2. No registration builder can put the cron module on a child argv, and
   `entry.main()` ignores a legacy `scheduler: true` config.
3. Invoking the module directly fails loudly instead of exiting 0 — so even a
   stray hand-rolled spawn cannot look like a serving child.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import navig.daemon.entry as entry

# core/ — so the subprocess resolves the WORKTREE's navig package (the editable
# install points at the main checkout; PYTHONPATH must lead with this tree).
_CORE_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _entry_chdirs_to_its_home(monkeypatch):
    """entry.main() chdir's to config_dir() by design — restore cwd afterwards
    (same guard as tests/daemon/test_entry_is_idempotent.py)."""
    monkeypatch.chdir(os.getcwd())


def test_default_daemon_config_has_no_scheduler_toggle() -> None:
    """The default config must not advertise the removed child toggle."""
    assert "scheduler" not in entry.DEFAULT_DAEMON_CONFIG


def test_entry_ignores_a_legacy_scheduler_true_config(monkeypatch, tmp_path) -> None:
    """A stale `"scheduler": true` on disk must register NOTHING — not a crash,
    not a no-op child."""
    import navig.daemon.supervisor as supervisor

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    calls: list[str] = []

    class _FakeDaemon:
        def __init__(self, health_port: int = 0) -> None:
            calls.append(f"init:{health_port}")

        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid() -> int | None:
            return None

        def add_telegram_bot(self, bot_script=None) -> None:
            calls.append("telegram")

        def add_gateway(self, port: int = 0) -> None:
            calls.append(f"gateway:{port}")

        def add_scheduler(self) -> None:  # pragma: no cover — must never be called
            raise AssertionError(
                "entry.main() tried to register the removed scheduler child — "
                "python -m navig.scheduler.cron_service is a silent no-op"
            )

        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(supervisor, "NavigDaemon", _FakeDaemon)
    monkeypatch.setattr(
        entry,
        "_load_config",
        lambda: {"telegram_bot": True, "gateway": True, "scheduler": True, "health_port": 0},
    )

    entry.main()  # must return cleanly, never reaching add_scheduler

    assert "run" in calls, "the daemon did not start from a legacy config"
    assert "telegram" in calls and any(c.startswith("gateway:") for c in calls)


def test_running_the_cron_module_directly_fails_loudly(tmp_path) -> None:
    """e2e: `python -m navig.scheduler.cron_service` must NOT exit 0 as a silent
    no-op — it has no runner, so it fails immediately with the gateway hint."""
    env = {**os.environ}
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(_CORE_DIR) + (os.pathsep + existing if existing else "")
    # Isolate every path the import could touch away from the operator's brain.
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / "cfg")
    env["NAVIG_DATA_DIR"] = str(tmp_path / "data")
    env["NAVIG_LOG_DIR"] = str(tmp_path / "logs")

    proc = subprocess.run(
        [sys.executable, "-m", "navig.scheduler.cron_service"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(tmp_path),
    )

    combined = (proc.stderr or "") + (proc.stdout or "")
    assert proc.returncode != 0, (
        "python -m navig.scheduler.cron_service exited 0 — a supervisor would "
        f"read that as a serving child. stdout/stderr: {combined!r}"
    )
    assert "gateway" in combined.lower()
    assert "cron runs" in combined.lower()
