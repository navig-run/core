"""The approval gate is PROCESS-GLOBAL — no test may leak its backend to the next.

`navig.tools.approval` keeps the ApprovalGate singleton and the active policy in module
globals, and `bind_approval_manager` writes a backend into that singleton. The gateway
binds one during `_init_autonomous_modules`, so any test exercising that path installs its
backend for every later test in the same xdist worker.

That is not hypothetical. On 2026-08-06 a full run failed with

    PermissionError: Tool 'navig_agent_component_restart' was not approved by the
    approval gate (denied).

while the same test passed alone (6 passed) and across all of tests/mcp (408 passed).
`test_autonomous_init_and_comms` had installed a FAKE ApprovalManager that does not define
`request_approval`; the next test to gate a dangerous tool called it, the backend raised
`AttributeError`, and the gate fell closed. Ordering-dependent, so it moved between runs
and never looked like a real defect — it looked like flake.

A `dangerous` tool NEVER takes `check_sync`'s fast path (neither `NAVIG_ALLOW_ALL_COMMANDS`
nor "policy does not require approval" applies), so it consults the global gate every single
time. That is why the MCP control-plane test is the natural victim: it wires no approval
state of its own.

The containment lives in `tests/conftest.py::_reset_approval_gate_globals`. This pins it
end-to-end by running the ORIGINAL failing pair in a subprocess, in that exact order —
explicit ordering is what stops this from going vacuous if the runner ever shuffles.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[2]

# The exact pair from the 2026-08-06 failure: the binder, then the victim.
POLLUTER = "tests/gateway/test_gateway_server_runtime.py::test_autonomous_init_and_comms"
VICTIM = "tests/mcp/test_mcp_server_agent_control_plane.py::test_agent_component_restart_and_retry"


@pytest.mark.integration
def test_gateway_bind_does_not_leak_into_a_later_test(tmp_path: Path) -> None:
    for nodeid, path in ((POLLUTER, POLLUTER), (VICTIM, VICTIM)):
        f = CORE_ROOT / path.split("::")[0]
        assert f.exists(), (
            f"{f} is gone — this guard names two real tests by nodeid, and a renamed file "
            f"would make it pass having run nothing. Re-point it at the moved test."
        )
        del nodeid

    env = dict(os.environ)
    # Pin the subprocess to THIS tree. The editable install can resolve `navig` to the main
    # checkout, and a worktree run would then silently validate main's code instead of the
    # change under test.
    env["PYTHONPATH"] = str(CORE_ROOT)
    # Give it its own HOME/config dir. This test adds one more concurrent pytest to an
    # `-n auto` run, and app configs are written to a cwd-derived project-local `.navig/`
    # that every worker shares — so an unisolated child would widen an existing cross-worker
    # race rather than just observing it. (It must still run FROM CORE_ROOT, or the nodeids
    # below do not resolve.)
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["NAVIG_CONFIG_DIR"] = str(home / ".navig")
    env["NAVIG_DATA_DIR"] = str(tmp_path / "data")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", POLLUTER, VICTIM, "-q", "-p", "no:cacheprovider"],
        cwd=CORE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert proc.returncode == 0, (
        "A gateway test that binds an ApprovalManager leaked it into a later test.\n"
        "Expected both to pass; the reset lives in tests/conftest.py"
        f"::_reset_approval_gate_globals.\n\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}"
    )
    # Both must actually RUN — a collection error also exits non-zero, but a typo'd nodeid
    # can select zero tests and still exit 0 on some pytest versions.
    assert "2 passed" in proc.stdout, (
        f"expected exactly the two named tests to run and pass\n{proc.stdout[-4000:]}"
    )
