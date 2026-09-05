"""
navig doctor — Self-diagnostics (P1-15)

Reports on NAVIG installation health without mutating any state.
Checks: config, cache, vault, formations, skills, gateway, API keys.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

import typer

from navig._daemon_defaults import _DAEMON_PORT, _GATEWAY_PORT
from navig.console_helper import get_console
from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

doctor_app = typer.Typer(
    name="doctor",
    help="Run NAVIG self-diagnostics and report installation health.",
    invoke_without_command=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_OK = "✓"
_WARN = "⚠"
_ERR = "✗"
_INFO = "·"


class CheckResult(tuple):
    """One doctor row.

    Behaves exactly like the historical ``(icon, ok, line)`` 3-tuple — every
    existing consumer unpacks or indexes it — while ALSO carrying the raw
    structured fields (``label``, ``detail``) so ``--json`` can emit data
    instead of scraping glyphs and labels back out of pre-rendered strings.
    The rendered warn state is not stored: it is fully encoded by the icon
    (``row[0] == _WARN``), which is exactly what the human report prints.
    """

    label: str
    detail: str

    def __new__(cls, icon: str, ok: bool, line: str, *, label: str = "", detail: str = ""):
        self = super().__new__(cls, (icon, ok, line))
        self.label = label
        self.detail = detail
        return self


def _check(
    label: str,
    ok: bool,
    detail: str = "",
    warn: bool = False,
) -> CheckResult:
    """Return a formatted result row (a ``(icon, ok, line)`` tuple + structured fields)."""
    if ok:
        icon = _OK
    elif warn:
        icon = _WARN
    else:
        icon = _ERR
    line = f"  {icon} {label}" + (f": {detail}" if detail else "")
    return CheckResult(icon, ok, line, label=label, detail=detail)


# JSON output must carry plain data: strip ANSI escapes and the report glyphs
# from every emitted string (details are already plain text today — this is
# the guard that keeps a future exception message or wrapped value from
# smuggling terminal formatting into a machine-readable payload).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain_text(text: str) -> str:
    text = _ANSI_RE.sub("", str(text))
    for glyph in (_OK, _WARN, _ERR):
        text = text.replace(glyph, "")
    return text.strip()


def _gateway_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Try a TCP connect to the gateway port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _count_yaml_files(directory: Path) -> tuple[int, int]:
    """Return (total, error_count) for YAML files in directory."""
    total = 0
    errors = 0
    if not directory.exists():
        return 0, 0
    try:
        import yaml

        for f in directory.rglob("*.yaml"):
            total += 1
            try:
                yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                errors += 1
        for f in directory.rglob("*.yml"):
            total += 1
            try:
                yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                errors += 1
    except ImportError:
        pass  # optional dependency not installed; feature disabled
    return total, errors


def _runtime_dir() -> Path:
    """Resolve the isolated runtime root (`~/.navig/runtime`).

    Derives it from ``sys.executable`` when we're actually running inside the
    managed venv (``<runtime>/venv/(Scripts|bin)/python``); otherwise falls back
    to the installer's fixed location.
    """
    try:
        exe = Path(sys.executable).resolve()
        # Match the exact managed layout: <runtime>/venv/(Scripts|bin)/python(.exe)
        if (
            exe.parent.name in ("Scripts", "bin")
            and exe.parents[1].name == "venv"
            and exe.parents[2].name == "runtime"
        ):
            return exe.parents[2]
    except Exception:  # noqa: BLE001
        pass  # unexpected interpreter layout; use the default below
    return Path.home() / ".navig" / "runtime"


def _daemon_autostart() -> tuple[bool, str]:
    """Best-effort: is the daemon registered for OS auto-start? (registered, kind)."""
    import subprocess

    try:
        if sys.platform == "win32":
            # No text mode: only the exit status is read, and schtasks writes localized
            # text in the console code page that decoding could only get wrong.
            r = subprocess.run(
                ["schtasks", "/query", "/tn", "NAVIG Daemon"],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0, "Task Scheduler"
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", "navig-agent"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0, "systemd"
    except Exception:  # noqa: BLE001
        return False, ""  # tool missing/timeout — treat as not registered


# ──────────────────────────────────────────────────────────────────────────────
# Individual checks
# ──────────────────────────────────────────────────────────────────────────────


def check_runtime() -> list[tuple[str, bool, str]]:
    """Check the isolated uv-managed runtime: uv, venv, shim/PATH, daemon, skills.

    For dev/editable installs (not running from the managed runtime) the
    runtime-specific checks are skipped so they don't report false failures.
    """
    results: list[tuple[str, bool, str]] = []
    rt = _runtime_dir()
    is_win = os.name == "nt"
    venv_py = rt / "venv" / ("Scripts" if is_win else "bin") / ("python.exe" if is_win else "python")

    try:
        in_runtime = str(Path(sys.executable).resolve()).startswith(str(rt.resolve()))
    except Exception:  # noqa: BLE001
        in_runtime = False

    managed = in_runtime or venv_py.exists()
    if not managed:
        results.append(_check("install type", True, f"dev/editable ({sys.executable}) — runtime checks skipped"))
    else:
        uv_exe = rt / ("uv.exe" if is_win else "uv")
        results.append(_check("uv engine", uv_exe.exists(), str(uv_exe) if uv_exe.exists() else "missing from runtime", warn=True))
        results.append(_check("isolated venv", venv_py.exists(), str(rt / "venv") if venv_py.exists() else "missing", warn=True))
        results.append(_check("running from runtime", in_runtime, sys.executable, warn=True))
        shim = Path.home() / ".local" / "bin" / ("navig.cmd" if is_win else "navig")
        results.append(_check("launcher shim", shim.exists(), str(shim) if shim.exists() else "missing", warn=True))
        on_path = str(shim.parent) in os.environ.get("PATH", "").split(os.pathsep)
        results.append(_check("shim dir on PATH", on_path, "" if on_path else "open a new shell / re-run install", warn=True))
        registered, kind = _daemon_autostart()
        results.append(_check("daemon auto-start", registered, kind if registered else "not registered — run 'navig service install'", warn=True))

    try:
        from navig.platform.paths import store_dir
        skills_dir = store_dir() / "skills"
    except Exception:  # noqa: BLE001
        skills_dir = config_dir() / "data" / "store" / "skills"
    n = sum(1 for _ in skills_dir.glob("*/SKILL.md")) if skills_dir.exists() else 0
    results.append(_check("installed skills", True, f"{n} in {skills_dir}"))
    return results


def check_config() -> list[tuple[str, bool, str]]:
    """Check global config.yaml."""
    results = []
    config_path = config_dir() / "config.yaml"

    if not config_path.exists():
        results.append(_check("Config file", False, f"{config_path} not found"))
        return results

    try:
        import yaml

        content = config_path.read_text(encoding="utf-8", errors="replace")
        cfg = yaml.safe_load(content) or {}
        version = cfg.get("version", "missing")
        results.append(_check("Config file", True, f"{config_path} (v{version}, valid YAML)"))
    except Exception as e:
        results.append(_check("Config file", False, f"YAML error in {config_path}: {e}"))

    return results


def check_logs() -> list[tuple[str, bool, str]]:
    """Report where the daemon is ACTUALLY writing, not where logs are meant to be.

    This row exists because of a real, expensive misdiagnosis. `navig.log` lives in
    `config_dir()`, and `config_dir()` becomes the PROJECT `.navig/` whenever the
    process's cwd is inside a tree that has a `.navig/` of its own -- so the file follows whatever directory
    the daemon happened to be launched from. Seven of them existed on one machine.
    The one an operator naturally opens, `~/.navig/navig.log`, showed EIGHT DAYS with
    zero lines and read exactly like a dead or log-blind daemon, while the daemon was
    running fine and logging into the desktop app's workspace, and a cron job ran an
    hour after that file's last entry.

    So this does not print where the log SHOULD be. It asks the running process --
    via its own open file handles, falling back to its cwd -- and prints what it finds.

    Honesty rule (the doctor rule): "I could not look" is never a green tick. No
    daemon, no psutil, an unreadable process -> warn and say which.
    """
    results: list[tuple[str, bool, str]] = []

    from navig.platform import paths

    # 1. The supervisor's own logs. These DO use the canonical OS location, and are
    #    the reliable ones: daemon.log is what records child deaths and orphan sweeps.
    log_dir = paths.log_dir()
    daemon_log = log_dir / "daemon.log"
    if daemon_log.is_file():
        age_min = (time.time() - daemon_log.stat().st_mtime) / 60
        age = f"{age_min:.0f}m ago" if age_min < 90 else f"{age_min / 60:.1f}h ago"
        results.append(
            _check("supervisor logs", True, f"{log_dir} (daemon.log last written {age})")
        )
    else:
        results.append(
            _check("supervisor logs", False, f"{daemon_log} not found", warn=True)
        )

    # 2. The live daemon's app log -- the one that moves.
    pid = None
    try:
        from navig.daemon.supervisor import NavigDaemon

        if NavigDaemon.is_running():
            pid = NavigDaemon.read_pid()
    except Exception as exc:  # noqa: BLE001 - a probe must not break doctor
        results.append(_check("daemon log path", False, f"could not read the pid file: {exc}", warn=True))
        return results

    if not pid:
        results.append(
            _check(
                "daemon log path",
                False,
                "daemon is not running, so its live log location is unknown "
                f"(when stopped it would use {paths.config_dir() / 'navig.log'})",
                warn=True,
            )
        )
        return results

    try:
        import psutil  # noqa: PLC0415 - optional, and only needed for this row
    except ImportError:
        results.append(
            _check("daemon log path", False, "psutil not installed - cannot ask the process", warn=True)
        )
        return results

    try:
        proc = psutil.Process(pid)
        # The SUPERVISOR does not open navig.log -- its gateway child does. Asking only
        # the supervisor finds nothing and invites a guess; asking the children finds
        # the real file. (Measured: supervisor 113012 held daemon.log/gateway.log,
        # gateway 79708 held the navig.log that was actually being written.)
        live = None
        for candidate in [proc, *proc.children(recursive=True)]:
            try:
                for handle in candidate.open_files():
                    if handle.path.endswith("navig.log"):
                        live = handle.path
                        break
            except Exception:  # noqa: BLE001, PERF203 - a child may exit mid-scan
                continue
            if live:
                break
    except Exception as exc:  # noqa: BLE001
        results.append(
            _check("daemon log path", False, f"could not inspect pid {pid}: {exc}", warn=True)
        )
        return results

    if live is None:
        # DO NOT GUESS. Deriving a path from the cwd produced
        # `~/.navig/.navig/navig.log` on the first run of this check -- a path nothing
        # writes. Printing an invented location is the very defect this row exists to
        # prevent, so say what is known (the cwd) and admit the rest.
        try:
            cwd = proc.cwd()
        except Exception:  # noqa: BLE001
            cwd = "unknown"
        results.append(
            _check(
                "daemon log path",
                False,
                f"no navig.log is open by pid {pid} or its children "
                f"(daemon cwd: {cwd}) - it may not have logged yet",
                warn=True,
            )
        )
        return results

    expected = paths.config_dir() / "navig.log"
    if Path(live).resolve() == expected.resolve():
        results.append(_check("daemon log path", True, str(live)))
    else:
        # Not an error -- this is legitimate and cwd-driven. But it is the exact thing
        # that made a healthy daemon look dead, so it must be SAID rather than implied.
        results.append(
            _check(
                "daemon log path",
                False,
                f"{live} - NOT {expected}. `navig.log` follows the "
                "daemon's working directory; read the path above, not the default one",
                warn=True,
            )
        )
    return results


def check_cache_dir() -> list[tuple[str, bool, str]]:
    """Check cache directory is writable."""
    results = []
    cache_dir = config_dir() / "cache"

    if not cache_dir.exists():
        results.append(_check("Cache dir", False, f"{cache_dir} does not exist", warn=True))
        return results

    test_file = cache_dir / ".write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        results.append(_check("Cache dir", True, f"{cache_dir} (writable)"))
    except Exception as e:
        results.append(_check("Cache dir", False, f"Not writable: {e}"))

    return results


def check_storage() -> list[tuple[str, bool, str]]:
    """Check if the system has enough free disk space for NAVIG databases and operations."""
    import shutil

    results = []

    navig_dir = config_dir()
    navig_dir.mkdir(exist_ok=True, parents=True)

    try:
        usage = shutil.disk_usage(navig_dir)
        free_gb = usage.free / (1024**3)

        # Invariant: Must have at least 1GB free to safely run SQLite WAL, migrations, and model caches.
        if free_gb < 1.0:
            results.append(
                _check(
                    "Disk Space",
                    False,
                    f"FATAL: Only {free_gb:.2f}GB free. NAVIG requires >1GB to prevent DB corruption.",
                )
            )
        elif free_gb < 5.0:
            results.append(
                _check(
                    "Disk Space",
                    False,  # a Low-Space WARNING must render ⚠, not a green ✓ (warn is ignored when ok=True)
                    f"Low Space Warning: {free_gb:.2f}GB free. Consider cleanup.",
                    warn=True,
                )
            )
        else:
            results.append(_check("Disk Space", True, f"{free_gb:.1f}GB free (OK)"))
    except Exception as e:
        results.append(_check("Disk Space", False, f"Failed to stat volume: {e}"))

    return results


# ── Database integrity ────────────────────────────────────────────────────────
# NAVIG's own stores sit at the config-dir root and one level under it (data/,
# memory/, credentials/, bot/ …). The scan is bounded to those two levels and skips
# copies + scratch trees, so a doctor run can never wander into a space or cache tree
# holding an unbounded number of databases.
_DB_SCAN_SKIP = {".backup", "backups", "cache", "spaces", "runtime", ".git", "trash"}
_DB_SCAN_BUDGET_S = 8.0

# The unsafe-FTS-trigger defect is repaired by the owning store's schema init, which runs
# when that store is next opened — so the remedy is "open this store", and the operator
# needs to be told HOW. Each command below is a READ-ONLY verb chosen because it opens the
# store and changes nothing else; a remedy that also mutates data would be a poor thing to
# print in a diagnostic. Pinned by tests/cli/test_doctor_fts_remedy.py, which asserts every
# command here actually resolves in the CLI — a remedy naming a command that no longer
# exists is worse than the generic sentence it replaced.
_FTS_REPAIR_COMMAND = {
    "links.db": "navig links list",
    "knowledge_graph.db": "navig kg status",
    "index.db": "navig memory bank",
}


def _fts_repair_hint(offenders: list[str]) -> str:
    """The concrete command(s) that repair the offending stores, newest advice first.

    Falls back to naming the store when the database is not one we know a verb for:
    "reopen <name>" is still more actionable than "reopen the owning store", and it never
    invents a command that does not exist.
    """
    cmds: list[str] = []
    unknown: list[str] = []
    for offender in offenders:
        db = offender.split(":", 1)[0]
        cmd = _FTS_REPAIR_COMMAND.get(db)
        if cmd and cmd not in cmds:
            cmds.append(cmd)
        elif not cmd and db not in unknown:
            unknown.append(db)
    parts = []
    if cmds:
        parts.append("repair by opening the store: " + " ; ".join(cmds))
    if unknown:
        parts.append("reopen the owning store for " + ", ".join(unknown))
    return " — ".join(parts) if parts else "reopening the owning store repairs it"


def _local_databases() -> list[Path]:
    """The operator's own SQLite stores, bounded to two levels under the config dir."""
    root = config_dir()
    found = [*root.glob("*.db"), *root.glob("*/*.db")]
    return sorted({p for p in found if not (_DB_SCAN_SKIP & set(p.parts))})


def _external_content_fts_misuse(con: Any) -> list[str]:
    """External-content FTS5 indexes whose sync triggers use plain DML.

    An fts5 table declared ``content=<table>`` stores no copy of the text, so its index
    may only be maintained with the command syntax
    (``INSERT INTO t(t) VALUES('delete', …)`` then a fresh insert). A plain
    ``UPDATE t SET …`` / ``DELETE FROM t …`` inside a sync trigger silently desyncs the
    index and eventually raises "database disk image is malformed" — exactly what broke
    editing a bookmark twice (#530c0a21).

    This reads schema only. That is deliberate: it reports the defect BEFORE any data is
    damaged, and it needs no write access — fts5's own ``'integrity-check'`` is issued as
    an INSERT and cannot run on the read-only connection doctor uses.
    """
    fts = {
        name: (sql or "")
        for name, sql in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%USING fts5%'"
        ).fetchall()
    }
    # content='' is CONTENTLESS (a different, legal mode) — only content=<table> is external.
    external = {n for n, sql in fts.items() if re.search(r"content\s*=\s*'?[A-Za-z_]", sql)}
    if not external:
        return []
    offenders: set[str] = set()
    for _tname, tsql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall():
        for table in external:
            if re.search(rf"\bUPDATE\s+{re.escape(table)}\b", tsql or "", re.I) or re.search(
                rf"\bDELETE\s+FROM\s+{re.escape(table)}\b", tsql or "", re.I
            ):
                offenders.add(table)
    return sorted(offenders)


def check_databases() -> list[tuple[str, bool, str]]:
    """Integrity of the local SQLite stores — read-only, never mutating.

    Two independent signals:
      * ``PRAGMA quick_check`` — page-level corruption.
      * an external-content FTS5 trigger audit — the schema defect that corrupts a
        search index over time (see :func:`_external_content_fts_misuse`).

    ``quick_check`` deliberately, not ``integrity_check``: it skips the expensive
    index-vs-table cross-check, and the whole sweep is capped by ``_DB_SCAN_BUDGET_S``
    so a large install can never make ``navig doctor`` hang.
    """
    import sqlite3
    import time

    databases = _local_databases()
    if not databases:
        # Nothing found is not "healthy" — it means the scan learned nothing.
        return [_check("Database integrity", False, "no local databases found to check", warn=True)]

    deadline = time.monotonic() + _DB_SCAN_BUDGET_S
    checked = 0
    corrupt: list[str] = []
    unreadable: list[str] = []
    fts_defects: list[str] = []
    skipped = 0

    for path in databases:
        if time.monotonic() > deadline:
            skipped = len(databases) - checked - len(unreadable)
            break
        try:
            # mode=ro: doctor must never create, migrate or write a live store.
            # busy_timeout: these are the operator's LIVE stores, so the daemon may hold a
            # write lock as we look. Without it a momentary lock raises instantly and the
            # store is reported "unreadable" — a health warning about a perfectly healthy
            # database, which is exactly the noise that trains you to skim past this row.
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            con.execute("PRAGMA busy_timeout=5000")
        except sqlite3.DatabaseError as exc:
            unreadable.append(f"{path.name} ({exc})")
            continue
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            verdict = (row[0] if row else "") or ""
            if verdict.lower() != "ok":
                corrupt.append(f"{path.name}: {verdict.splitlines()[0]}")
            for table in _external_content_fts_misuse(con):
                fts_defects.append(f"{path.name}:{table}")
            checked += 1
        except sqlite3.DatabaseError as exc:
            unreadable.append(f"{path.name} ({exc})")
        finally:
            con.close()

    if corrupt:
        return [
            _check(
                "Database integrity",
                False,
                f"{len(corrupt)} corrupt: {'; '.join(corrupt[:3])}"
                + (" …" if len(corrupt) > 3 else "")
                # `navig db backup` has never existed, and the local-store command that
                # does (`navig db local backup <dir>`) WRITES backups rather than listing
                # them — so the old text sent an operator whose database is corrupt, the
                # worst moment to be misdirected, after a command that errors.
                + " — no automatic restore: put a known-good copy of the file back, then"
                + " verify with navig db local status (navig db local backup <dir> writes"
                + " fresh copies of the stores that are still healthy)",
            )
        ]
    if fts_defects:
        # A schema defect, not damage yet: the index desyncs and only later errors.
        return [
            _check(
                "Database integrity",
                False,
                f"{checked} DB(s) intact, but {len(fts_defects)} search index(es) use "
                f"unsafe external-content triggers: {', '.join(fts_defects[:3])}"
                + (" …" if len(fts_defects) > 3 else "")
                + " — "
                + _fts_repair_hint(fts_defects),
                warn=True,
            )
        ]
    if unreadable or skipped:
        # Could-not-look is never a green tick.
        parts = []
        if unreadable:
            parts.append(f"{len(unreadable)} unreadable ({'; '.join(unreadable[:2])})")
        if skipped:
            parts.append(f"{skipped} skipped (budget {_DB_SCAN_BUDGET_S:.0f}s)")
        return [
            _check("Database integrity", False, f"{checked} of {len(databases)} verified — "
                   + ", ".join(parts), warn=True)
        ]
    return [_check("Database integrity", True, f"{checked} databases intact (quick_check + FTS audit)")]


def check_vault() -> list[tuple[str, bool, str]]:
    """Local vault health — no daemon required, no secrets, no mutation.

    Counts items and probes that ONE item's key wrapper actually decrypts,
    through the vault's public API. Only COUNTS ever reach the output: labels,
    providers and payloads must never appear in a diagnostic (doctor output
    gets copy-pasted into issues and logs).

    Every path resolves at CALL time (``NAVIG_CONFIG_DIR`` honoured when the
    check runs) — the lesson from the legacy-migration leak, where an
    import-time path constant silently pointed vault code at the REAL user
    home (see ``navig/vault/migrate.py:_legacy_db_path``). Deliberately avoids
    ``get_vault()``: the singleton triggers auto-migration (a mutation) and
    freezes its first-resolved path for the rest of the process.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        from navig.platform.paths import vault_dir as _vault_dir_fn
        from navig.vault.migrate import check_legacy_exists, legacy_migration_done

        vdir = _vault_dir_fn()
        db_path = vdir / "vault.db"

        if not db_path.exists():
            results.append(
                _check("Vault", True, "no vault yet — created on first `navig vault set`")
            )
        else:
            try:
                from navig.vault.core import Vault
                from navig.vault.crypto import CryptoEngine, CryptoError

                vault = Vault(vdir)
                n = vault.count()
                detail = f"{n} item(s)"
                if n:
                    if not (vdir / CryptoEngine.SALT_FILE).exists():
                        # derive_key() would CREATE a fresh salt — a mutation, and
                        # one that can never decrypt the existing items.
                        raise CryptoError("salt file missing")
                    probe = next((i for i in vault.store().list() if i.encrypted_dek), None)
                    if probe is not None:
                        # Open one item's DEK *wrapper* only — never the payload.
                        CryptoEngine.open(vault.engine().derive_key(None), probe.encrypted_dek)
                        detail += " · encryption OK"
                results.append(_check("Vault", True, detail))
            except Exception as exc:  # noqa: BLE001
                # Exception CLASS only: messages can embed paths or item labels.
                results.append(_check("Vault", False, f"cannot open vault ({type(exc).__name__})"))

        if check_legacy_exists():
            if legacy_migration_done(vdir):
                results.append(_check("Legacy credentials", True, "legacy DB retained (migrated)"))
            else:
                results.append(
                    _check(
                        "Legacy credentials",
                        False,
                        "legacy credentials DB present — will auto-migrate on next vault use",
                        warn=True,
                    )
                )
    except Exception as exc:  # noqa: BLE001 — a doctor check must never crash doctor
        results.append(_check("Vault", False, f"COULD NOT VERIFY ({type(exc).__name__})", warn=True))

    return results


def _bindability_row(label: str, port: int) -> CheckResult:
    """Answer "could a daemon actually start here?" — only call when nothing is LISTENING.

    `connect_ex` answers a different question ("is someone listening"), and the two come
    apart exactly where it matters: **Windows RESERVES port ranges** (Hyper-V / WSL /
    Docker) in which `bind()` raises PermissionError while nothing is listening and
    `Get-NetTCPConnection` cheerfully reports the port free. Measured on this machine:
    8680-8779, 8790-8889, 9011-9110 and 9181-9580 are all reserved, `_DAEMON_PORT` (8765)
    sits inside the first, and `navig cdp new` once failed to allocate ANY port because
    its whole 9222+ window was swallowed.

    SO_REUSEADDR is set deliberately: it mirrors what the real binders do, so the probe
    predicts what a daemon start will actually see rather than raising a false alarm on a
    POSIX socket sitting in TIME_WAIT. It cannot mask a reservation — those refuse the
    bind either way — and the "someone is listening" case is handled by the caller before
    this is reached.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        hint = (
            " — check `netsh interface ipv4 show excludedportrange protocol=tcp`"
            if os.name == "nt"
            else ""
        )
        # NOT ok: nothing is listening AND nothing can bind, so this port is unusable.
        # A ✓ here would be the "green light over an unknown" this file was hardened
        # against. warn= rather than error: NAVIG self-heals onto a free port, so the
        # install still works — the operator just needs to know why the port moved.
        return _check(
            label,
            False,
            f"Port {port} is free but NOT bindable ({exc.strerror or exc}){hint}",
            warn=True,
        )
    return _check(label, True, f"Port {port} is free and bindable")


# cmd.exe truncates PATH at 8191 characters. Past that EVERY command resolved through a
# shell dies with "'x' is not recognized" — naming a tool that IS installed and whose
# directory IS on PATH, which is why the error sends you reinstalling node_modules instead
# of looking at the environment. Measured on the operator's machine: PATH 6698 chars, and a
# `tauri dev` chain (three nested `npm run` levels, each prepending ~7 node_modules/.bin
# dirs) landed it at 8022 — 169 from the ceiling, with `vite` already unresolvable one hop
# deeper inside npx. The nesting cost that chain +1324 chars, which is where the warn
# threshold below comes from: less than that in reserve and an ordinary nested toolchain
# can cross the line.
_CMD_PATH_LIMIT = 8191
_PATH_NESTING_RESERVE = 2000  # > the +1324 a measured 3-level npm chain adds


def partition_path_entries(entries: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split PATH entries into what resolves something and what does not.

    Returns ``(kept, removed)``; each removed item is ``(entry, reason)`` with reason
    ``"missing"`` or ``"duplicate"``. Deliberately only those two classes: neither can
    affect command resolution, so pruning them needs no judgement about what the operator
    "still uses" — which is what makes an automated rewrite of their PATH defensible.

    Comparison is case-insensitive and ignores a trailing separator, because ``C:\\Foo``
    and ``c:\\foo\\`` are the same directory; counting them as two would invent
    reclaimable space that deleting cannot actually recover.
    """
    kept: list[str] = []
    removed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        key = entry.strip().rstrip("\\/").lower()
        if key in seen:
            removed.append((entry, "duplicate"))
            continue
        seen.add(key)
        try:
            exists = Path(os.path.expandvars(entry)).is_dir()
        except OSError:
            exists = False
        if exists:
            kept.append(entry)
        else:
            removed.append((entry, "missing"))
    return kept, removed


def _user_path_from_registry() -> str | None:
    """The PERSISTENT user PATH — the only PATH `navig doctor clean-path` can rewrite.

    ``None`` when it cannot be read, so a caller can tell "nothing to reclaim" from "I did
    not manage to look" and refrain from claiming either. Best-effort by contract: a health
    check must never crash the doctor, and a registry that will not open is a reason to say
    less, not to fail the run.

    Deliberately separate from the read inside ``clean_path_cmd``: that one also needs the
    value KIND in order to write it back, and must fail loudly rather than quietly do
    nothing. Same key, different obligations.
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            raw, _kind = winreg.QueryValueEx(key, "Path")
        return str(raw)
    except Exception:  # noqa: BLE001 — see contract above; never crash the doctor
        return None


def check_path_health() -> list[tuple[str, bool, str]]:
    """PATH headroom, and how much of it is reclaimable dead/duplicate entries.

    Windows only: the 8191 ceiling is a cmd.exe property, so on POSIX this contributes
    no row rather than a green one about a limit that does not exist there.
    """
    if os.name != "nt":
        return []

    raw = os.environ.get("PATH")
    if not raw:
        # Cannot look ⇒ warn, never ✓. See tests/cli/test_doctor_honesty.py.
        return [_check("PATH health", False, "PATH is empty or could not be read", warn=True)]

    # Headroom is measured on THIS PROCESS's PATH, because that is the string a shell
    # actually has to resolve against — it is what breaks.
    used = len(raw)
    headroom = _CMD_PATH_LIMIT - used

    # Reclaimable space is measured on the PERSISTENT USER PATH instead, because that is
    # the only thing `clean-path` can rewrite. A shell can inject entries into its own
    # environment that no command can remove — npm prepends ~7 node_modules/.bin dirs per
    # nesting level, and this row's own home is a session whose PATH carries 11 duplicates
    # the registry does not have. Counting those as reclaimable sent the operator to a
    # cleanup that then correctly answered "already clean": advice that is a dead end.
    waste = ""
    persistent = _user_path_from_registry()
    if persistent:
        _kept, removed = partition_path_entries(
            [e for e in persistent.split(os.pathsep) if e.strip()]
        )
        if removed:
            dead = sum(1 for _e, reason in removed if reason == "missing")
            dupes = sum(1 for _e, reason in removed if reason == "duplicate")
            reclaimable = sum(len(entry) + 1 for entry, _reason in removed)
            waste = (
                f"; {dead} missing + {dupes} duplicate entries in your user PATH hold "
                f"{reclaimable} chars (reclaim: navig doctor clean-path)"
            )

    if headroom <= 0:
        return [
            _check(
                "PATH health",
                False,
                f"PATH is {used} chars — OVER cmd.exe's {_CMD_PATH_LIMIT} limit, so "
                f"shell command resolution is already truncated{waste}",
            )
        ]
    if headroom < _PATH_NESTING_RESERVE:
        return [
            _check(
                "PATH health",
                False,
                f"PATH is {used} chars — only {headroom} under cmd.exe's "
                f"{_CMD_PATH_LIMIT} limit, and nested tooling adds ~1300{waste}",
                warn=True,
            )
        ]
    return [
        _check(
            "PATH health",
            True,
            f"PATH is {used} chars, {headroom} under the {_CMD_PATH_LIMIT} limit{waste}",
        )
    ]


def check_sockets(target_port: int | None = None) -> list[tuple[str, bool, str]]:
    """Check if critical ports are available or correctly bound."""
    results = []

    # Bind-side semantics: "could a NEW daemon bind here?" — so resolve the
    # CONFIGURED port (config is canonical for binding, unlike check_gateway
    # which follows the live self-healed port).
    if target_port is None:
        try:
            from navig.gateway_client import gateway_cli_defaults

            target_port = gateway_cli_defaults()[0]
        except Exception:  # noqa: BLE001
            target_port = _GATEWAY_PORT

    # Try binding to see if the port is strictly available for a new daemon.
    # If it's not available, it should be the running gateway.
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            ans = s.connect_ex(("127.0.0.1", target_port))
            if ans == 0:
                results.append(
                    _check(
                        "Port Occupation",
                        True,
                        # A bound port means the gateway is running — genuinely healthy, so
                        # this stays ✓. (warn= was dead here anyway: it is ignored when ok=True.)
                        f"Port {target_port} is bound (Gateway running)",
                    )
                )
            else:
                # Nothing is LISTENING — which is not the same as "a daemon could start
                # here". This row used to stop at connect_ex and disclaim the difference
                # in prose ("OS-reserved ranges may still block binding"), i.e. a ✓ over
                # an unknown. It is answerable: attempt the bind.
                results.append(_bindability_row("Port Occupation", target_port))
    except Exception as e:
        results.append(_check("Port Occupation", False, f"Socket error on port {target_port}: {e}"))

    # The gateway is not the only port NAVIG binds. `_DAEMON_PORT` carries the IPC/MCP
    # WebSocket server, and a reserved range that swallows it is just as fatal and just as
    # invisible — on this developer's machine 8765 sits inside a Hyper-V reservation while
    # 8789 survives only because it lands in a one-port gap between two of them. Checking
    # one port and calling the row "Network Sockets" protected a path, not the surface.
    if _DAEMON_PORT != target_port:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", _DAEMON_PORT)) == 0:
                    results.append(
                        _check("Daemon Port", True, f"Port {_DAEMON_PORT} is bound (daemon running)")
                    )
                else:
                    results.append(_bindability_row("Daemon Port", _DAEMON_PORT))
        except Exception as e:  # noqa: BLE001
            results.append(
                _check("Daemon Port", False, f"Socket error on port {_DAEMON_PORT}: {e}")
            )

    return results


def check_formations() -> list[tuple[str, bool, str]]:
    """Check formations: count what the loader actually discovers.

    The old check counted ``*.yaml`` under ``config_dir()/formations`` — but
    formations are ``formation.json`` DIRS, and installs land under
    ``store_dir()/formations`` (#373). So it read the wrong dir with the wrong
    file type and effectively always showed 0. ``discover_formations()`` is the
    loader's real discovery (builtin + user store + config + plugins), so the
    count matches what actually loads.

    It also surfaces any BROKEN ``formation.json`` (``formation_load_errors()``):
    the loader silently skips a malformed manifest, so a count of only the good
    ones would be a green light over a broken formation — the honesty rule forbids it.
    """
    results = []
    try:
        from navig.formations.loader import discover_formations, formation_load_errors

        n = len(discover_formations())
        errors = formation_load_errors()
    except Exception as e:  # noqa: BLE001
        results.append(_check("Formations", False, f"discovery failed: {e}", warn=True))
        return results

    # `_check` renders ✓ whenever ok=True regardless of warn, and a green light over a
    # broken/empty store is exactly what the doctor honesty rule forbids — so every
    # not-fully-healthy branch here is ok=False (⚠).
    if n > 0:
        results.append(_check("Formations", True, f"{n} discovered"))
    elif errors:
        # 0 loaded but files ARE present and broken — NOT a missing-store situation.
        results.append(
            _check("Formations", False, f"0 loaded — {len(errors)} broken (see below)", warn=True)
        )
    else:
        # Builtins ship with NAVIG, so 0 discoverable AND nothing broken means the
        # builtin store is likely missing.
        results.append(
            _check("Formations", False, "0 discovered — builtin store may be missing", warn=True)
        )

    # Surface each BROKEN formation.json — discover_formations silently skips these, so a
    # count of only the good ones would hide them behind a green tick.
    for subdir, reason in errors:
        results.append(_check("Formations", False, f"{subdir.name}: {reason}", warn=True))

    return results


def check_skills() -> list[tuple[str, bool, str]]:
    """Check skills: count + parse errors."""
    results = []

    # Skills live inside the installed package
    try:
        import navig

        pkg_dir = Path(navig.__file__).parent.parent
        skills_dir = pkg_dir / "skills"
        if not skills_dir.exists():
            # Try navig-core shipped skills
            skills_dir = pkg_dir.parent / "skills"
    except Exception:
        skills_dir = None

    if skills_dir and skills_dir.exists():
        total, errors = _count_yaml_files(skills_dir)
        if errors:
            results.append(_check("Skills", False, f"{total} found, {errors} invalid"))
        else:
            results.append(_check("Skills", True, f"{total} found, 0 invalid"))
    else:
        results.append(_check("Skills", False, "Skills dir not found (non-fatal)", warn=True))

    return results


def check_gateway(port: int | None = None) -> list[tuple[str, bool, str]]:
    """Check if the gateway is running — on its LIVE port, not a guessed one."""
    results = []

    # Live-first: follow the self-healed port from ~/.navig/gateway.json when
    # the gateway is up (the configured port can be swallowed by reserved OS
    # ranges); fall back to config / the canonical default otherwise.
    if port is None:
        try:
            from navig.gateway_client import gateway_live_defaults

            port = gateway_live_defaults()[0]
        except Exception:  # noqa: BLE001
            port = _GATEWAY_PORT

    reachable = _gateway_reachable("127.0.0.1", port)
    if reachable:
        results.append(_check("Gateway", True, f"Responding at 127.0.0.1:{port}"))
    else:
        results.append(
            _check(
                "Gateway",
                False,
                f"No response at 127.0.0.1:{port} (start with: navig service start)",
                warn=True,
            )
        )

    # The mesh token is minted by the gateway on start — it belongs here, not under
    # AI provider keys (where it used to sit purely because both were "secrets").
    #
    # It lives in CONFIG (`gateway.mesh_token` — see GatewayServer._ensure_mesh_token),
    # NOT in a file. This check used to stat `<config_dir>/cache/mesh_token`, a path
    # nothing has ever written, so it warned "not found (generated on gateway start)"
    # on every single run — including on a healthy install whose token was present all
    # along. A warning that can never go green is worse than no warning: it teaches the
    # operator to skim past the one row that might have mattered.
    token = ""
    try:
        from navig.config import ConfigManager

        gw = (ConfigManager().global_config or {}).get("gateway", {}) or {}
        token = str(gw.get("mesh_token", "") or "").strip()
    except Exception:  # noqa: BLE001 — a health check must never crash the doctor
        token = ""

    if not token:  # legacy installs may still carry the old sidecar file
        legacy = config_dir() / "cache" / "mesh_token"
        try:
            if legacy.exists() and legacy.stat().st_size > 0:
                token = legacy.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass

    if token:
        results.append(_check("MESH_TOKEN", True, "present (gateway.mesh_token)"))
    else:
        results.append(
            _check("MESH_TOKEN", False, "not set (minted on the next gateway start)", warn=True)
        )

    return results


# Pending events before the Event processor row warns about a growing backlog.
_EVENTS_BACKLOG_WARN = 50

# How long to wait on /api/deck/status. 3s was too tight: a gateway that is up but still
# warming (plugins + 99 skills load on boot) blew the deadline, and the row then reported
# a cheerful ✓ "not checked" — a green light over a check that never ran.
_STATUS_TIMEOUT = 8.0


def check_event_processor(port: int | None = None) -> list[tuple[str, bool, str]]:
    """Is the gateway's system-event processor actually draining events?

    Regression guard for the silent failure fixed in the gateway boot path:
    ``SystemEventQueue.start()`` was never called, so every emitted event
    (board_update, council_update, …) piled up undrained — 2,737 accumulated —
    while ``/api/events`` served heartbeats only and every light stayed green.
    Reads the additive ``events`` block on ``GET /api/deck/status``; a local
    request rides the deck's loopback desktop bypass, so no credential needed.
    """
    if port is None:
        try:
            from navig.gateway_client import gateway_live_defaults

            port = gateway_live_defaults()[0]
        except Exception:  # noqa: BLE001
            port = _GATEWAY_PORT

    if not _gateway_reachable("127.0.0.1", port):
        # The Gateway row already fails loudly when the daemon is down — this
        # daemon-dependent row degrades to "not checked" (like Reachability).
        return [_check("Event processor", False, "not checked — gateway not running", warn=True)]

    try:
        import requests

        from navig.gateway_client import gateway_request_headers

        resp = requests.get(
            f"http://127.0.0.1:{port}/api/deck/status",
            headers=gateway_request_headers(),
            timeout=_STATUS_TIMEOUT,
        )
        if resp.status_code != 200:
            return [
                _check(
                    "Event processor",
                    False,
                    f"COULD NOT VERIFY (status endpoint returned HTTP {resp.status_code})",
                    warn=True,
                )
            ]
        events = (resp.json() or {}).get("events")
    except Exception as exc:  # noqa: BLE001 — a doctor check must never crash doctor
        return [_check("Event processor", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not isinstance(events, dict):
        return [
            _check(
                "Event processor",
                False,
                "COULD NOT VERIFY — daemon does not expose event stats (older core); "
                "restart the gateway after upgrading",
                warn=True,
            )
        ]

    try:
        running = bool(events.get("running"))
        pending = int(events.get("pending") or 0)
        history = int(events.get("history") or 0)
    except (TypeError, ValueError):
        return [_check("Event processor", False, "COULD NOT VERIFY (malformed event stats)", warn=True)]

    if not running:
        return [
            _check(
                "Event processor",
                False,
                f"NOT RUNNING — emitted events are piling up undrained ({pending} pending). "
                "Restart the gateway; if it persists, the processor failed to start "
                "(check logs for 'System event processor failed to start')",
            )
        ]
    if pending >= _EVENTS_BACKLOG_WARN:
        return [
            _check(
                "Event processor",
                False,
                f"running but {pending} pending — emit backlog growing "
                f"(warn threshold {_EVENTS_BACKLOG_WARN})",
                warn=True,
            )
        ]
    return [_check("Event processor", True, f"running · {pending} pending · {history} in history")]


def check_ai_providers() -> list[tuple[str, bool, str]]:
    """What AI the brain can ACTUALLY use.

    This check used to look only at three environment variables (plus a flat
    ``config.yaml`` key that nothing writes). That is not where NAVIG keeps provider
    auth: `navig connect add` and `navig vault` put keys in the **vault / auth
    profiles**, and a Claude Pro/Max subscription is an **OAuth token, not a key at
    all**. So doctor confidently reported "OPENAI_API_KEY: some models unavailable"
    at a user whose OpenAI key was sitting in their vault, and "Claude models
    unavailable" at a user with three working Claude connections.

    Resolve through the SAME path every other consumer uses (env → vault → auth
    profiles), and report the Connections the brain would actually route through.
    """
    results: list[tuple[str, bool, str]] = []

    # ── 1. Provider keys, resolved the way `navig ai` resolves them ──────────
    configured: list[str] = []
    try:
        from navig.providers import AuthProfileManager
        from navig.providers.connect import CONNECTION_TEMPLATES

        auth = AuthProfileManager()
        provider_ids = sorted(
            {t.provider_id for t in CONNECTION_TEMPLATES.values() if t.provider_id}
        )
        for pid in provider_ids:
            try:
                key, source = auth.resolve_auth(pid)
            except Exception:  # noqa: BLE001 — one bad provider must not kill the check
                continue
            if key:
                # `source` is like "vault:openai" / "profile:x" / "env:OPENAI_API_KEY".
                configured.append(pid)
                results.append(_check(pid, True, f"key configured ({source})"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Provider keys", False, f"could not resolve: {exc}", warn=True))

    # ── 2. Connections — including whether the DEFAULT can actually route ────
    #
    # A default connection stuck in needs_reauth means the brain cannot do AI at
    # all, and nothing surfaced that anywhere. That is the single most important
    # thing this section can tell you.
    try:
        from navig.providers.connect import diagnostics_report

        report = diagnostics_report()
        conns = report.get("connections") or []
        default = next((c for c in conns if c.get("is_default")), None)

        if conns:
            results.append(_check("Connections", True, f"{len(conns)} configured"))
        if default:
            name = default.get("name") or default.get("connection_id")
            if default.get("is_routable"):
                results.append(_check("Default", True, f"{name} (ready)"))
            else:
                results.append(
                    _check(
                        "Default",
                        False,
                        f"{name} is {default.get('ui_state', 'not routable')} — the brain "
                        f"cannot run AI until this is fixed (navig connect test)",
                    )
                )
        elif conns:
            results.append(
                _check("Default", False, "no default connection set (navig connect default <id>)",
                       warn=True)
            )

        broken = [
            c for c in conns
            if not c.get("is_routable") and not c.get("is_default")
        ]
        if broken:
            names = ", ".join(str(c.get("name") or c.get("connection_id")) for c in broken[:3])
            results.append(
                _check("Needs re-auth", False, f"{len(broken)}: {names}", warn=True)
            )

        # In-flight logins. Only worth a line when there ARE any — an abandoned one
        # is the visible trace of a login that never completed.
        pending = report.get("pending_logins")
        if pending:
            results.append(
                _check("Logins in progress", True, f"{pending} (expire after 10 min)")
            )

        if not conns and not configured:
            results.append(
                _check(
                    "AI provider",
                    False,
                    "none configured — run `navig connect add <template>` or `navig init --provider`",
                    warn=True,
                )
            )
    except Exception as exc:  # noqa: BLE001 — never let doctor itself crash
        results.append(_check("Connections", False, f"could not read: {exc}", warn=True))

    return results


def check_browsers() -> list[tuple[str, bool, str]]:
    """Debug browsers left running that no NAVIG session owns.

    A leaked debug browser is invisible by design: the harness renders its content in
    a tab it then closes, so the window is blank — and a headless one shows nothing at
    all. Nothing ever looked for them, which is exactly how ~24 of them (and 3.6 GB of
    profiles) once accumulated before anyone noticed. Port scans miss them too: a
    browser on ``--remote-debugging-port=0`` holds an ephemeral port nobody can guess.

    Returns [] when there is nothing to say, so the section only appears when it has
    something worth telling you.
    """
    try:
        from navig.browser.targets import list_debug_browsers

        running = list_debug_browsers()
    except Exception as exc:  # noqa: BLE001 — doctor must never be the thing that breaks
        # `return []` hides the section, which reads as "no leaked browsers" — the exact
        # state this check exists to end (~24 of them, 3.6 GB of profiles, unnoticed).
        # A debug line nobody reads is not a report.
        logger.debug("browser leak check failed: %s", exc)
        return [_check("Leaked browsers", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    orphans = [b for b in running if b["kind"] == "orphan"]
    foreign = [b for b in running if b["kind"] == "foreign"]
    if not orphans and not foreign:
        return []

    results: list[tuple[str, bool, str]] = []
    if orphans:
        results.append(
            _check(
                "Leaked browsers",
                False,
                f"{len(orphans)} NAVIG-launched browser(s) still running "
                f"(reclaim: navig cdp stop --all)",
                warn=True,
            )
        )
    if foreign:
        # Not ours to kill — another harness may still be driving them. Say what they
        # are and let the operator decide.
        pids = ", ".join(str(b["pid"]) for b in foreign[:3])
        results.append(
            _check(
                "Foreign debug browsers",
                True,
                f"{len(foreign)} running that NAVIG did not launch (pid {pids}) — "
                f"left untouched",
            )
        )
    return results


def check_python_deps() -> list[tuple[str, bool, str]]:
    """Quick check for key optional dependencies."""
    results = []

    optional_deps = [
        ("aiohttp", "gateway server"),
        ("yaml", "config parsing"),
        ("typer", "CLI framework"),
        ("rich", "terminal output"),
        ("pydantic", "data validation"),
        ("cryptography", "vault / encryption"),
    ]

    for mod, purpose in optional_deps:
        try:
            importlib.import_module(mod)
            results.append(_check(f"Python/{mod}", True, purpose))
        except ImportError:
            results.append(_check(f"Python/{mod}", False, f"missing — affects {purpose}"))

    return results


def check_media_tools() -> list[tuple[str, bool, str]]:
    """The two external tools the media pipeline needs — and silently skips.

    ffmpeg and OCR are the only dependencies whose absence produces a *plausible
    empty answer* rather than an error: a video with no transcript, an image with
    no text. Everything else that goes missing raises. So these two are the pair
    worth a row — without one, the operator's evidence is that the feature "found
    nothing", which is exactly what a working feature looks like on quiet input.
    """
    results = []

    ffmpeg = shutil.which("ffmpeg")
    results.append(_check(
        "ffmpeg",
        bool(ffmpeg),
        ffmpeg or "not on PATH — video/audio transcription and frame OCR are skipped",
        warn=True,
    ))

    from navig.core.ocr import (
        OCR_INSTALL_HINT,
        ocr_language,
        ocr_language_gap,
        ocr_unavailable_reason,
    )

    reason = ocr_unavailable_reason()
    if reason is None:
        detail = "available"
        try:
            import pytesseract  # type: ignore

            detail = f"tesseract {pytesseract.get_tesseract_version()}"
        except Exception:  # noqa: BLE001 — the probe already said it works
            pass
        # An installed Tesseract reading the WRONG language is the worst of the
        # three states: it returns confident-looking nonsense, so a green row
        # here would tell the operator not to look at the one thing that is
        # broken. Say which language it reads in, and warn when that is not the
        # language they pinned.
        gap = ocr_language_gap()
        if gap:
            results.append(_check(
                "OCR (on-screen text)", False,
                f"{detail} — but {gap}. Text in that script reads as noise.",
                warn=True,
            ))
        else:
            lang = ocr_language()
            results.append(_check(
                "OCR (on-screen text)", True,
                f"{detail} · reads {lang}" if lang else detail,
            ))
    else:
        results.append(_check(
            "OCR (on-screen text)",
            False,
            f"{reason} — image/video text is silently skipped. Fix: {OCR_INSTALL_HINT}",
            warn=True,
        ))

    return results


def _webhook_tenant_rows() -> list[tuple[str, bool, str]]:
    """Can the bot actually HEAR? (lighthouse mode only — no rows otherwise.)

    "Uplink: online" is not proof of reachability. The lighthouse webhook URL embeds
    ``sha256(deck.api_key)`` — the Durable Object the edge routes Telegram's POSTs to
    — and the gateway *rotates* ``deck.api_key`` on its own (mints one when missing,
    upgrades one under 16 chars). A rotation leaves ``telegram.webhook_url`` addressing
    the OLD tenant, whose DO has no uplink socket: it queues every update and acks 202,
    while the brain, attached to the NEW tenant, reports a perfectly healthy uplink.
    The bot goes 100% deaf with every light green — so check the tenants match.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        from navig.core import Config
        from navig.telegram.updates import corrected_webhook_url

        cfg = Config()
        if str(cfg.get("cloud.mode", "") or "").lower() != "lighthouse":
            return []  # not lighthouse — the section does not apply

        hook = cfg.get("telegram.webhook_url")
        if not hook:
            return [
                _check(
                    "Telegram webhook",
                    False,  # lighthouse mode with no webhook is a ⚠, not a green ✓
                    "not configured (bot uses long-polling)",
                    warn=True,
                )
            ]

        edge = str(cfg.get("cloud.lighthouse_url", "") or "").strip().rstrip("/")
        if corrected_webhook_url(hook, cfg):
            results.append(
                _check(
                    "Telegram webhook",
                    False,
                    "STALE tenant — deck.api_key was rotated, so Telegram is delivering "
                    "to a dead edge queue and the bot cannot receive ANY message. "
                    "Fix: restart the gateway (it self-heals) or `navig lighthouse redeploy`",
                )
            )
        elif edge and not hook.startswith(f"{edge}/tg/"):
            # Not our edge — we can't derive or vouch for this tenant, so don't pretend to.
            results.append(
                # We can't derive or vouch for this tenant — that's a ⚠ (could-not-verify),
                # never a green ✓ (matches the "COULD NOT VERIFY" sibling below).
                _check("Telegram webhook", False, "custom host (not the lighthouse edge)", warn=True)
            )
        else:
            results.append(_check("Telegram webhook", True, "tenant matches the live brain"))
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        results.append(_check("Telegram webhook", False, f"COULD NOT VERIFY ({exc})", warn=True))

    return results


def _miniapp_button_row() -> list[tuple[str, bool, str]]:
    """Is the Mini App button serving the deck we actually deployed?

    The sibling of the webhook-tenant check above, pointing the other way: that one
    catches "the bot cannot HEAR with every light green", this one catches "the deck
    SHOWS an old app with every light green". Telegram caches a Mini App by URL and
    ignores Cache-Control, so a button URL whose ``v=`` cache-bust never changed pins
    every client to the bundle it cached — a deploy the operator watched succeed that
    literally no one can see.

    Applies in EVERY cloud mode (unlike the webhook rows), because the button points
    at the deck, not at the brain's ingress.
    """
    try:
        from navig.commands.miniapp import miniapp_button_health

        verdict = miniapp_button_health()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        return [_check("Mini App button", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if verdict is None:
        return []  # no deck deployed — the question does not apply
    ok, detail, warn = verdict
    return [_check("Mini App button", ok, detail, warn=warn)]


def check_reachability() -> list[tuple[str, bool, str]]:
    """Reachability rows: can the bot HEAR, and is the deck people open the current one?"""
    return _webhook_tenant_rows() + _miniapp_button_row()


def _gateway_has_ever_run() -> bool:
    """Has a gateway ever bound on this install?

    The live gateway writes ``gateway.json`` when it binds, so its presence answers the
    question. Its own function so a test can replace *this* rather than patching
    ``paths.config_dir`` — patching the global path resolver poisons whatever has
    already cached a config dir, and the damage outlives the test that did it (two
    unrelated vault rows went green-but-empty in a neighbouring file before this was
    split out).

    Unknown counts as "has run": between reassuring the operator and warning them, an
    unanswerable question should not resolve to the reassuring answer.
    """
    try:
        from navig.platform.paths import config_dir

        return (config_dir() / "gateway.json").exists()
    except Exception:  # noqa: BLE001
        return True


def check_pending_approvals() -> list[tuple[str, bool, str]]:
    """Approvals the operator was asked for and has not answered.

    The record is written to `<config_dir>/approvals/` so it survives a restart — and until
    now **nothing read it back**. A durable record no surface displays is the same shape as
    the bug it was built to fix: the daemon knows something is outstanding and the operator
    cannot see it. `ApprovalManager.list_pending()` is the in-memory view, which is empty
    after every restart, so it cannot answer this question.

    Not a failure — an unanswered approval is a normal state. It is reported as a WARN so it
    shows up without pretending the install is broken, and stays silent when there is
    nothing waiting.
    """
    try:
        from navig.approval import journal, resume
    except Exception:  # noqa: BLE001 — an absent module must not break doctor
        return []

    try:
        pending = journal.list_pending()
        resumable = resume.list_resumable()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        # `return []` renders as NO ROW, which the operator reads as "nothing waiting" —
        # the reassuring answer to an unanswerable question, and the same shape this
        # function was written to fix (a durable record no surface displays). The sibling
        # `_webhook_tenant_rows` already reports this case as `COULD NOT VERIFY (...)`;
        # match it. Note the import guard above still returns [] on purpose: no approvals
        # module means there is genuinely nothing to report, which is an answer.
        return [
            _check("Pending approvals", False, f"COULD NOT VERIFY ({exc})", warn=True)
        ]

    if not pending and not resumable:
        return []

    rows: list[tuple[str, bool, str]] = []
    if pending:
        newest = max((e.get("asked_at") or 0) for e in pending.values())
        age_min = max(0, int((time.time() - newest) / 60))
        rows.append((
            "Waiting on you",
            False,
            f"{len(pending)} approval(s) unanswered (most recent {age_min}m ago) — "
            "answer from Telegram or the deck",
        ))

    stale = [rid for rid, e in resumable.items() if resume.is_too_old(e)]
    if stale:
        rows.append((
            "Too old to resume",
            False,
            f"{len(stale)} approved-too-late record(s) past "
            f"{int(resume.max_age_seconds() / 60)}m — answering these now re-runs nothing; "
            "re-issue the request instead",
        ))
    return rows


def check_gateway_auth() -> list[tuple[str, bool, str]]:
    """Is anything actually authenticating requests to the local gateway?

    ``require_bearer_auth`` opens with ``if not token: return None`` — **no token means
    open access** — and seventeen route modules sit behind it, including
    ``POST /approval/{id}/respond``. An unauthenticated gateway lets any local process
    list the agent's pending approvals and answer them.

    The gateway therefore **mints and persists a token on its first start**. That makes
    "no token" mean something quite different from what it used to, and the two cases
    are not the same news:

    * the gateway has **never started** — nothing is wrong; the token appears when it
      does. Informational.
    * the gateway **has started** and there is still no token — the mint could not
      write, so a running gateway is serving its admin routes to anything on this
      machine. That is a fault, not a setting.

    ``gateway.json`` is the discriminator: the live gateway writes it when it binds, so
    its presence means this install has run one.

    (An earlier version of this row predated the mint and told the operator to set a
    token by hand as though they had simply never configured one. That advice was right
    for the failure case and misleading for the ordinary one — the whole reason to tell
    these apart.)
    """
    try:
        from navig.config import ConfigManager

        gateway_cfg = ConfigManager().get("gateway", {}) or {}
        auth = gateway_cfg.get("auth") if isinstance(gateway_cfg, dict) else None
        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        host = gateway_cfg.get("host", "127.0.0.1") if isinstance(gateway_cfg, dict) else "127.0.0.1"

        if token:
            return [_check("Gateway auth", True, f"bearer token set · bound to {host}")]

        has_run = _gateway_has_ever_run()

        if not has_run:
            return [
                _check(
                    "Gateway auth",
                    True,
                    "no token yet — one is generated and saved the first time the "
                    "gateway starts",
                )
            ]

        local_only = str(host).strip() in {"127.0.0.1", "localhost", "::1"}
        detail = (
            f"this gateway has run but has NO token, so its admin routes — including "
            f"approval responses — accept every request. The startup mint could not "
            f"write to your config. Bound to {host}"
            + (
                " (local processes only). Set one now: navig config set "
                "gateway.auth.token <secret>"
                if local_only
                else " — this is reachable off this machine. Set one NOW: "
                "navig config set gateway.auth.token <secret>"
            )
        )
        # Bound to a non-loopback address with no token is not a warning, it is a hole.
        return [_check("Gateway auth", False, detail, warn=local_only)]
    except Exception as exc:  # noqa: BLE001 — could-not-look is a warning, never a ✓
        return [_check("Gateway auth", False, str(exc)[:120], warn=True)]


def check_mcp_trust() -> list[tuple[str, bool, str]]:
    """What each configured MCP server is actually allowed to do.

    A third-party MCP server's tools are held for approval by default; marking one
    ``vetted`` lets the reads it *declares* run unprompted, and `…​.tools` narrows what it
    may offer at all. Both are set with `navig config set`, and until this row existed
    there was no way to confirm either took effect.

    The failure that matters is silent: an unrecognised tier — a typo, or a value the
    operator expected to mean something — falls back to ``byo`` with a log line the
    person who typed it will never see. That is the "configured but not in effect" state,
    so it is reported as a **warning**, not as a tidy ✓ over a setting that is being
    ignored.

    Returns an empty list when no MCP servers are configured, so the section does not
    appear on an install that does not use them.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        from navig.config import ConfigManager
        from navig.mcp.trust import (
            ServerTrust,
            allowed_tools_for_server,
            auto_approve_tools_for_server,
            trust_for_server,
        )

        # A fresh manager, not the process-wide singleton: that one caches for the
        # lifetime of the process and would happily describe a different install.
        mcp_cfg = ConfigManager().get("mcp", {}) or {}
        servers = mcp_cfg.get("servers") if isinstance(mcp_cfg, dict) else None
        names = sorted(servers) if isinstance(servers, dict) else []
        if not names:
            return []

        raw_trust = (mcp_cfg.get("trust") or {}) if isinstance(mcp_cfg, dict) else {}
        raw_servers = raw_trust.get("servers") if isinstance(raw_trust, dict) else None
        raw_servers = raw_servers if isinstance(raw_servers, dict) else {}

        for name in names:
            tier = trust_for_server(name)
            scope = allowed_tools_for_server(name)

            entry = raw_servers.get(name)
            configured = entry.get("tier") if isinstance(entry, dict) else entry
            # Only a STRING can be a tier; anything else was never going to apply.
            mistyped = (
                configured is not None
                and str(configured).strip().lower()
                not in {t.value for t in ServerTrust}
            )

            if scope is None:
                scope_text = "all tools"
            elif scope:
                scope_text = f"{len(scope)} tool(s): {', '.join(sorted(scope)[:4])}"
            else:
                scope_text = "NO tools (empty allowlist denies everything)"

            # Pre-authorised tools are the one setting here that lets a WRITE happen
            # with nobody watching, so it is always shown — a release valve the operator
            # cannot see is one they cannot review.
            pre_authorised = auto_approve_tools_for_server(name)
            if pre_authorised:
                scope_text += (
                    f" · {len(pre_authorised)} pre-authorised: "
                    f"{', '.join(sorted(pre_authorised)[:4])}"
                )

            detail = f"{tier.value} · {scope_text}"

            # Pre-authorising on a byo server is inert: gate 1 needs a vetted endpoint,
            # so those tools still prompt. Silently doing nothing is the failure mode
            # this whole row exists to catch.
            if pre_authorised and tier is not ServerTrust.VETTED and not mistyped:
                results.append(
                    _check(
                        f"MCP {name}",
                        False,
                        f"{detail} — auto_approve has no effect on a '{tier.value}' "
                        f"server; mark it vetted or those tools will keep asking.",
                        warn=True,
                    )
                )
                continue

            if mistyped:
                results.append(
                    _check(
                        f"MCP {name}",
                        False,
                        f"configured tier {configured!r} is not recognised — using "
                        f"'{tier.value}'. Valid: vetted, byo.",
                        warn=True,
                    )
                )
            elif scope == ():
                results.append(_check(f"MCP {name}", False, detail, warn=True))
            else:
                results.append(_check(f"MCP {name}", True, detail))
    except Exception as exc:  # noqa: BLE001
        # Could-not-look is a WARNING, never a ✓ — the rule this file learned the hard way.
        results.append(_check("MCP trust", False, str(exc)[:120], warn=True))
    return results


def check_config_health() -> list[tuple[str, bool, str]]:
    """Did the config/identity layer have to SAVE ITSELF recently — and is it still armed?

    Every fix in this area is self-healing: a refused config wipe, a load that fell back
    to the last known-good cache, a deck.api_key restored from the vault. That is the
    right behaviour — and it is also how an install ends up quietly broken, because a
    daemon that heals itself at 3am and tells nobody looks exactly like a daemon that is
    fine. The whole chain was written after a bot went 100% deaf with every light green.

    So: surface the incidents, and confirm the recovery mirror actually exists. An armed
    net you cannot see is indistinguishable from no net at all.
    """
    results: list[tuple[str, bool, str]] = []

    # 1. A preserved corrupt file — the config was unreadable at some point.
    try:
        from navig.platform.paths import config_dir

        corrupt = config_dir() / "config.yaml.corrupt"
        if corrupt.exists():
            results.append(
                _check(
                    "config.yaml",
                    False,
                    f"a previous load FAILED — the unreadable copy is kept at {corrupt}. "
                    "Compare it with your live config, then delete it to clear this.",
                )
            )
    except Exception as exc:  # noqa: BLE001 — a health check must never crash the doctor
        # Skipping silently means the operator cannot tell "no corrupt copy" from "I could
        # not look" — and a kept config.yaml.corrupt is evidence of a load that already
        # failed once.
        results.append(_check("config.yaml", False, f"COULD NOT VERIFY ({exc})", warn=True))

    # 2. Recent self-healing events. Silence here is the healthy state.
    try:
        from navig.core import incidents

        events = incidents.recent(limit=3)
        if events:
            results.append(
                _check(
                    "Incidents",
                    False,
                    f"{len(events)} recent self-healing event(s) — the config layer had to "
                    "rescue itself. Latest: " + incidents.describe(events[0]),
                )
            )
            for entry in events[1:]:
                # Continuation rows for the SAME finding. These MUST NOT be ok=True:
                # _check() renders ✓ whenever ok=True and ignores warn=, so ok=True here
                # printed a green tick over a real self-healing incident (a
                # DECK_KEY_REIDENTIFIED shows as "✓ :  …") — the precise "every light
                # green while broken" trap this whole section exists to prevent. And the
                # empty label rendered "✓ :  detail" in the console and "" in --json.
                # Render each as a labelled ⚠ sub-row instead.
                results.append(
                    _check("Incidents", False, incidents.describe(entry), warn=True)
                )
        else:
            results.append(_check("Incidents", True, "none — no config rescue was needed"))
    except Exception as exc:  # noqa: BLE001
        # "none — no config rescue was needed" and "I could not read the incident log" are
        # opposite answers, and silence renders as the first. This section exists because a
        # daemon that heals itself at 3am and tells nobody looks exactly like a healthy one.
        results.append(_check("Incidents", False, f"COULD NOT VERIFY ({exc})", warn=True))

    # 3. Is the identity recoverable at all? (deck.api_key IS the Lighthouse tenant.)
    try:
        from navig.cloud import deck_key
        from navig.config import ConfigManager

        # A fresh ConfigManager, NOT navig.core.Config: that one is a process-wide
        # singleton that caches its instance and never re-reads the config dir, so a
        # health check built on it would report on whichever config happened to be
        # loaded first. A check that can silently describe the wrong install is worse
        # than no check.
        deck_cfg = (ConfigManager().global_config or {}).get("deck", {}) or {}
        if str(deck_cfg.get("api_key", "") or "").strip():
            if deck_key.is_mirrored():
                results.append(
                    _check("Key recovery", True, "deck.api_key is mirrored — a config wipe is survivable")
                )
            else:
                results.append(
                    _check(
                        "Key recovery",
                        False,
                        "deck.api_key is NOT mirrored to the vault — if config lost it, the "
                        "gateway would mint a NEW identity and silently move the bot's mailbox. "
                        "Start the daemon once to arm the mirror.",
                        warn=True,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        # Without this row the operator cannot tell whether a config wipe would silently
        # re-identify the install and move the bot's mailbox — which is the whole reason
        # the mirror is checked here.
        results.append(_check("Key recovery", False, f"COULD NOT VERIFY ({exc})", warn=True))

    return results


def check_ledger() -> list[tuple[str, bool, str]]:
    """In-flight operations that outlived their process — interrupted, never completed.

    The operation ledger records a line only at completion (an atexit handler),
    so a hard-killed process leaves an in-flight MARKER but no ledger line
    (navig.operation_inflight). A marker whose owning process is gone is an
    operation that was interrupted; ``navig ledger reap`` records it honestly as
    ``interrupted``. Read-only here — doctor never mutates the ledger. Returns
    [] (section hidden) when there is nothing to say.
    """
    try:
        from navig import operation_inflight as _inflight
        from navig.operation_recorder import get_operation_recorder

        markers = get_operation_recorder().iter_inflight()
    except Exception as exc:  # noqa: BLE001 — a health check must never crash the doctor
        # Hiding the section reads as "no interrupted operations", which is the answer this
        # check exists to stop taking on faith.
        return [_check("Ledger", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not markers:
        return []

    running = interrupted = 0
    for marker in markers:
        try:
            alive = _inflight.pid_is_alive(marker.pid, marker.create_time)
        except Exception:  # noqa: BLE001
            alive = True  # indeterminate → assume alive, never over-report interrupted
        if alive:
            running += 1
        else:
            interrupted += 1

    results: list[tuple[str, bool, str]] = []
    if interrupted:
        results.append(
            _check(
                "Interrupted ops",
                False,
                f"{interrupted} operation(s) never completed (owning process gone) — "
                "record them honestly: navig ledger reap",
            )
        )
    if running:
        results.append(
            _check("In-flight ops", True, f"{running} operation(s) currently running")
        )
    return results


def check_repo_guard() -> list[tuple[str, bool, str]]:
    """Multi-agent repo guard state for the CURRENT repo.

    Empty (section skipped) outside a git repo — the guard is a per-repo,
    opt-in protection (see ``navig repo guard install``), so its absence is
    reported as informational, never as a failure. Partial wiring IS flagged:
    a half-wired guard gives false safety.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        import json as _json

        from navig.commands.repo import (
            _GUARD_MARKERS,
            _guard_event_wired,
            lock_state,
            read_lock,
            repo_root,
        )

        root = repo_root()
        if root is None:
            return []  # not inside a git repo — nothing to report

        settings_path = root / ".claude" / "settings.json"
        try:
            settings = _json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        wired = [ev for ev in _GUARD_MARKERS if _guard_event_wired(settings, ev)]

        if not wired:
            results.append(
                _check(
                    "Repo Guard",
                    True,
                    "not installed (optional) — enable with: navig repo guard install",
                )
            )
        elif len(wired) < len(_GUARD_MARKERS):
            missing = sorted(set(_GUARD_MARKERS) - set(wired))
            results.append(
                _check(
                    "Repo Guard",
                    False,
                    f"partially wired (missing: {', '.join(missing)}) — "
                    "re-run: navig repo guard install",
                    warn=True,
                )
            )
        else:
            lk = lock_state(read_lock(root))
            if lk["state"] == "free":
                detail = "active — lock free"
            else:
                detail = (
                    f"active — lock {lk['state']} by session {lk.get('session', '?')} "
                    f"(age {lk.get('age_minutes', '?')}m, branch {lk.get('branch') or '?'})"
                )
            results.append(_check("Repo Guard", True, detail))

        # Orphaned worktree dirs pile up (git worktree remove often can't delete
        # them on Windows); surface a warn row when any exist. Silent when clean.
        try:
            from navig.commands.repo import orphan_worktree_dirs

            n_orphans = len(orphan_worktree_dirs(root))
        except Exception:  # noqa: BLE001 — never let this crash the guard check
            n_orphans = 0
        if n_orphans:
            results.append(
                _check(
                    "Worktree orphans",
                    False,
                    f"{n_orphans} orphaned dir(s) in .dev/worktrees (untracked) — "
                    "clean: navig repo prune",
                    warn=True,
                )
            )
    except Exception as exc:  # noqa: BLE001 — a doctor check must never crash doctor
        # Not "outside a git repo" — that case returns [] explicitly inside the try above.
        # Reaching here means the guard state could not be read, and a hidden section reads
        # as "the guard is fine", which is what a half-wired guard already fakes.
        return [_check("Repo guard", False, f"COULD NOT VERIFY ({exc})", warn=True)]
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Report collection — the shared seam
# ──────────────────────────────────────────────────────────────────────────────


def _git_head(src_dir: Path) -> tuple[str, str] | None:
    """``(full_sha, short_sha)`` of HEAD in *src_dir*, or None if it isn't a git checkout.

    Uses ``git rev-parse`` (which walks UP to the repo root), so it works even though this
    monorepo keeps ``.git`` at the root, not inside the editable ``core/`` src dir.
    """
    import subprocess

    try:
        full = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        if full.returncode != 0 or not full.stdout.strip():
            return None
        short = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        s = short.stdout.strip() if short.returncode == 0 and short.stdout.strip() else full.stdout.strip()[:8]
        return full.stdout.strip(), s
    except Exception:  # noqa: BLE001
        return None


def _git_count(src_dir: Path, rng: str) -> int | None:
    """``git rev-list --count <rng>`` (e.g. ``A..B``); None on any failure."""
    import subprocess

    try:
        r = subprocess.run(
            ["git", "-C", str(src_dir), "rev-list", "--count", rng],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def check_daemon_freshness() -> list[tuple[str, bool, str]]:
    """Is the RUNNING daemon executing the code that's on disk NOW?

    An editable/pip install loads its source into memory once at boot, so a merge, a
    ``git pull`` or a branch switch changes disk while the running daemon keeps the OLD
    code — the "merged but not live" trap, invisible with every other light green. The
    daemon records the commit/version it booted from (``supervisor._capture_code_identity``);
    here we compare it to the current on-disk state and surface a ⚠ when they differ.

    Returns [] when the daemon is stopped (nothing to compare) or when neither a git commit
    nor a version can be resolved — silence beats crying wolf.
    """
    try:
        from navig.daemon.supervisor import NavigDaemon
    except Exception:  # noqa: BLE001
        return []
    try:
        if not NavigDaemon.is_running():
            return []  # stopped — nothing to compare, and that is an answer
        state = NavigDaemon.read_state() or {}
    except Exception as exc:  # noqa: BLE001
        # "stopped" is an answer; "I could not read the daemon's state" is not, and this
        # check exists for the merged-but-not-live trap that is already invisible with
        # every other light green.
        return [_check("Daemon freshness", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    boot = state.get("boot_code") if isinstance(state, dict) else None
    boot = boot if isinstance(boot, dict) else {}
    src_dir = Path(__file__).resolve().parents[2]
    disk = _git_head(src_dir)

    # --- git checkout: compare commits (the editable-install case) ---
    if disk is not None:
        disk_full, disk_short = disk
        boot_commit = str(boot.get("commit") or "")
        if not boot_commit:
            # An older daemon that predates freshness tracking — honest ⚠, not silence:
            # restarting both enables the feature AND loads whatever is newest on disk.
            return [
                _check(
                    "Daemon freshness",
                    False,
                    "the running daemon predates freshness tracking — restart once "
                    "(navig service restart --admin) to enable it",
                    warn=True,
                )
            ]
        if boot_commit == disk_full:
            branch = str(boot.get("branch") or "")
            detail = f"running current commit {disk_short}" + (f" ({branch})" if branch else "")
            behind = _git_count(src_dir, "HEAD..@{upstream}")  # offline: last-fetched ref
            if behind:
                detail += f" · disk is {behind} behind upstream — `navig update` to pull"
            return [_check("Daemon freshness", True, detail)]
        n = _git_count(src_dir, f"{boot_commit}..{disk_full}")
        boot_branch = str(boot.get("branch") or "")
        boot_ctx = f" on {boot_branch}" if boot_branch else ""
        gap = f"{n} commit(s) behind" if n else "diverged from"
        return [
            _check(
                "Daemon freshness",
                False,
                f"STALE — daemon booted at {boot_commit[:8]}{boot_ctx}, disk is now at "
                f"{disk_short} ({gap} on-disk). Restart to load the new code: "
                "navig update  /  navig service restart --admin",
                warn=True,
            )
        ]

    # --- non-git (wheel) install: compare versions ---
    boot_ver = str(boot.get("version") or "")
    try:
        import navig as _nav

        cur_ver = str(getattr(_nav, "__version__", "") or "")
    except Exception:  # noqa: BLE001
        cur_ver = ""
    if boot_ver and cur_ver and boot_ver != cur_ver:
        return [
            _check(
                "Daemon freshness",
                False,
                f"STALE — daemon is running v{boot_ver}, v{cur_ver} is installed. "
                "Restart to load it: navig service restart --admin",
                warn=True,
            )
        ]
    if boot_ver and cur_ver:
        return [_check("Daemon freshness", True, f"running v{cur_ver}")]
    return []  # can't determine — say nothing rather than cry wolf


def _collect_sections(
    port: int | None = None, skip_deps: bool = False
) -> list[tuple[str, list[tuple[str, bool, str]]]]:
    """Run every doctor check and return the ordered ``(section, rows)`` pairs.

    Checks are looked up as module globals at call time so tests (and plugins)
    can monkeypatch individual ``check_*`` functions on this module.
    """
    secs: list[tuple[str, list[tuple[str, bool, str]]]] = [
        ("Config", check_config()),
        ("Runtime", check_runtime() + check_path_health()),
        ("Storage", check_storage() + check_databases() + check_vault()),
        ("Filesystem", check_cache_dir() + check_logs()),
        ("Network Sockets", check_sockets(port)),
        ("Formations", check_formations()),
        ("Skills", check_skills()),
        (
            "Gateway",
            check_gateway(port=port)
            + check_event_processor(port=port)
            + check_gateway_auth(),
        ),
        ("AI Providers", check_ai_providers()),
        ("Identity", check_identity()),
        ("Media Tools", check_media_tools()),
        ("Wiring", check_wiring()),
    ]

    freshness_results = check_daemon_freshness()
    if freshness_results:  # only when the daemon is running + freshness is determinable
        secs.append(("Daemon", freshness_results))

    mcp_trust_results = check_mcp_trust()
    if mcp_trust_results:  # only when MCP servers are configured
        secs.append(("MCP Trust", mcp_trust_results))

    config_health_results = check_config_health()
    if config_health_results:
        secs.append(("Config Health", config_health_results))

    approval_results = check_pending_approvals()
    if approval_results:  # silent when nothing is waiting
        secs.append(("Approvals", approval_results))

    reachability_results = check_reachability()
    if reachability_results:  # webhook rows are lighthouse-only; the button row is not
        secs.append(("Reachability", reachability_results))

    ledger_results = check_ledger()
    if ledger_results:  # only when there are in-flight / interrupted operations
        secs.append(("Operations Ledger", ledger_results))

    repo_guard_results = check_repo_guard()
    if repo_guard_results:  # only inside a git repo
        secs.append(("Repo Guard", repo_guard_results))

    browser_results = check_browsers()
    if browser_results:  # only when a debug browser is actually running unowned
        secs.append(("Browsers", browser_results))

    if not skip_deps:
        secs.append(("Python Deps", check_python_deps()))
    return secs


def _shape_report(sections: list[tuple[str, list[tuple[str, bool, str]]]]) -> dict[str, Any]:
    """Shape collected sections into the machine-readable payload (the --json contract)."""
    from datetime import datetime, timezone

    from navig import __version__ as _navig_version

    passed = warnings = failed = 0
    out_sections: list[dict[str, Any]] = []
    for name, results in sections:
        checks: list[dict[str, Any]] = []
        for row in results:
            ok = bool(row[1])
            warn = (not ok) and row[0] == _WARN  # the rendered ⚠ state
            if ok:
                passed += 1
            elif warn:
                warnings += 1
            else:
                failed += 1
            checks.append(
                {
                    "label": _plain_text(getattr(row, "label", "")),
                    "ok": ok,
                    "warn": warn,
                    "detail": _plain_text(getattr(row, "detail", row[2])),
                }
            )
        out_sections.append({"name": name, "ok": all(c["ok"] for c in checks), "checks": checks})

    return {
        "ok": failed == 0 and warnings == 0,
        "sections": out_sections,
        "summary": {"passed": passed, "warnings": warnings, "failed": failed},
        "version": _navig_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_report(
    port: int | None = None, skip_deps: bool = False, *, quiet: bool = False
) -> dict[str, Any]:
    """Structured health report — EXACTLY the dict ``navig doctor --json`` prints.

    The shared seam between the CLI's ``--json`` branch and programmatic
    consumers (the self-heal loop — ``navig.selfheal.doctor_remediation`` —
    and any surface rendering doctor verdicts): same process, same check
    functions, no subprocess.

    ``quiet=True`` captures anything a check narrates to stdout mid-run and
    forwards it to stderr, so a caller that owns stdout (machine mode) still
    emits clean data.
    """
    if quiet:
        import contextlib
        import io

        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            sections = _collect_sections(port=port, skip_deps=skip_deps)
        if _buf.getvalue():
            sys.stderr.write(_buf.getvalue())
    else:
        sections = _collect_sections(port=port, skip_deps=skip_deps)
    return _shape_report(sections)


# ──────────────────────────────────────────────────────────────────────────────
# Main command
# ──────────────────────────────────────────────────────────────────────────────


def check_identity() -> list[tuple[str, bool, str]]:
    """Which identity actually reaches the model, and can it still be cached.

    Three questions an operator cannot otherwise answer:

    * **Which source won.** Seven sources can supply identity and only the
      highest-priority one is used. An ``IDENTITY.md`` outranked by a persona
      looks exactly like a broken ``IDENTITY.md`` until something says so.
    * **Is the guardrail floor intact.** It is compiled in and cannot be removed
      by a file, so this row is normally a formality — but it names the floor
      version, which is what you want when an agent misbehaves.
    * **Is the system prefix byte-stable.** A single volatile field in the system
      block discards the whole cached tools+system prefix on every turn; the
      symptom is a silent cost multiple, never an error.
    """
    results: list[tuple[str, bool, str]] = []

    # ── Which source won, and what it shadowed ──────────────────────────────
    try:
        from navig.personas.soul_loader import SOURCE_LABELS, resolve_soul

        resolution = resolve_soul()
        if not resolution.raw:
            results.append(_check(
                "Identity source",
                False,
                "nothing resolved — the agent will fall back to a built-in identity",
                warn=True,
            ))
        else:
            label = SOURCE_LABELS.get(resolution.source, resolution.source)
            detail = f"{label} · {len(resolution.raw):,} chars"
            if resolution.persona:
                detail += f" · persona {resolution.persona}"
            if resolution.shadowed:
                detail += " · shadows " + ", ".join(
                    SOURCE_LABELS.get(s.tag, s.tag) for s in resolution.shadowed
                )
            results.append(_check("Identity source", True, detail))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Identity source", False, str(exc)[:120], warn=True))

    # ── Guardrail floor ─────────────────────────────────────────────────────
    try:
        from navig.agent.conv.guardrails import (
            GUARDRAIL_FLOOR_VERSION,
            guardrail_floor,
            guardrails_paths,
        )

        floor = guardrail_floor()
        operator_files = [p for p, _tag in guardrails_paths() if p.exists()]
        detail = f"v{GUARDRAIL_FLOOR_VERSION} · {len(floor):,} chars"
        if operator_files:
            detail += f" · +{len(operator_files)} operator file(s)"
        results.append(_check("Guardrail floor", bool(floor.strip()), detail))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Guardrail floor", False, str(exc)[:120], warn=True))

    # ── Prompt-prefix stability (the cache invariant) ────────────────────────
    try:
        from navig.agent.conv.soul import get_soul_loader

        loader = get_soul_loader()
        ctx = loader.resolve()

        def _build() -> str:
            return loader.build_system_prompt(
                soul=ctx.condensed,
                lang_instruction="",
                awareness="",
                capabilities="",
                guardrails=ctx.guardrails,
                tone=ctx.tone,
                banned_phrases=ctx.banned_phrases,
                truncation_note=ctx.truncation_note,
            )

        first, second = _build(), _build()
        stable = first == second
        detail = f"{len(first):,} chars · ~{round(len(first) / 4):,} tok"
        if not stable:
            detail += " · NOT byte-stable — the prompt cache will miss every turn"
        results.append(_check("Prompt prefix", stable, detail, warn=not stable))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Prompt prefix", False, str(exc)[:120], warn=True))

    return results


def check_wiring() -> list[tuple[str, bool, str]]:
    """Plugin/module/command-map wiring health — the Store's doctor view.

    Covers: package-plugin lifecycle states, pip entry-point seams, module
    registry gating (stale overrides), command-map drift (entry-point commands
    missing from command_providers.json / names shadowed by the built-in map),
    and stale disabled_commands.json entries.
    """
    results: list[tuple[str, bool, str]] = []

    # Package-format plugins: lifecycle report.
    try:
        from navig.plugins.host import get_plugin_host

        tracker = get_plugin_host().tracker()
        report = tracker.report()
        failed = report.get("failed", 0)
        results.append(_check(
            "Plugin packages",
            failed == 0,
            tracker.summary_line(),
            warn=report.get("degraded", 0) > 0,
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Plugin packages", False, str(exc)[:120], warn=True))

    # Pip entry-point seams: every navig.plugins/navig.commands ep must load.
    ep_commands: set[str] = set()
    try:
        from importlib.metadata import entry_points

        for group in ("navig.plugins", "navig.commands", "navig.connectors"):
            for ep in entry_points(group=group):
                if group == "navig.commands":
                    ep_commands.add(ep.name)
                try:
                    ep.load()
                except Exception as exc:  # noqa: BLE001
                    results.append(_check(f"entry-point {group}:{ep.name}", False, str(exc)[:120]))
        results.append(_check("Entry-point seams", True, f"{len(ep_commands)} plugin command(s)"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Entry-point seams", False, str(exc)[:120], warn=True))

    # Module registry: stale user overrides (id no longer exists).
    try:
        from navig.core import Config
        from navig.modules.registry import get_registry

        modules = get_registry().discover().list_modules(include_dev=True)
        known_ids = {m["id"] for m in modules}
        overrides = Config().get("modules.overrides", default={}) or {}
        stale = [k for k in overrides if k not in known_ids] if isinstance(overrides, dict) else []
        locked = sum(1 for m in modules if m.get("locked"))
        results.append(_check(
            "Module registry",
            not stale,
            f"{len(modules)} modules · {locked} locked"
            + (f" · stale overrides: {', '.join(stale)}" if stale else ""),
            warn=bool(stale),
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Module registry", False, str(exc)[:120], warn=True))

    # Command map drift + shadowing. Shadowing (a plugin command hidden behind a
    # built-in) is a real misconfiguration → fails. Drift (an entry-point command
    # absent from the release-generated map) is EXPECTED for third-party plugins
    # — the shipped map only covers first-party — so it is informational only
    # and must never flip doctor's exit code on a healthy install.
    try:
        from navig.cli.providers import _shipped_map
        from navig.cli.registration import _EXTERNAL_CMD_MAP

        shipped = set(_shipped_map().keys())
        drift = sorted(ep_commands - shipped)
        shadowed = sorted(ep_commands & set(_EXTERNAL_CMD_MAP.keys()))
        detail = f"{len(shipped)} mapped"
        if drift:
            # Informational: expected for third-party plugins (the shipped map is
            # first-party only). First-party staleness is enforced by
            # tests/cli/test_command_providers_fresh.py, not by failing here.
            detail += (f" · not in shipped map ({', '.join(drift)}) — 3rd-party is fine; "
                       "if first-party, run scripts/gen_command_providers.py")
        if shadowed:
            detail += f" · shadowed by built-ins: {', '.join(shadowed)}"
        results.append(_check("Command map", not shadowed, detail, warn=True))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Command map", False, str(exc)[:120], warn=True))

    # Stale disabled_commands.json (provider uninstalled).
    try:
        import json as _json

        f = config_dir() / "disabled_commands.json"
        if f.exists():
            mapping = _json.loads(f.read_text(encoding="utf-8"))
            from navig.plugins.host import get_plugin_host

            installed = {p.id for p in get_plugin_host().list_installed()}
            stale_cmds = sorted(c for c, pid in mapping.items() if pid not in installed)
            results.append(_check(
                "Disabled commands",
                not stale_cmds,
                f"{len(mapping)} entries"
                + (f" · stale (plugin uninstalled): {', '.join(stale_cmds)}" if stale_cmds else ""),
                warn=bool(stale_cmds),
            ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Disabled commands", False, str(exc)[:120], warn=True))

    # Store one-liner.
    try:
        from navig.hub import store_status

        summary = store_status()
        by_state = summary["by_state"]
        results.append(_check(
            "Store",
            not summary["broken"],
            f"{by_state.get('wired', 0)} wired · {by_state.get('unwired', 0)} unwired · "
            f"{len(summary['broken'])} broken",
            warn=bool(summary["degraded"]),
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Store", False, str(exc)[:120], warn=True))

    return results


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show all checks, including passing ones (--json always includes every check)",
    ),
    skip_deps: bool = typer.Option(False, "--skip-deps", help="Skip Python dependency checks"),
    port: int | None = typer.Option(
        None, "--port", help="Gateway port to probe (default: live-resolved, else config)"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Machine-readable JSON report: ALWAYS includes every check (--verbose semantics), "
        "plain text only (no colors or glyphs), never prompts; exit code matches human mode",
    ),
    heal: bool = typer.Option(
        False,
        "--heal",
        help="Close the observe→repair loop: map failing checks to existing remediations, "
        "run the SAFE ones (report-only for anything disruptive), then re-check",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --heal: list what would be remediated without executing anything",
    ),
):
    """Run self-diagnostics on the NAVIG installation."""
    if ctx.invoked_subcommand is not None:
        return

    if dry_run and not heal:
        raise typer.BadParameter("--dry-run only applies with --heal")

    if heal:
        from navig.selfheal.doctor_remediation import run_heal

        raise typer.Exit(
            run_heal(port=port, skip_deps=skip_deps, dry_run=dry_run, json_output=json_output)
        )

    try:
        from rich import print as rprint  # noqa: F401
        from rich.table import Table  # noqa: F401

        console = get_console()
        _has_rich = True
    except ImportError:
        _has_rich = False
        console = None  # type: ignore[assignment]  # noqa: F841

    if json_output:
        # Machine mode: emit ONLY the JSON document — no header, no footer, no
        # prompts, no Rich. Every check from every section is included
        # (--verbose semantics); the exit code is identical to the human mode
        # (0 only when every row is ok — a ⚠ row flips it, exactly like ✗).
        # quiet=True: stdout must carry EXACTLY one JSON document — anything a
        # check narrates mid-run (e.g. ConfigManager announcing a migration)
        # is forwarded to stderr, where diagnostics belong in machine mode.
        import json as _json

        payload = collect_report(port=port, skip_deps=skip_deps, quiet=True)
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if payload["ok"] else 1)

    sections = _collect_sections(port=port, skip_deps=skip_deps)

    all_ok = True
    printed_lines: list[str] = []

    for section_name, results in sections:
        section_has_issues = any(not r[1] for r in results)
        if not verbose and not section_has_issues:
            # Summarise passing sections as one line
            printed_lines.append(f"  {_OK} {section_name}: all OK")
            continue

        printed_lines.append(f"\n  [{section_name}]")
        for _icon, ok, line in results:
            if not ok:
                all_ok = False
            printed_lines.append(line)

    # Print results
    header = "\n🩺 NAVIG Doctor\n" + ("─" * 55)
    print(header)
    for line in printed_lines:
        print(line)

    footer_icon = "✅" if all_ok else "⚠️ "
    footer = "\n" + ("─" * 55)
    if all_ok:
        footer += f"\n{footer_icon} All checks passed."
    else:
        footer += f"\n{footer_icon} Some issues found. Review items marked with ✗ or ⚠ above."
    print(footer)

    if not all_ok:
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# migrate-packs — fold the legacy ~/.navig/packs systems into plugins
# ──────────────────────────────────────────────────────────────────────────────


@doctor_app.command("migrate-packs")
def migrate_packs(
    apply: bool = typer.Option(False, "--apply", help="Actually migrate (default: dry-run)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Migrate legacy ~/.navig/packs content into the plugin format (idempotent).

    - a dir with SKILL.md            → plugin wrapping it under skills/<id>/
    - a dir with navig.package.json  → plugin with the `navig.handler` manifest key
    Dry-run by default; --apply performs the moves. Sources are MOVED (the
    plugins dir becomes the single install root).
    """
    import json as _json
    import shutil as _shutil

    packs_dir = config_dir() / "packs"
    plugins_dir = config_dir() / "plugins"
    plan: list[dict[str, str]] = []

    if packs_dir.exists():
        for child in sorted(packs_dir.iterdir()):
            if not child.is_dir() or child.name in {"installed", "local"}:
                continue
            if (plugins_dir / child.name).exists():
                plan.append({"src": str(child), "action": "skip", "reason": "already migrated"})
                continue
            if (child / "SKILL.md").exists():
                plan.append({"src": str(child), "action": "skill-bundle", "dest": str(plugins_dir / child.name)})
            elif (child / "navig.package.json").exists():
                plan.append({"src": str(child), "action": "handler-pack", "dest": str(plugins_dir / child.name)})
            else:
                plan.append({"src": str(child), "action": "skip", "reason": "unrecognized layout"})

    if json_output:
        print(_json.dumps({"dry_run": not apply, "plan": plan}, indent=2))
    else:
        if not plan:
            print("  ✓ Nothing to migrate — ~/.navig/packs is clean.")
            return
        for step in plan:
            print(f"  {step['action']:>13}  {step['src']}" + (f" → {step.get('dest', '')}" if step.get("dest") else f"  ({step.get('reason', '')})"))
        if not apply:
            print("\n  Dry run — re-run with --apply to migrate.")

    if not apply:
        return

    migrated = 0
    for step in plan:
        if step["action"] == "skip":
            continue
        src = Path(step["src"])
        dest = Path(step["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        manifest_dir = dest / ".claude-plugin"
        if step["action"] == "skill-bundle":
            # plugin wrapping the skill: content moves under skills/<id>/
            (dest / "skills").mkdir(parents=True, exist_ok=True)
            _shutil.move(str(src), str(dest / "skills" / src.name))
            manifest = {
                "name": src.name,
                "version": "1.0.0",
                "description": f"Migrated skill pack '{src.name}' (was ~/.navig/packs).",
            }
        else:  # handler-pack
            _shutil.move(str(src), str(dest))
            try:
                legacy = _json.loads((dest / "navig.package.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                legacy = {}
            manifest = {
                "name": legacy.get("id", src.name),
                "version": legacy.get("version", "1.0.0"),
                "description": legacy.get("description", f"Migrated handler pack '{src.name}'."),
                "navig": {"handler": legacy.get("entry", "handler.py")},
            }
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(
            _json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        migrated += 1
        print(f"  ✓ migrated {src.name}")

    if migrated:
        # `navig package` does not exist — the plugin surface is `navig plugin`.
        print(
            f"\n  {migrated} pack(s) migrated to ~/.navig/plugins/. "
            "See them with `navig plugin list`."
        )


@doctor_app.command("clean-path")
def clean_path_cmd(
    apply: bool = typer.Option(False, "--apply", help="Actually rewrite PATH (default: dry-run)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Prune non-existent and duplicate directories from the persistent user PATH (Windows).

    The companion to the `PATH health` check: that row reports how much of PATH is
    reclaimable, this reclaims it. Only two classes are ever removed — directories that
    do not exist, and exact duplicates — because neither can affect command resolution,
    so no judgement is made about what the operator still uses.

    Why it matters: cmd.exe truncates PATH at 8191 chars, and past that every command
    resolved through a shell fails naming a tool that IS installed. On this developer's
    machine 76 dead ``AppData/Local/Temp/<random>`` entries had leaked into the persistent
    user PATH, and a nested `npm run` chain crossed the ceiling.

    Dry-run by default; `--apply` writes. The previous value is saved under
    `<config>/backups/` first, and only the USER PATH is touched — the machine PATH
    needs admin and is not navig's to rewrite.
    """
    import json as _json

    from navig import console_helper as ch

    if os.name != "nt":
        # The 8191 ceiling is a cmd.exe property. Say so plainly rather than pruning a
        # POSIX PATH for a problem it does not have.
        msg = "clean-path is Windows-only (the 8191-char ceiling is a cmd.exe property)"
        if json_output:
            print(_json.dumps({"supported": False, "reason": msg}))
        else:
            ch.warning(msg)
        raise typer.Exit(1)

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            # winreg does NOT expand REG_EXPAND_SZ, which is what we want: expanding
            # %VAR% and writing the result back is how a PATH gets silently baked.
            raw, kind = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        raw, kind = "", winreg.REG_EXPAND_SZ
    except OSError as exc:
        msg = f"could not read the user PATH from the registry: {exc}"
        if json_output:
            print(_json.dumps({"supported": True, "error": msg}))
        else:
            ch.error(msg)
        raise typer.Exit(1) from exc

    entries = [e for e in str(raw).split(os.pathsep) if e.strip()]
    kept, removed = partition_path_entries(entries)
    new_value = os.pathsep.join(kept)
    freed = len(raw) - len(new_value)

    if json_output:
        print(
            _json.dumps(
                {
                    "supported": True,
                    "applied": bool(apply and removed),
                    "before_chars": len(raw),
                    "after_chars": len(new_value),
                    "freed_chars": freed,
                    "removed": [{"entry": e, "reason": r} for e, r in removed],
                },
                indent=2,
            )
        )
        if not (apply and removed):
            return

    if not json_output:
        if not removed:
            ch.success(f"User PATH is already clean ({len(raw)} chars, {len(entries)} entries)")
            return
        table = ch.Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Reason", no_wrap=True)
        table.add_column("Chars", no_wrap=True, justify="right")
        table.add_column("Directory")  # the one wrappable column
        for entry, reason in removed:
            colour = "yellow" if reason == "duplicate" else "red"
            table.add_row(f"[{colour}]{reason}[/{colour}]", str(len(entry) + 1), entry)
        ch.console.print(table)
        ch.info(
            f"{len(removed)} entr{'y' if len(removed) == 1 else 'ies'} · "
            f"{len(raw)} → {len(new_value)} chars (frees {freed})"
        )

    if not apply:
        if not json_output:
            ch.info("dry-run — re-run with --apply to write it")
        return

    # A prune that empties PATH is a bug, not a clean machine. Refuse rather than write.
    if not kept:
        ch.error("refusing to write: every entry would be removed")
        raise typer.Exit(1)

    backup_dir = config_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"path-user-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    backup.write_text(str(raw), encoding="utf-8")

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            # Preserve the ORIGINAL value kind: rewriting a REG_EXPAND_SZ PATH as REG_SZ
            # turns every %VAR% in it into a literal that resolves to nothing.
            winreg.SetValueEx(key, "Path", 0, kind, new_value)
    except OSError as exc:
        ch.error(f"could not write the user PATH: {exc} (unchanged; backup at {backup})")
        raise typer.Exit(1) from exc

    _broadcast_environment_change()
    ch.success(f"User PATH rewritten — freed {freed} chars. Backup: {backup}")
    ch.info("open a NEW terminal to pick it up; restore by pasting the backup back if needed")


def _broadcast_environment_change() -> None:
    """Tell running shells the environment changed. Best-effort by design.

    Without it the new PATH only reaches processes started after the next logon. A
    failure here means "existing windows keep the old PATH", which is a nuisance, not a
    reason to fail a write that already succeeded.
    """
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x1A,  # WM_SETTINGCHANGE
            0,
            "Environment",
            0x2,  # SMTO_ABORTIFHUNG
            5000,
            None,
        )
    except Exception:  # noqa: BLE001 — a notification must never fail the command
        pass
