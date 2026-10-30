"""One seam answers "which OS is this?" — and no new module re-decides it.

Why this exists: before ``navig.platform.capabilities``, ~250 branches across
``core/navig`` tested ``sys.platform``, ``os.name`` and ``platform.system()``, so the
same question had three answers and none of them was reviewable in one place. Two real
defects came out of that sprawl: a test that patched ``os.name`` made ``pathlib`` build
a ``WindowsPath`` on Linux and crash before the code under test ran, and the installer's
PATH snippet was written for shells the user's terminal does not start.

This test is a RATCHET, not a rewrite: the baseline below is the number of remaining
``os.name``/``platform.system()`` comparisons per file at the moment the seam landed.
A file may keep the ones it has (each is correct on the three supported hosts) and must
never gain another; when a file drops, its entry must be lowered to match, so the
baseline can only shrink. New code asks ``navig.platform.capabilities`` instead:

    from navig.platform import capabilities

    if capabilities.is_macos():          # not sys.platform == "darwin"
        ...
    if capabilities.supports("launchd"): # not a bare hasattr/platform probe
        ...
"""

from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"

#: The seam itself names the platforms it abstracts.
_ALLOWED_FILE = "navig/platform/capabilities.py"

_PATTERN = re.compile(r"os\.name\s*(?:==|!=)|platform\.system\(\)\s*(?:==|!=)")

#: file (relative to ``core/navig`` with the ``navig/`` prefix kept) -> comparisons.
#: Lowered by exactly the amount a change removes; never raised.
BASELINE: dict[str, int] = {
    "navig/agent/background_task.py": 1,
    "navig/agent/tools/repo_search_tools.py": 1,
    "navig/agent/worktree.py": 1,
    "navig/ai.py": 1,
    "navig/builtin/tools/_lib/common.py": 1,
    "navig/cli/__init__.py": 1,
    "navig/cli/providers.py": 1,
    "navig/cli/selector.py": 1,
    "navig/commands/app.py": 1,
    "navig/commands/flux.py": 2,
    "navig/commands/host.py": 1,
    "navig/commands/init.py": 1,
    "navig/commands/interactive.py": 1,
    # navig/commands/config.py reached 0 (its edit_config branch went through
    # navig.platform.opener), so its entry was removed rather than kept as debt.
    "navig/commands/local_discovery.py": 2,
    "navig/commands/menu.py": 2,
    "navig/commands/monitor.py": 4,
    "navig/commands/monitoring.py": 2,
    "navig/commands/mount.py": 1,
    "navig/commands/os_cmd.py": 1,
    "navig/commands/plan_mode.py": 1,
    "navig/commands/prompts.py": 2,
    "navig/commands/update.py": 1,
    "navig/core/connection.py": 2,
    "navig/core/evolution/sweep.py": 1,
    "navig/core/file_permissions.py": 1,
    "navig/core/security.py": 1,
    "navig/daemon/supervisor.py": 1,
    "navig/guard/session_start.py": 1,
    "navig/installer/__init__.py": 1,
    "navig/llm/guard.py": 1,
    "navig/move/os_sign_ins.py": 1,
    "navig/notify/monitors/resources.py": 1,
    "navig/onboarding/steps.py": 1,
    "navig/readiness/remote_dev.py": 2,
    "navig/remote.py": 1,
    "navig/scheduler/cron_service.py": 1,
    "navig/storage/engine.py": 2,
    "navig/store/base.py": 1,
    "navig/telegram/business.py": 2,
    "navig/tools/bash_exec.py": 1,
    "navig/tools/shell_readonly.py": 2,
}


def _comparisons() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE.parent).as_posix()
        if rel == _ALLOWED_FILE:
            continue
        count = len(_PATTERN.findall(path.read_text(encoding="utf-8", errors="replace")))
        if count:
            found[rel] = count
    return found


def test_no_module_gains_a_new_platform_branch():
    found = _comparisons()
    grown = {
        rel: (BASELINE.get(rel, 0), count)
        for rel, count in found.items()
        if count > BASELINE.get(rel, 0)
    }
    assert not grown, (
        "these modules added a platform branch; ask navig.platform.capabilities instead "
        f"(baseline → now): {grown}"
    )


def test_a_module_that_dropped_its_branches_lowers_the_baseline():
    """The baseline only shrinks: a file with fewer comparisons than recorded must have
    its entry lowered, so the number always states the real remaining debt."""
    found = _comparisons()
    stale = {
        rel: (expected, found.get(rel, 0))
        for rel, expected in BASELINE.items()
        if found.get(rel, 0) < expected
    }
    assert not stale, (
        f"lower these BASELINE entries to the counts shown (recorded → now): {stale}"
    )


def test_a_deleted_file_leaves_no_ghost_entry():
    found = _comparisons()
    ghosts = sorted(rel for rel in BASELINE if rel not in found and not (CORE.parent / rel).exists())
    assert not ghosts, f"BASELINE names files that no longer exist: {ghosts}"


def test_the_seam_owns_the_platform_question():
    from navig.platform import capabilities

    assert capabilities.os_name() in ("windows", "macos", "linux", "wsl")
    assert capabilities.is_windows() is (capabilities.os_name() == "windows")
    assert capabilities.executable_suffix() in ("", ".exe")
    assert capabilities.launcher_name().endswith(capabilities.executable_suffix())
    assert capabilities.service_manager() in ("systemd", "launchd", "nssm", "schtasks")
