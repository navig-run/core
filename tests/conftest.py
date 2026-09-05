"""
Shared pytest fixtures for NAVIG test suite.

This module provides common fixtures used across all test files:
- Mock configurations
- Console mocks
- Temporary directories
- Sample data factories
"""

import asyncio
import gc
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, Mock

import pytest
import yaml

# ── Console width ──────────────────────────────────────────────────────────
# Rich hard-wraps to the console width, and when stdout is not a tty it assumes
# **80** — a width the operator never actually uses (CLAUDE.md: "piping to a
# subprocess falsely reports 80"; the real PowerShell width is 120). At 80 a
# perfectly correct message gets a newline inserted mid-sentence, so
# `assert "nothing recorded yet" in result.output` fails on output that reads
# exactly right in a terminal.
#
# It bites intermittently, which is the expensive part: the wrap column moves
# with the message length, and these views print PATHS — under `-n auto` xdist
# lengthens tmp_path with the worker id (`popen-gw6`), so the same assert passes
# solo, passes for its directory, and fails only in the full run. Three separate
# tests were debugged this way (ledger, reversibility, audit-tail) before the
# pattern was recognised, and 215 more asserts across 75 files are exposed to it.
#
# Setting it once here removes the variable instead of teaching 215 asserts to
# tolerate it. Verified: those 75 files give 1832 passed / 1 skipped both with
# and without this, so no assertion depends on the 80-column wrap.
#
# Assigned, not `setdefault`: inheriting a narrow COLUMNS from the operator's
# shell is the very failure being removed. A test that genuinely cares about
# layout must pass its own width to the subprocess or CliRunner env it drives —
# `test_help_md_pages.py` (120) and `test_doctor_heal.py` (`_WIDE` = 160)
# already do exactly that, and still work because each builds a fresh Console.
os.environ["COLUMNS"] = "200"

# ── Vault crypto guard ─────────────────────────────────────────────────────
# Two platform operations hang indefinitely in fresh subprocesses on this
# Windows / Python 3.14 environment:
#   1. argon2-cffi DLL load  (argon2.low_level) — fixed by pre-setting probe flag
#   2. _machine_fingerprint  (platform.node / DNS) — fixed by patching derive_key
#
# Patch CryptoEngine.derive_key at conftest *import* time (before any test
# module is loaded or any fixture runs) so that the vault always uses a fast,
# deterministic test key — no KDF iteration, no platform calls.
try:
    import hashlib as _hashlib

    import navig.vault.crypto as _vc  # noqa: PLC0415

    # Disable argon2 probe (belt-and-suspenders — derive_key is patched anyway)
    _vc._argon2_probed = True
    _vc._argon2_funcs = None

    def _test_derive_key(self, passphrase=None):  # noqa: ANN001
        """Test-only key derivation: SHA-256, no KDF, no platform calls."""
        material = passphrase if passphrase is not None else b"navig-test-machine"
        return _hashlib.sha256(b"navig-test-vault-" + material).digest()

    _vc.CryptoEngine.derive_key = _test_derive_key  # type: ignore[method-assign]
    del _test_derive_key  # prevent it from leaking into fixture namespace

    # Also patch _machine_fingerprint so the new _stable_machine_uuid() helper
    # (subprocess / file / registry calls) never runs during test collection.
    _vc.CryptoEngine._machine_fingerprint = staticmethod(  # type: ignore[method-assign]
        lambda: b"navig-test-machine"
    )
    _vc.CryptoEngine._legacy_fingerprint = staticmethod(  # type: ignore[method-assign]
        lambda: b"navig-test-machine"
    )
except Exception:  # noqa: BLE001
    pass  # best-effort; if crypto module can't be imported, tests will still collect


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Close the navig.debug logger's file handlers at the end of the test
    session to prevent ResourceWarning: unclosed file at GC finalisation."""
    import logging

    debug_log = logging.getLogger("navig.debug")
    for h in list(debug_log.handlers):
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
        debug_log.removeHandler(h)


def pytest_configure(config):  # noqa: ARG001
    """Pre-disable the argon2-cffi probe in navig.vault.crypto.

    On Windows / Python 3.14 the argon2-cffi C extension (argon2.low_level)
    blocks indefinitely when the DLL is first loaded, which causes every test
    that triggers key derivation (vault add/unlock) to hang.

    Since navig.vault.crypto now probes argon2 lazily (on the first _kdf
    call), we can pre-set the probe variables here — at pytest startup, before
    any fixture or test runs — so that _kdf always uses the PBKDF2-HMAC-SHA256
    fallback throughout the test session without ever touching the broken DLL.

    This does NOT affect production behaviour; only the test session.
    """
    try:
        import navig.vault.crypto as _vc  # noqa: PLC0415

        _vc._argon2_probed = True
        _vc._argon2_funcs = None
    except Exception:  # noqa: BLE001
        pass  # best-effort; if the module can't be imported, skip silently


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip ``@pytest.mark.live`` tests unless explicitly opted in.

    Live tests hit a running gateway on ``http://localhost:8789`` and can MUTATE
    it (add/delete cron jobs, trigger heartbeats). They already skip when the
    gateway is unreachable, but on a dev machine with a real daemon running an
    unguarded ``pytest tests/`` would mutate live state. Require
    ``NAVIG_TEST_LIVE=1`` to run them; CI (no daemon) never needs them.
    """
    if os.environ.get("NAVIG_TEST_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test — set NAVIG_TEST_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# A FULL `gc.collect()` here cost 46% of the suite's wall clock. This helper runs from an
# autouse fixture BEFORE AND AFTER every test — 27,456 tests, so ~55,000 full gen-2 sweeps of
# a heap holding the entire collected suite. Measured on tests/config (219 tests), idle,
# back-to-back: full 61.7s · gen-0 33.0s · no gc at all 31.5s. Gen-0 recovers 28.7s of the
# 30.3s theoretically available, i.e. essentially all of it, while still collecting.
#
# The point of collecting is to run `__del__` on unreferenced Popen handles so
# `subprocess._cleanup()` can reap them and no "unraisable" warning leaks into the next test.
# Such a handle is created DURING the test, so it is young — gen-0/1 is the right window.
# But automatic GC during a long test can promote it, and gen-2 objects are only examined by
# a full sweep, so a purely gen-0 policy could let one linger and surface the warning later
# against an unrelated test. Hence: cheap sweep every drain, full sweep every _FULL_GC_EVERY
# drains, which bounds the lingering while amortising the 13ms+ cost to well under 1ms.
_FULL_GC_EVERY = 50
_drain_count = 0


def _drain_orphan_subprocesses() -> None:
    """Best-effort cleanup of orphan child processes/subprocess handles."""
    try:
        subprocess._cleanup()
    except Exception:  # noqa: BLE001
        pass

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # noqa: BLE001
        loop = None

    if loop is not None:
        subprocesses = getattr(loop, "_subprocesses", None)
        if isinstance(subprocesses, dict):
            for transport in list(subprocesses.values()):
                try:
                    transport.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                subprocesses.clear()
            except Exception:  # noqa: BLE001
                pass

    global _drain_count
    _drain_count += 1
    gc.collect(2 if _drain_count % _FULL_GC_EVERY == 0 else 1)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):  # noqa: ARG001
    """Run cleanup before pytest's unraisable-collection setup hooks."""
    _drain_orphan_subprocesses()

    try:
        active = list(getattr(subprocess, "_active", []))
    except Exception:  # noqa: BLE001
        active = []

    for proc in active:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=1)
        except Exception:  # noqa: BLE001
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception:  # noqa: BLE001
                pass

    try:
        import psutil

        parent = psutil.Process()
        for child in parent.children(recursive=True):
            try:
                if child.is_running():
                    child.terminate()
            except Exception:  # noqa: BLE001
                pass

        _, alive = psutil.wait_procs(parent.children(recursive=True), timeout=1)
        for child in alive:
            try:
                if child.is_running():
                    child.kill()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _cleanup_orphan_subprocesses():
    """Drain subprocess leftovers before and after each test.

    Running pre-test cleanup avoids strict unraisable warnings leaking into the
    next test's call phase when previous tests leave dangling subprocess objects.
    """
    _drain_orphan_subprocesses()
    yield
    _drain_orphan_subprocesses()


@pytest.fixture(autouse=True)
def _cleanup_orphan_asyncio_tasks():
    """Best-effort cleanup for leaked asyncio tasks/transports between tests."""
    yield

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # noqa: BLE001
        return

    # Explicitly close loop-tracked subprocess transports (Windows proactor).
    subprocesses = getattr(loop, "_subprocesses", None)
    if isinstance(subprocesses, dict):
        for transport in list(subprocesses.values()):
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            subprocesses.clear()
        except Exception:  # noqa: BLE001
            pass

    # Cancel pending tasks when possible. In running loops (pytest-asyncio),
    # cancellation is still useful even if we cannot synchronously await here.
    try:
        pending = [task for task in asyncio.all_tasks(loop=loop) if not task.done()]
    except Exception:  # noqa: BLE001
        pending = []

    for task in pending:
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass

    if pending and not loop.is_running():
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(autouse=True)
def _clear_dispatch_credential_cache():
    """Never leak a resolved dispatch credential from one test into another.

    navig.providers.inference keeps a short-TTL cache of successful credential
    resolutions (the parallel fan-out hardening). Tests that resolve through
    patched seams would otherwise poison later tests within the TTL window.
    Guarded by sys.modules so this never force-imports the providers package.
    """
    import sys as _sys

    def _clear() -> None:
        inf = _sys.modules.get("navig.providers.inference")
        if inf is not None:
            try:
                inf.invalidate_credential_cache()
            except Exception:  # noqa: BLE001 — cache hygiene must never fail a test
                pass

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _reset_approval_gate_globals():
    """Never leak approval state from one test into another.

    `navig.tools.approval` keeps BOTH the ApprovalGate singleton and the active
    policy in module globals, and `bind_approval_manager` writes a backend into
    that singleton. The gateway binds one during `_init_autonomous_modules`, so a
    test that exercises that path installs its backend for every later test in the
    same xdist worker — and a `dangerous` tool never fast-paths, it always asks the
    gate.

    Reproduced exactly (2026-08-06): `test_autonomous_init_and_comms` installs a
    FAKE ApprovalManager, and the next test to gate a dangerous tool called
    `request_approval` on that fake — a method it does not define — so the backend
    raised, failed closed, and `test_agent_component_restart_and_retry` failed with
    `not approved by the approval gate (denied)`. It passed alone (6) and across all
    of tests/mcp (408); only the full suite saw it. Ordering-dependent, so it moved
    between runs and never looked like a real defect.

    `reset_approval_gate()` alone is NOT enough — it clears the instance and leaves
    `_policy` untouched, so a leaked policy survives it. Both are reset here.

    Guarded by sys.modules so this never force-imports the approval package, and
    silent on failure: gate hygiene must never be the thing that fails a test.
    """
    import sys as _sys

    def _reset() -> None:
        appr = _sys.modules.get("navig.tools.approval")
        if appr is None:
            return
        try:
            appr.reset_approval_gate()
            appr.set_approval_policy(appr.ApprovalPolicy.CONFIRM_DESTRUCTIVE)
        except Exception:  # noqa: BLE001 — gate hygiene must never fail a test
            pass

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _track_sqlite_connections(monkeypatch: pytest.MonkeyPatch):
    """Track sqlite connections created during a test and close them on teardown."""
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def _tracked_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", _tracked_connect)
    yield

    for conn in opened:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def temp_dir():
    """A temporary directory that is OUTSIDE the git checkout.

    Prefer pytest's built-in ``tmp_path`` for ordinary scratch files. Reach for this one
    whenever the test must stand **outside any git repository**.

    Why the distinction is not academic: ``core/conftest.py`` points ``PYTEST_DEBUG_TEMPROOT``
    at ``core/.dev/tmp``, and pytest allocates ``pytest-of-<user>/pytest-<n>/`` beneath it — so
    every ``tmp_path`` lives *inside* ``core/`` and has a ``.git`` **ancestor**. Anything that
    walks upward looking for a repo (git-root discovery, the agent-lock hook classifier, "am I
    in a worktree" checks) therefore finds one, and a test written with ``tmp_path`` silently
    exercises the opposite of what it meant to. That has already broken two tests. This fixture
    uses the system temp dir, which has no ``.git`` ancestor — see
    ``test_tmp_path_is_inside_the_repo_but_temp_dir_is_not``.

    Do **not** "restore" a ``--basetemp`` to pytest.ini on the strength of this note: an earlier
    version of it claimed pytest.ini pinned ``--basetemp=.dev/tmp/pytest``, which it never does
    on purpose. ``--basetemp`` WIPES the directory it points at, so a fixed one made concurrent
    agent sessions in this checkout delete each other's temp dirs and fail at session start —
    see the comment at the top of ``pytest.ini`` and ``tests/core/test_tmp_dir_concurrency.py``.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
    """Mock NAVIG configuration."""
    return {
        "current_host": "test-server",
        "hosts": {
            "test-server": {
                "host": "test.example.com",
                "user": "testuser",
                "port": 22,
                "key_file": None,
                "password": None,
            }
        },
        "settings": {
            "auto_confirm": False,
            "color_output": True,
            "verbose": False,
        },
    }


@pytest.fixture
def mock_config_file(temp_dir, mock_config):
    """Create a temporary config file with mock data."""
    config_file = temp_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(mock_config, f)
    return config_file


@pytest.fixture
def mock_console():
    """Mock Rich console for output testing."""
    console = Mock()
    console.print = Mock()
    console.log = Mock()
    return console


@pytest.fixture
def mock_ssh_client():
    """Mock paramiko SSH client."""
    client = MagicMock()
    client.connect = Mock()
    client.exec_command = Mock(return_value=(Mock(), Mock(), Mock()))
    client.close = Mock()
    return client


@pytest.fixture
def sample_host_config() -> Dict[str, Any]:
    """Sample host configuration."""
    return {
        "host": "10.0.0.10",
        "user": "admin",
        "port": 22,
        "key_file": "/path/to/key.pem",
        "password": None,
        "tunnel_port": 3307,
    }


@pytest.fixture
def sample_app_config() -> Dict[str, Any]:
    """Sample application configuration."""
    return {
        "name": "test-app",
        "host": "test-server",
        "domain": "test-app.example.com",
        "path": "/var/www/test-app",
        "type": "php",
        "database": "test_app_db",
        "db_user": "test_app_user",
    }


@pytest.fixture
def mock_subprocess_run(monkeypatch):
    """Mock subprocess.run for command execution tests."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "Command executed successfully"
    mock_result.stderr = ""

    mock_run = Mock(return_value=mock_result)
    monkeypatch.setattr("subprocess.run", mock_run)

    return mock_run


@pytest.fixture
def mock_paramiko_client(monkeypatch):
    """Mock paramiko SSHClient for remote operation tests."""
    mock_client = MagicMock()

    # Mock exec_command to return stdin, stdout, stderr
    mock_stdout = Mock()
    mock_stdout.read = Mock(return_value=b"Command output")
    mock_stdout.channel.recv_exit_status = Mock(return_value=0)

    mock_stderr = Mock()
    mock_stderr.read = Mock(return_value=b"")

    mock_stdin = Mock()

    mock_client.exec_command = Mock(return_value=(mock_stdin, mock_stdout, mock_stderr))
    mock_client.connect = Mock()
    mock_client.close = Mock()

    def mock_ssh_client_factory():
        return mock_client

    monkeypatch.setattr("paramiko.SSHClient", mock_ssh_client_factory)

    return mock_client


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    test_env = {
        "NAVIG_CONFIG_DIR": "/tmp/navig-test",
        "NAVIG_AUTO_CONFIRM": "false",
        "OPENROUTER_API_KEY": "test-api-key",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    return test_env


@pytest.fixture
def sample_template_yaml() -> str:
    """Sample template.yaml content for scaffold tests."""
    return """meta:
  name: Test Template
  description: A test template
  version: 1.0.0
  author: Test Author

files:
  - path: config/app.conf
    type: file
    mode: "0644"
    content: |
      # Application config
      APP_NAME={{ app_name }}
      APP_ENV={{ app_env }}

  - path: scripts
    type: directory
    mode: "0755"

  - path: scripts/setup.sh
    type: file
    mode: "0755"
    content: |
      #!/bin/bash
      echo "Setting up {{ app_name }}"
"""


@pytest.fixture
def capture_output(monkeypatch):
    """Capture printed output for assertion."""
    output = []

    def mock_print(*args, **kwargs):
        output.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", mock_print)

    return output


@pytest.fixture
def log_messages():
    """Capture log records emitted by the navig logger tree.

    Because ``navig.core.logging`` configures the root ``navig`` logger with
    ``propagate = False`` and stores ``sys.stderr`` at handler-creation time,
    neither ``caplog``, ``capsys``, nor ``capfd`` see these messages.  This
    fixture attaches a temporary ``ListHandler`` directly to ``logging.getLogger
    ("navig")`` so tests can assert on log text without relying on fd-level or
    sys.stderr-level capture.
    """
    import logging

    records: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _ListHandler(level=logging.DEBUG)
    # Attach to both "navig" and "navig.daemon": the supervisor sets
    # navig.daemon.propagate = False via _make_logger(), so if any test
    # creates a NavigDaemon instance before this fixture runs the message
    # from navig.daemon.telegram_worker would never reach the "navig"
    # handler.  Attaching directly to "navig.daemon" covers that path.
    navig_logger = logging.getLogger("navig")
    daemon_logger = logging.getLogger("navig.daemon")
    # Ensure a permissive level so INFO records are not silently dropped when
    # _configure_root_logger() has not yet been called (e.g., when these tests
    # run in isolation the effective level would otherwise fall back to the
    # root logger's WARNING default).
    original_level = navig_logger.level
    if navig_logger.level == logging.NOTSET:
        navig_logger.setLevel(logging.DEBUG)
    navig_logger.addHandler(handler)
    daemon_logger.addHandler(handler)
    try:
        yield records
    finally:
        navig_logger.removeHandler(handler)
        daemon_logger.removeHandler(handler)
        navig_logger.setLevel(original_level)


# Keep navig_log_capture as an alias so existing tests using either name work.
navig_log_capture = log_messages


# ---------------------------------------------------------------------------
# Test isolation: prevent ConfigManager singleton and platform path cache from
# leaking real project config (~/.navig or .navig/) into unit tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _isolate_navig_config_dir(tmp_path_factory):
    """Isolate the three independent state roots for the whole session.

    They are genuinely independent — each is resolved by its own code path, and
    setting one says nothing about the others:

    * ``NAVIG_CONFIG_DIR`` -> ``global_config_dir``
    * ``NAVIG_DATA_DIR``   -> ``paths.data_dir()`` (its own env var, else ``home/.navig/data``)
    * app-root detection   -> ``base_dir`` (hosts / apps / cache), from the **CWD**

    ⚠ This docstring used to claim the first bullet stopped ``find_app_root()`` from
    finding the real project ``.navig/``. It does not, and never did: that walk starts
    at the cwd and no env var reaches it. The claim is why the gap survived long enough
    to cause e1422a253 — reviewers read the docstring instead of the resolution path.
    Verified by construction, not by reading: with NAVIG_CONFIG_DIR pointed at a temp
    dir, ``ConfigManager().hosts_dir`` still resolved to ``<repo>/core/.navig/hosts``.

    The rule this encodes: **"config is isolated" does not imply "state is isolated"** —
    check each path's OWN resolution before trusting a fixture that appears to cover it.
    """
    import shutil
    import sys

    # -- Pre-cleanup: close any Storage Engine connections from previous runs --
    # On Windows, WAL mode keeps SQLite files locked; must close before rmtree.
    try:
        from navig.storage import get_engine

        get_engine().close_all()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass

    # -- Pre-cleanup: handle stale temp directories from previous crashed runs --
    # On Windows, SQLite files may remain locked; retry with ignore handler.
    def _onerror_ignore(func, path, exc_info):
        """Best-effort cleanup: try chmod then ignore remaining errors."""
        import stat

        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:  # noqa: BLE001
            pass  # let pytest create a new numbered dir suffix

    # Get basetemp carefully - on Windows, pre-existing locked files may cause
    # cleanup failures. Using ignore_errors=True and wrapping in try/except.
    try:
        base_tmp = tmp_path_factory.getbasetemp()
    except Exception:  # noqa: BLE001
        # If getbasetemp() fails due to locked/stale files, use a fresh directory.
        # On Windows this can surface as PermissionError or FileNotFoundError
        # while pytest attempts to clean old basetemp contents.
        base_tmp = Path(tempfile.mkdtemp(prefix="navig_pytest_"))

    # Clean up any leftover navig_cfg_isolated* directories
    try:
        for child in base_tmp.iterdir():
            if child.name.startswith("navig_cfg_isolated"):
                shutil.rmtree(
                    child,
                    onexc=_onerror_ignore if sys.version_info >= (3, 12) else None,
                    ignore_errors=True,
                )
    except PermissionError:
        pass  # Best-effort cleanup — proceed anyway

    # Use a temp dir OUTSIDE pytest basetemp for NAVIG_CONFIG_DIR.
    # This avoids pytest's own basetemp recycling from racing with live SQLite
    # handles (vault.db) on Windows between test modules.
    isolated = Path(tempfile.mkdtemp(prefix="navig_cfg_isolated_"))
    old_value = os.environ.get("NAVIG_CONFIG_DIR")
    os.environ["NAVIG_CONFIG_DIR"] = str(isolated)

    # NAVIG_DATA_DIR is NOT derived from NAVIG_CONFIG_DIR. `paths.data_dir()` reads its own
    # env var and otherwise falls back to `home_dir()/".navig"/"data"`, so isolating the
    # config dir alone leaves every database pointing at the operator's REAL ~/.navig/data
    # (690 files here, including live connections.db and contacts.db).
    #
    # Nothing depends on that today — verified by redirecting HOME and running
    # tests/store + tests/memory: 804 passed and ZERO files appeared under the fake home.
    # This is defence in depth, because the identical shape has already bitten: the
    # `temp_home` fixture in test_webserver_autodetect patched HOME and isolated nothing,
    # since hosts_dir comes from app-root detection, so three differently-patched homes all
    # resolved to one shared real directory and the tests raced under -n auto (e1422a253).
    # A fixture that appears to isolate is worse than one that obviously does not.
    #
    # Placed inside `isolated` so it mirrors the production layout (~/.navig + ~/.navig/data)
    # and is discarded with it. Tests that set NAVIG_DATA_DIR themselves still win — this is
    # a floor, not an override.
    old_data = os.environ.get("NAVIG_DATA_DIR")
    isolated_data = isolated / "data"
    isolated_data.mkdir(parents=True, exist_ok=True)
    os.environ["NAVIG_DATA_DIR"] = str(isolated_data)

    # The THIRD leg, and the one no env var can reach: app-root detection.
    #
    # `base_dir` — the parent of hosts_dir / apps_dir / cache_dir — is NOT
    # global_config_dir when the cwd sits inside a navig project. `ConfigManager`
    # asks `paths.find_app_root()`, which walks up from the CWD looking for a
    # `.navig/` directory, and prefers what it finds. pytest runs with the cwd
    # inside THIS checkout, and `core/.navig/` exists — so a bare `ConfigManager()`
    # in a test resolved its state into the SOURCE TREE, shared by every xdist
    # worker. Measured directly, with NAVIG_CONFIG_DIR already pointed at a temp dir:
    #
    #     global_config_dir -> <temp>                     (isolated)
    #     hosts_dir         -> <repo>/core/.navig/hosts   (NOT isolated)
    #
    # That is what e1422a253 hit (12 tests racing for one `test-host` file, failing
    # on a different pair of assertions each run) and it is a class, not an
    # instance. Neither NAVIG_CONFIG_DIR nor NAVIG_DATA_DIR governs it: this
    # fixture's own docstring used to claim otherwise, which is precisely why the
    # gap survived — a fixture that appears to isolate is worse than one that
    # obviously does not.
    #
    # Only detection of the SOURCE CHECKOUT is suppressed. A test that deliberately
    # chdirs into a temp project still gets the real behaviour, because that is real
    # product behaviour worth testing (test_insights_reads_the_ledger_that_is_written
    # and the ops recorder tests depend on it). Writing into the checkout is never
    # what a test wants, so refusing only that is the narrow, correct cut.
    import navig.platform.paths as _paths_mod

    _repo_checkout = Path(__file__).resolve().parents[2]
    _real_find_app_root = _paths_mod.find_app_root

    def _find_app_root_outside_checkout(*args, **kwargs):
        root = _real_find_app_root(*args, **kwargs)
        if root is None:
            return None
        try:
            rel = Path(root).resolve().relative_to(_repo_checkout)
        except ValueError:
            return root  # a real project elsewhere — leave it alone
        # ⚠ `.dev/` is INSIDE the checkout but is gitignored scratch, and this repo
        # redirects pytest's basetemp there — so `tmp_path` is `<checkout>/core/.dev/tmp/...`.
        # Treating "inside the checkout" as "suppress" therefore broke every test that
        # chdirs into a tmp project. Caught by the narrowness test that ships with this,
        # on its first run. Compare `rel.parts`, never a substring of the absolute path
        # (an absolute-path match on "/.dev/" misfires inside a `.dev/worktrees/` checkout,
        # where EVERY path contains it).
        if ".dev" in rel.parts:
            return root
        return None  # the source tree itself — fall back to the isolated global dir

    # ⚠ Deliberately NOT functools.wraps'd. `test_cross_brain_isolation` enumerates
    # `paths.py` with `inspect.getmembers` and filters on `fn.__module__`, so this wrapper
    # is skipped there precisely because it does not claim to be a paths.py function.
    # Copying the original's metadata would make it look native again and put it back into
    # a classification it does not belong in (it is excluded there explicitly too, so this
    # is belt-and-braces — but do not "tidy" it by adding wraps).
    _paths_mod.find_app_root = _find_app_root_outside_checkout

    yield isolated

    _paths_mod.find_app_root = _real_find_app_root

    # -- Cleanup: close Storage Engine connections (Windows file lock fix) --
    try:
        from navig.storage import get_engine

        get_engine().close_all()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass

    # -- Cleanup: close any open vault SQLite connections (Windows file lock fix) --
    try:
        import navig.vault.core as _vault_core_mod

        # Current singleton (post V1/V2 consolidation)
        vault_singleton = getattr(_vault_core_mod, "_vault", None)
        if vault_singleton is not None and hasattr(vault_singleton, "_store"):
            store = vault_singleton._store
            if store is not None:
                store.close()
        _vault_core_mod._vault = None

        # Legacy compatibility singleton (if present in older paths)
        vault_v2 = getattr(_vault_core_mod, "_vault_v2", None)
        if vault_v2 is not None and hasattr(vault_v2, "_store"):
            store = vault_v2._store
            if store is not None:
                store.close()
        if hasattr(_vault_core_mod, "_vault_v2"):
            _vault_core_mod._vault_v2 = None
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass

    # Also close any Storage singleton
    try:
        from navig.vault.storage import Storage

        if hasattr(Storage, "_instance") and Storage._instance is not None:
            Storage._instance._conn.close()
            Storage._instance = None
    except Exception:  # noqa: BLE001
        pass

    # Restore previous values (or remove them if they weren't set before the session).
    if old_value is None:
        os.environ.pop("NAVIG_CONFIG_DIR", None)
    else:
        os.environ["NAVIG_CONFIG_DIR"] = old_value
    if old_data is None:
        os.environ.pop("NAVIG_DATA_DIR", None)
    else:
        os.environ["NAVIG_DATA_DIR"] = old_data


@pytest.fixture(autouse=True)
def _no_env_leaks(_isolate_navig_config_dir):
    """A test must leave ``os.environ`` as it found it.

    A leaked variable is a cross-file failure: it reads as a flake in some OTHER test,
    usually in another directory, and only when xdist happens to put the two in one worker.
    This repo has paid for that three times -- three teardowns that popped
    ``NAVIG_CONFIG_DIR`` (#1125), a ``config_dir`` patch that poisoned every module imported
    inside it (#1143), and eight tests that leaked ``NAVIG_HOME`` / ``NAVIG_DEBUG`` and
    friends (#1156). The worst of them redirected ``navig/memory/paths.py``, which honours
    ``NAVIG_HOME`` as its FIRST precedence rule, at a ``tmp_path`` pytest had already deleted.

    Ordering is load-bearing twice over:

    * it depends on ``_isolate_navig_config_dir`` so the snapshot is taken AFTER the session
      fixture has set ``NAVIG_CONFIG_DIR`` / ``NAVIG_DATA_DIR`` -- otherwise the first test in
      every worker would report the session's own setup as a leak;
    * autouse fixtures are set up before the test's own, so this one finalises LAST, after
      ``monkeypatch`` has undone its changes. A correctly scoped ``monkeypatch.setenv`` /
      ``delenv`` is therefore invisible here, and only a genuine leak is reported.

    ⚠ ``monkeypatch.delenv`` does NOT clean up a variable that was ABSENT: it records nothing
    to restore, so a later direct write survives. Use ``monkeypatch.setenv(name, "")`` (which
    records "was absent", and whose undo deletes) -- and, where the code under test needs the
    variable absent, ``setenv`` followed by ``delenv``.
    """
    # pytest maintains PYTEST_CURRENT_TEST itself, updating it per phase -- it changes on
    # EVERY test and is not the test's doing. Ignoring it is required, not a concession:
    # without this the guard flags all 29,000 tests and is worthless.
    _pytest_owned = {"PYTEST_CURRENT_TEST"}

    # Set ONCE by `navig.cli` on Windows (`os.environ.setdefault`) so stdio is UTF-8 and
    # `ch.success("✓")` does not crash a cp1252 console. That is the product configuring its
    # own process, not a test leaking: `setdefault` makes it idempotent, and the first test to
    # import the CLI triggers it. Only their APPEARANCE is tolerated -- a test that CHANGES or
    # REMOVES one is still reported, because that genuinely alters later tests' subprocess
    # encoding.
    #
    # This was found by the gate, not by a local run: the ambient shell here already had both
    # set, so there was no delta to see. Validate this guard with `env -u PYTHONIOENCODING -u
    # PYTHONUTF8`, or it silently proves nothing.
    _product_owned = {"PYTHONIOENCODING", "PYTHONUTF8"}

    def _snapshot() -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k not in _pytest_owned}

    before = _snapshot()
    yield
    after = _snapshot()

    added = sorted(after.keys() - before.keys() - _product_owned)
    removed = sorted(before.keys() - after.keys())
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    if not (added or removed or changed):
        return

    # Repair before failing: one leaking test must not cascade into every test after it.
    for key in added:
        os.environ.pop(key, None)
    for key in removed:
        os.environ[key] = before[key]
    for key in changed:
        os.environ[key] = before[key]

    detail = []
    if added:
        detail.append("added: " + ", ".join(added))
    if removed:
        detail.append("removed: " + ", ".join(removed))
    if changed:
        detail.append("changed: " + ", ".join(changed))
    raise AssertionError(
        "this test left os.environ modified, which leaks into every later test in this "
        "xdist worker (" + " | ".join(detail) + "). Use monkeypatch.setenv/delenv; note that "
        "delenv on a variable that was ABSENT restores nothing, so a later direct write -- "
        "including one made by the code under test -- survives teardown."
    )


@pytest.fixture(autouse=True)
def _reset_navig_singletons():
    """Reset module-level singletons before and after every test.

    - ConfigManager singleton: prevents a real ConfigManager created by test A
      from being returned by ``get_config_manager()`` in test B.
    - ``navig.platform.paths._DETECTED_OS``: prevents the OS-detection cache
      set during one test from affecting path calculations in subsequent tests.
    """
    try:
        from navig.config import reset_config_manager

        reset_config_manager()
    except Exception:  # noqa: BLE001 — never block test collection
        pass
    try:
        from navig.storage import get_engine

        get_engine().close_all()
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.vault.core as _vault_core

        active_vault = getattr(_vault_core, "_vault", None)
        if active_vault is not None and hasattr(active_vault, "_store"):
            store = active_vault._store
            if store is not None and hasattr(store, "close"):
                store.close()
        _vault_core._vault = None
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.vault.storage import Storage

        if hasattr(Storage, "_instance") and Storage._instance is not None:
            Storage._instance._conn.close()
            Storage._instance = None
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.vault.core as _v2

        _vault_v2 = getattr(_v2, "_vault_v2", None)
        if _vault_v2 is not None and hasattr(_vault_v2, "_store"):
            _store = _vault_v2._store
            if _store is not None:
                _store.close()
        _v2._vault_v2 = None
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.agent.ai_client as _ai_client

        _ai_client.reset_default_ai_client(close_session=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.memory import reset_key_fact_store

        reset_key_fact_store()
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.llm.routing.router import reset_router

        reset_router()
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.platform.paths as _paths

        _paths._DETECTED_OS = None
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from navig.config import reset_config_manager

        reset_config_manager()
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.storage import get_engine

        get_engine().close_all()
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.vault.core as _vault_core

        active_vault = getattr(_vault_core, "_vault", None)
        if active_vault is not None and hasattr(active_vault, "_store"):
            store = active_vault._store
            if store is not None and hasattr(store, "close"):
                store.close()
        _vault_core._vault = None
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.vault.storage import Storage

        if hasattr(Storage, "_instance") and Storage._instance is not None:
            Storage._instance._conn.close()
            Storage._instance = None
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.vault.core as _v2

        _vault_v2 = getattr(_v2, "_vault_v2", None)
        if _vault_v2 is not None and hasattr(_vault_v2, "_store"):
            _store = _vault_v2._store
            if _store is not None:
                _store.close()
        _v2._vault_v2 = None
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.agent.ai_client as _ai_client

        _ai_client.reset_default_ai_client(close_session=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.memory import reset_key_fact_store

        reset_key_fact_store()
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.llm.routing.router import reset_router

        reset_router()
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.platform.paths as _paths

        _paths._DETECTED_OS = None
    except Exception:  # noqa: BLE001
        pass
