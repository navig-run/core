"""`navig agent run --timeout` must cap the PROCESS, not only the call.

Regression: the run used a ThreadPoolExecutor and `shutdown(wait=False)`. That let
`agent_run` return and print [TIMEOUT], but concurrent.futures' interpreter-exit hook
still joined the worker, so the CLI process (and any shell awaiting it) stayed blocked
until the hung AI call finished. The child here runs `agent_run` against an AI stub that
sleeps far longer than the timeout; the process must exit shortly after the cap.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]

_CHILD = r"""
import time
import types

import navig.ai
import navig.formations.loader as loader
from navig.commands import agent as agent_cmd

agent = types.SimpleNamespace(
    id="stub", name="Stub", role="tester", system_prompt="", description=""
)
formation = types.SimpleNamespace(id="f", loaded_agents={"stub": agent})
loader.get_active_formation = lambda *a, **k: formation
navig.ai.ask_ai_with_context = lambda *a, **k: time.sleep(120)

agent_cmd.agent_run(
    agent_id="stub", task="hang", json_output=False, plain=True, timeout=1.0, effort=None
)
"""


def test_agent_run_timeout_does_not_hold_process_exit(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / ".navig")
    env["NAVIG_DATA_DIR"] = str(tmp_path / ".navig" / "data")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(CORE), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )

    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )
    elapsed = time.monotonic() - started

    assert "[TIMEOUT after 1.0s]" in proc.stdout, proc.stdout + proc.stderr
    # Import cost dominates; the stub sleeps 120 s, so anything well under that proves
    # the stuck worker no longer holds interpreter exit.
    assert elapsed < 45, f"process held exit for {elapsed:.1f}s after the timeout"
