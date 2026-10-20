"""Root-level pytest configuration.

Ensures the ``--basetemp`` directory exists before collection starts so it never
fails on a fresh clone (fixes #34). The path is read back from the running
config rather than retyped — a hardcoded copy here silently drifted to
``.local/.pytest_tmp`` while ``pytest.ini`` pinned ``.pytest_tmp``, so this hook
pre-created a directory pytest never used.

It also gives every RUN its own temp root (see ``PYTEST_DEBUG_TEMPROOT`` below), because
several agent sessions run this suite at the same time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent
_BUILD_LIB = (_PROJECT_ROOT / "build" / "lib").resolve()

# ── One temp root per RUN, not one shared by all of them ──────────────────────────────────
# `pytest.ini` used to pin `--basetemp=.dev/tmp/pytest`, and `--basetemp` WIPES AND RECREATES
# the directory it points at. Several sessions run this suite concurrently here, so each new
# run deleted the temp dirs of the runs already in flight and died before executing a single
# test:
#
#   INTERNALERROR> PermissionError: [WinError 32] The process cannot access the file because
#   it is being used by another process: '...\\.dev\\tmp\\pytest\\popen-gw12\\...\\task_1.log'
#
# Since the pre-push gate runs the suite, that also turned every concurrent push into a false
# failure. Dropping `--basetemp` hands temp-dir management back to pytest, which allocates a
# NUMBERED `pytest-<n>` directory per run under this root, guarded by a lock file and rotated
# (it keeps the last few) — the mechanism built for exactly this.
#
# The root stays inside `core/` on purpose: `tests/core/test_tmp_dir_repo_boundary.py` pins
# that every `tmp_path` has a `.git` AND `.navig` ancestor, because a hundred tests rely on it
# (the `temp_dir` fixture is the escape hatch for "must be outside a repo"). `.dev/` is also
# where the artifact policy requires generated files to live.
#
# `setdefault`: an explicit `PYTEST_DEBUG_TEMPROOT` from the environment still wins.
_TMP_ROOT = _PROJECT_ROOT / ".dev" / "tmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(_TMP_ROOT))


def _ensure_repo_import_priority() -> None:
    """Keep the repo root ahead of any ``build/lib`` on ``sys.path``.

    Cheap enough to run before every test and every collection step: it only
    rewrites the small ``sys.path`` list, so a test that mutates ``sys.path`` is
    corrected on the next hook — without the O(sys.modules) filesystem scan that
    ``_evict_build_lib_modules`` performs.
    """
    cleaned: list[str] = []
    for entry in sys.path:
        try:
            resolved = Path(entry).resolve()
        except Exception:  # noqa: BLE001
            cleaned.append(entry)
            continue
        if resolved == _BUILD_LIB:
            continue
        cleaned.append(entry)

    sys.path[:] = cleaned
    root_str = str(_PROJECT_ROOT)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)


def _evict_build_lib_modules() -> None:
    """Drop any ``navig`` module that was imported from ``build/lib``.

    This ``Path.resolve()``-per-navig-module scan is expensive — ~50-170 ms once
    the session has imported hundreds of navig modules. It only needs to run
    ONCE, at session start: ``_ensure_repo_import_priority`` keeps ``build/lib``
    off ``sys.path`` for the whole run, so no ``build/lib`` module can be
    (re)imported mid-session. Running it in ``pytest_runtest_setup`` (per test)
    and ``pytest_collectstart`` (per module) was pure overhead and dominated
    suite setup time. No-op when there is no ``build/lib`` tree to shadow.
    """
    if not _BUILD_LIB.exists():
        return
    stale_modules: list[str] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("navig"):
            continue
        module_file = getattr(module, "__file__", None)
        # Cheap prefilter: only a module physically under build/lib can be stale.
        # Skipping the slow Path.resolve() (Windows realpath) for every other navig
        # module — this hook runs on *every* collect step, so resolving all of
        # sys.modules each time was O(nodes × modules) and made collection hang.
        if not module_file or "build" not in module_file.lower():
            continue
        try:
            resolved_file = Path(module_file).resolve()
        except Exception:  # noqa: BLE001
            continue
        if _BUILD_LIB in resolved_file.parents:
            stale_modules.append(module_name)

    for module_name in stale_modules:
        sys.modules.pop(module_name, None)


def _normalize_import_path() -> None:
    """Full normalization: repo import priority + one-time ``build/lib`` eviction."""
    _ensure_repo_import_priority()
    _evict_build_lib_modules()


def pytest_sessionstart(session):
    """Create the configured basetemp directory if it doesn't exist yet."""
    basetemp = getattr(session.config.option, "basetemp", None)
    if basetemp:
        Path(basetemp).mkdir(parents=True, exist_ok=True)
    _normalize_import_path()


def pytest_runtest_setup(item):  # noqa: ARG001
    """Re-apply the cheap sys.path priority in case a test mutated ``sys.path``."""
    _ensure_repo_import_priority()
    if item.nodeid.startswith("tests/test_provider_control_surface.py"):
        sys.modules.pop("navig.gateway.channels.telegram_keyboards", None)


def pytest_collectstart(collector):  # noqa: ARG001
    """Keep repo import priority before each collection step/module import."""
    _ensure_repo_import_priority()


def _dist_given_explicitly(args: tuple[str, ...] | list[str]) -> bool:
    return any(a == "--dist" or a.startswith("--dist=") or a == "-d" for a in args)


def pytest_configure(config):
    """Under xdist, honour ``xdist_group`` markers by default (``load`` -> ``loadgroup``).

    ``-n`` alone means ``--dist load``, which IGNORES ``xdist_group``: the e2e gateways
    (``tests/e2e/test_gateway_api.py``) then boot on several workers at once and fail
    at random (a 401 from a neighbour's gateway). The gate always passes
    ``--dist loadgroup``; a hand-typed ``pytest -n auto`` did not. ``loadgroup``
    schedules ungrouped tests exactly like ``load``, so this changes nothing else.

    Not ``--dist`` in ``pytest.ini`` addopts: xdist is optional (the gate probes for
    it), and without it ``--dist`` is an unknown option that refuses to start pytest.
    An explicit ``--dist`` on the command line always wins.

    Two halves, because xdist decides grouping in two places: the CONTROLLER picks the
    scheduler from ``option.dist`` (promoted here), but each WORKER re-parses the original
    command line — no ``--dist`` there — and sets ``option.loadgroup`` from that before any
    conftest runs. Without the worker half the scheduler says LoadGroupScheduling while
    no test carries its ``@group`` suffix, so nothing is actually grouped (measured).
    ``pytest_configure_node`` hands the decision to each worker via ``workerinput``.
    """
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        if workerinput.get("navig_loadgroup"):
            config.option.loadgroup = True
        return
    option = config.option
    if getattr(option, "dist", None) != "load":
        return  # xdist absent, not distributing, or a mode chosen on purpose
    if _dist_given_explicitly(config.invocation_params.args):
        return
    option.dist = "loadgroup"


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    """xdist controller hook: tell each worker the run is grouped (see pytest_configure)."""
    if getattr(node.config.option, "dist", None) == "loadgroup":
        node.workerinput["navig_loadgroup"] = True



# ── `-n auto` respects the machine's heavy-job slots ──────────────────────────
#
# One machine-wide protocol limits heavy jobs: scripts/heavy-slot-lib.mjs (slot files in
# ~/.navig/slots/heavy, a per-lane CPU share exported as NAVIG_HEAVY_CPUS). The gate takes
# a slot; a hand-typed `pytest -n auto` did not, and claimed ~20 workers on top of the
# gates (2026-10-08: a dozen workers crashed, the run died with no summary). This only
# READS that protocol — it never takes a slot. To queue properly, wrap the run:
#     node scripts/heavy-slot.mjs --run -- py -3.13 -m pytest -n auto …

_SLOT_MAX_HOLD_S = 3 * 3600  # heavy-slot-lib MAX_HOLD_MS: an older slot is free


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return n if lo <= n <= hi else default


def _heavy_slot_lanes() -> int:
    """capacity + overflowCapacity, with heavy-slot-lib's defaults and bounds."""
    return _int_env("NAVIG_HEAVY_SLOTS", 3, 1, 64) + _int_env("NAVIG_HEAVY_OVERFLOW", 1, 0, 8)


def _busy_heavy_slots(slot_dir: Path) -> int:
    """Live holders of slot-*.json / overflow-*.json — heavy-slot-lib's isFree, inverted.

    psutil, never ``os.kill(pid, 0)``: on Windows signal 0 is not a probe — ``os.kill``
    calls TerminateProcess and would KILL the holder it was asking about.
    """
    import json
    from datetime import datetime, timezone

    try:
        import psutil
    except ImportError:
        return 0
    try:
        files = [f for f in slot_dir.iterdir() if f.suffix == ".json" and f.name.startswith(("slot-", "overflow-"))]
    except OSError:
        return 0
    now = datetime.now(timezone.utc)
    busy = 0
    for f in files:
        try:
            holder = json.loads(f.read_text(encoding="utf-8"))
            pid = int(holder["pid"])
            started = datetime.fromisoformat(str(holder["started"]).replace("Z", "+00:00"))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable or mid-write: heavy-slot-lib treats it as free too
        if (now - started).total_seconds() > _SLOT_MAX_HOLD_S:
            continue
        if pid > 0 and psutil.pid_exists(pid):
            busy += 1
    return busy


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):  # noqa: ARG001
    """`-n auto` = this lane's share when the machine's heavy slots are in use; else xdist's default."""
    share = _int_env("NAVIG_HEAVY_CPUS", 0, 1, 1024)
    if share:
        return share  # we run inside a slot holder; it already computed our share
    if os.environ.get("CI") or str(os.environ.get("NAVIG_HEAVY_SLOTS", "")).lower() in ("0", "off"):
        return None
    slot_dir = Path(os.environ.get("NAVIG_SLOT_DIR") or Path.home() / ".navig" / "slots" / "heavy")
    busy = _busy_heavy_slots(slot_dir)
    if busy == 0:
        return None
    n = max(2, (os.cpu_count() or 1) // _heavy_slot_lanes())
    sys.stderr.write(
        f"[navig] {busy} heavy-job slot(s) in use on this machine — using {n} xdist workers "
        "instead of `auto`. Queue for a slot instead: "
        "node scripts/heavy-slot.mjs --run -- <your pytest command>\n"
    )
    return n
