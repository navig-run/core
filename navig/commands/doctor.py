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
    #: Optional structured payload (plain data) for --json. Rows without one are shaped
    #: exactly as before, so every existing consumer of the payload is untouched; the key
    #: is added only where a check has data that would not survive one detail string.
    data: dict | None

    def __new__(
        cls,
        icon: str,
        ok: bool,
        line: str,
        *,
        label: str = "",
        detail: str = "",
        data: dict | None = None,
    ):
        self = super().__new__(cls, (icon, ok, line))
        self.label = label
        self.detail = detail
        self.data = data
        return self


def _check(
    label: str,
    ok: bool,
    detail: str = "",
    warn: bool = False,
    data: dict | None = None,
) -> CheckResult:
    """Return a formatted result row (a ``(icon, ok, line)`` tuple + structured fields)."""
    if ok:
        icon = _OK
    elif warn:
        icon = _WARN
    else:
        icon = _ERR
    line = f"  {icon} {label}" + (f": {detail}" if detail else "")
    return CheckResult(icon, ok, line, label=label, detail=detail, data=data)


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
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0, "Task Scheduler"
        if sys.platform == "darwin":
            # macOS has no systemd: the old probe here always read "not registered".
            from navig.daemon.service_manager import _launchd_plist_path

            return _launchd_plist_path().exists(), "launchd"
        for scope in (["--user"], []):  # a root install registers the system-scope unit
            r = subprocess.run(
                ["systemctl", *scope, "is-enabled", "navig-agent"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True, "systemd"
        return False, "systemd"
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
    venv_py = (
        rt / "venv" / ("Scripts" if is_win else "bin") / ("python.exe" if is_win else "python")
    )

    try:
        in_runtime = str(Path(sys.executable).resolve()).startswith(str(rt.resolve()))
    except Exception:  # noqa: BLE001
        in_runtime = False

    managed = in_runtime or venv_py.exists()
    if not managed:
        results.append(
            _check(
                "install type", True, f"dev/editable ({sys.executable}) — runtime checks skipped"
            )
        )
    else:
        uv_exe = rt / ("uv.exe" if is_win else "uv")
        results.append(
            _check(
                "uv engine",
                uv_exe.exists(),
                str(uv_exe) if uv_exe.exists() else "missing from runtime",
                warn=True,
            )
        )
        results.append(
            _check(
                "isolated venv",
                venv_py.exists(),
                str(rt / "venv") if venv_py.exists() else "missing",
                warn=True,
            )
        )
        results.append(_check("running from runtime", in_runtime, sys.executable, warn=True))
        shim = Path.home() / ".local" / "bin" / ("navig.cmd" if is_win else "navig")
        results.append(
            _check(
                "launcher shim", shim.exists(), str(shim) if shim.exists() else "missing", warn=True
            )
        )
        on_path = str(shim.parent) in os.environ.get("PATH", "").split(os.pathsep)
        results.append(
            _check(
                "shim dir on PATH",
                on_path,
                "" if on_path else "open a new shell / re-run install",
                warn=True,
            )
        )
        registered, kind = _daemon_autostart()
        results.append(
            _check(
                "daemon auto-start",
                registered,
                kind if registered else "not registered — run 'navig service install'",
                warn=True,
            )
        )

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


def check_install_profile() -> list[tuple[str, bool, str]]:
    """Which installation profile (`navig init --profile`) this install recorded.

    The row reports what is WRITTEN in the config, verified against the profile
    names the installer actually accepts — a stale or hand-edited name is a
    warning, not a green tick. "None recorded" is a verified answer (interactive
    installs and every pre-profile install legitimately have no key), so it is
    the one green row here; a config that cannot be read is COULD NOT VERIFY.
    """
    try:
        from navig.config import ConfigManager

        raw = ConfigManager(config_dir=config_dir()).get("install.profile")
    except Exception as exc:  # noqa: BLE001 — a health check must never crash the doctor
        return [_check("Install profile", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if raw is None or raw == "":
        return [
            _check(
                "Install profile",
                True,
                "none recorded (interactive or pre-profile install)",
            )
        ]

    from navig.installer.profiles import VALID_PROFILES  # noqa: PLC0415 — lazy

    name = str(raw)
    if name in set(VALID_PROFILES) | {"quickstart"}:
        return [_check("Install profile", True, name)]
    return [
        _check(
            "Install profile",
            False,
            f"{name!r} is not a known profile — fix install.profile",
            warn=True,
        )
    ]


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
        results.append(_check("supervisor logs", False, f"{daemon_log} not found", warn=True))

    # 2. The live daemon's app log -- the one that moves.
    pid = None
    try:
        from navig.daemon.supervisor import NavigDaemon

        if NavigDaemon.is_running():
            pid = NavigDaemon.read_pid()
    except Exception as exc:  # noqa: BLE001 - a probe must not break doctor
        results.append(
            _check("daemon log path", False, f"could not read the pid file: {exc}", warn=True)
        )
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
            _check(
                "daemon log path", False, "psutil not installed - cannot ask the process", warn=True
            )
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
            _check(
                "Database integrity",
                False,
                f"{checked} of {len(databases)} verified — " + ", ".join(parts),
                warn=True,
            )
        ]
    return [
        _check("Database integrity", True, f"{checked} databases intact (quick_check + FTS audit)")
    ]


def _declared_navig_engines() -> tuple[list[str], str]:
    """(engine names declared as HARD deps, where that was read from).

    Prefers the source tree's pyproject over the installed dist metadata, and
    that ordering is the whole point: the dist-info is regenerated by whatever
    checkout last ran `pip install -e`, so it can itself be the stale artifact.
    Reading it as the source of truth would make this check blind to exactly the
    drift it exists to catch — observed here, where the installed metadata had
    dropped navig-vault that pyproject still declares.
    """
    try:
        import tomllib

        import navig

        root = Path(navig.__file__).resolve().parent.parent / "pyproject.toml"
        if root.is_file():
            data = tomllib.loads(root.read_text(encoding="utf-8"))
            deps = data.get("project", {}).get("dependencies", []) or []
            names = [
                re.split(r"[<>=!;\[ ]", d.strip(), maxsplit=1)[0]
                for d in deps
                if d.strip().lower().startswith("navig-")
            ]
            return sorted(set(names)), str(root)
    except Exception:  # noqa: BLE001 — fall through to dist metadata
        logger.debug("pyproject read failed for dependency check", exc_info=True)

    try:
        import importlib.metadata as md

        reqs = md.requires("navig") or []
        names = [
            re.split(r"[<>=!;\[ ]", r.strip(), maxsplit=1)[0]
            for r in reqs
            if "extra ==" not in r and r.strip().lower().startswith("navig-")
        ]
        return sorted(set(names)), "installed metadata"
    except Exception:  # noqa: BLE001
        return [], ""


def check_hard_dependencies() -> list[tuple[str, bool, str]]:
    """navig's own engines that are declared but cannot be imported.

    An editable install does not pick up dependencies added to pyproject after
    it was made, so a package can be declared and absent at the same time. That
    drift stays invisible until some subsystem fails in its own vocabulary:
    navig-vault went missing once and the Telegram surface reported "api_id/
    api_hash are not set", sending the operator to re-authenticate over
    credentials that were intact the whole time. This names the package instead.

    Only navig-* engines are checked. Third-party packages fail loudly on
    import; the first-party ones are reached through shims that degrade quietly,
    which is what makes them worth a row here.
    """
    import importlib

    declared, source = _declared_navig_engines()
    if not declared:
        return [
            _check("declared engines", False, "could not read declared dependencies", warn=True)
        ]

    missing = []
    for name in declared:
        try:
            importlib.import_module(name.replace("-", "_"))
        except Exception:  # noqa: BLE001 — any import failure means "not usable"
            missing.append(name)

    if missing:
        return [
            _check(
                "declared engines",
                False,
                f"declared but not importable: {', '.join(missing)} — an editable "
                f"install does not pick up dependencies added after it was made. "
                f"Install them from plugins/ (see the Plugin Sources row for the exact "
                f"command); `pip install -e core` would fetch them from PyPI instead",
            )
        ]
    return [
        _check("declared engines", True, f"{len(declared)} declared in {source}, all importable")
    ]


def _editable_mappings() -> list[tuple[str, str]]:
    """(module, target-path) from every editable finder visible on ``sys.path``.

    The finder files hold a plain ``MAPPING = {'name': 'path'}`` literal; it is
    read with ``ast.literal_eval`` (never executed) so doubled backslashes in
    Windows paths decode exactly as the finder would see them.
    """
    import ast

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in sys.path:
        d = Path(entry)
        if not d.is_dir():
            continue
        try:
            finders = sorted(d.glob("__editable___*_finder.py"))
        except OSError:  # unreadable sys.path entry — skip, it cannot host imports either
            continue
        for f in finders:
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            at = txt.find("MAPPING")
            open_at = txt.find("{", at) if at >= 0 else -1
            if open_at < 0:
                continue
            depth, i = 0, open_at
            while i < len(txt):
                if txt[i] == "{":
                    depth += 1
                elif txt[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            else:
                continue
            try:
                mapping = ast.literal_eval(txt[open_at : i + 1])
            except (ValueError, SyntaxError):
                continue
            for name, path in mapping.items():
                key = (name, path)
                if key in seen:
                    continue
                seen.add(key)
                out.append(key)
    return out


def _editable_findings() -> list[tuple[str, str]]:
    """(module, detail) for every mapping that no longer resolves.

    Path existence alone is not enough: a package mid-extraction (its directory
    present, ``__init__.py`` not yet written) maps fine and imports dead — that is
    exactly how a first-party plugin can print "entry-point command unavailable"
    while a path check reports it healthy. ``find_spec`` resolves through the LIVE
    import system (the editable finder registered at site startup) and executes
    nothing, so it answers the real question cheaply.
    """
    import importlib.util

    findings: list[tuple[str, str]] = []
    for name, path in _editable_mappings():
        target = Path(path)
        if not target.exists():
            findings.append(
                (
                    name,
                    f"target gone (was under {Path(path).parent.name}) — reinstall from "
                    f"its checkout with pip install -e, or pip uninstall {name}",
                )
            )
            continue
        if not name.startswith("navig"):
            continue  # foreign editables: checked for existence only, never imported
        try:
            spec = importlib.util.find_spec(name)
        except Exception as exc:  # noqa: BLE001 — a broken finder raises its own shapes
            spec = None
            why = f" ({type(exc).__name__})"
        else:
            why = ""
        if spec is None:
            findings.append(
                (
                    name,
                    f"no module resolves though {Path(path).parent.name} exists "
                    f"(partial or moved package){why} — reinstall with pip install -e",
                )
            )
    return findings


def check_editable_installs() -> list[tuple[str, bool, str]]:
    """Editable installs whose mapping no longer resolves — the silent-stale class.

    An editable finder pins a module name to a directory; when that directory is
    deleted (a finished worktree) or half-written (a package mid-extraction), the
    mapping stays registered and the failure surfaces FAR away: ``navig <verb>``
    says "entry-point command unavailable", ``export_registry`` silently drops the
    plugin's commands from the manifest — a shrink that a ``--check`` against its
    own last run then calls fresh — or a stale checkout keeps serving code that no
    longer exists. Observed three times in one day (2026-10-14). Rows are ✗, not ⚠:
    a command surface that does not load is broken the way a missing declared
    engine is (see check_hard_dependencies).
    """
    findings = _editable_findings()
    if findings:
        return [_check(f"editable {name}", False, detail) for name, detail in findings]
    total = len(_editable_mappings())
    if not total:
        return [_check("editable installs", True, "none — every package is a regular install")]
    return [_check("editable installs", True, f"{total} mapped, all resolve")]


def _vault_custody_row(vault) -> CheckResult:
    """Can this vault survive the loss of this machine? Read-only (keyslot rows only).

    ✓ only with a recovery keyslot — that is the one way back after a dead disk or
    a reinstall. Legacy (fingerprint-only) and keyslots-without-recovery are ⚠.
    """
    try:
        kinds = {s.kind for s in vault.keyring.slots()}
    except Exception as exc:  # noqa: BLE001 — could not verify ⇒ never green
        return _check(
            "Vault keys", False, f"cannot read keyslots ({type(exc).__name__})", warn=True
        )
    if not kinds:
        return _check(
            "Vault keys",
            False,
            "fingerprint-only, no recovery key — navig vault recovery init",
            warn=True,
        )
    if "recovery" not in kinds:
        return _check(
            "Vault keys",
            False,
            f"{', '.join(sorted(kinds))}; no recovery key — navig vault recovery init",
            warn=True,
        )
    return _check("Vault keys", True, f"{', '.join(sorted(kinds))}")


def _plaintext_host_secrets_row() -> CheckResult:
    """How many host passwords still sit in plaintext YAML (counts only, never values)."""
    try:
        from navig.config import ConfigManager
        from navig.core.host_secrets import scan_plaintext_host_secrets

        rows: list = []
        seen: set = set()
        for cd in ConfigManager().get_config_directories():
            for sub in ("hosts", "apps"):
                d = cd / sub
                if d not in seen:
                    seen.add(d)
                    rows.extend(scan_plaintext_host_secrets(d))
    except Exception as exc:  # noqa: BLE001 — could not verify ⇒ never green
        return _check(
            "Host credentials", False, f"could not scan ({type(exc).__name__})", warn=True
        )
    if rows:
        hosts = len({r["host"] for r in rows})
        return _check(
            "Host credentials",
            False,
            f"{len(rows)} plaintext password(s) in {hosts} host file(s) — navig security migrate-host-secrets",
            warn=True,
        )
    return _check("Host credentials", True, "no plaintext passwords in host files")


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
                    if vault.is_legacy() and not (vdir / CryptoEngine.SALT_FILE).exists():
                        # A legacy vault's key IS derived from the salt: derive_key()
                        # would CREATE a fresh one — a mutation, and one that can never
                        # decrypt the existing items. (A keyslot vault does not need it.)
                        raise CryptoError("salt file missing")
                    probe = next((i for i in vault.store().list() if i.encrypted_dek), None)
                    if probe is not None:
                        # Open one item's DEK *wrapper* only — never the payload —
                        # under the key the vault itself uses: a keyslot vault's VMK
                        # is NOT the fingerprint key, so probing with derive_key(None)
                        # would report a healthy vault as broken.
                        CryptoEngine.open(vault._master_key(), probe.encrypted_dek)
                        detail += " · encryption OK"
                results.append(_check("Vault", True, detail))
                results.append(_vault_custody_row(vault))
            except Exception as exc:  # noqa: BLE001
                # Exception CLASS only: messages can embed paths or item labels.
                results.append(_check("Vault", False, f"cannot open vault ({type(exc).__name__})"))
        results.append(_plaintext_host_secrets_row())

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
        results.append(
            _check("Vault", False, f"COULD NOT VERIFY ({type(exc).__name__})", warn=True)
        )

    return results


def _managed_pid_files_dir():
    """Where the supervisor writes gateway.pid / worker.pid (``NAVIG_CONFIG_DIR``)."""
    from pathlib import Path

    base = os.environ.get("NAVIG_CONFIG_DIR") or str(Path.home() / ".navig")
    return Path(base)


def _pid_still_ours(pid: int) -> bool:
    """Whether *pid* still belongs to a NAVIG process.

    A pid file outlives the process that wrote it and Windows recycles the number:
    measured 2026-10-25, a ``gateway.pid`` written on 22 Oct named a **Chrome
    renderer**, which made this doctor claim "a local brain is running" beside a brain
    link — a two-authority alarm against a browser — and made the runtime inventory
    report that browser as the daemon. A pid is never a process.
    """
    try:
        import psutil  # noqa: PLC0415

        cmd = [str(a) for a in psutil.Process(pid).cmdline()]
    except Exception:  # noqa: BLE001 — gone, denied, or not a process: not ours
        return False
    if not cmd:
        return False
    head = Path(cmd[0]).name.lower()
    if head.startswith("navig"):
        return True
    return head.startswith("python") and "navig" in cmd


def _pid_file_entries() -> list[tuple[str, int]]:
    """``(file name, pid)`` for the supervisor's pid files; unparseable files are skipped."""
    entries: list[tuple[str, int]] = []
    d = _managed_pid_files_dir()
    for name in ("gateway.pid", "worker.pid"):
        try:
            entries.append((name, int((d / name).read_text(encoding="utf-8").strip())))
        except Exception:  # noqa: BLE001 — absent or unparseable: it is simply not ours
            continue
    return entries


def _managed_daemon_pids() -> set[int]:
    """PIDs this install started that are STILL NAVIG processes.

    A recycled pid is not this install's daemon (see :func:`_pid_still_ours`): counting it
    turned a stale file into a running authority and a green "managed" verdict.
    """
    return {pid for _, pid in _pid_file_entries() if _pid_still_ours(pid)}


def _pid_owner_name(pid: int) -> str:
    """The current owner of *pid* — the evidence a stale pid file needs beside it."""
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process(pid)
        return Path(proc.name() or proc.exe()).name
    except Exception:  # noqa: BLE001
        return "no such process"


def _stale_pid_files_row() -> CheckResult | None:
    """Warn when a pid file names a process that is not NAVIG (or nothing at all)."""
    stale = [
        (name, pid, _pid_owner_name(pid))
        for name, pid in _pid_file_entries()
        if not _pid_still_ours(pid)
    ]
    if not stale:
        return None
    named = ", ".join(f"{name} names pid {pid} ({owner})" for name, pid, owner in stale)
    return _check(
        "PID files",
        False,
        f"{named} — not this install's process; the file is stale and is rewritten on the "
        "next start",
        warn=True,
    )


def _port_owner_pids(port: int):
    """PIDs LISTENING on *port*, or None when the OS will not name them.

    None and set() are different answers: None means "cannot attribute", set() means
    "nothing is listening". Collapsing them would hide the case this row exists for.
    """
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        owners: set[int] = set()
        for conn in psutil.net_connections(kind="tcp"):
            try:
                if (
                    conn.status == psutil.CONN_LISTEN
                    and conn.laddr
                    and conn.laddr.port == port
                    and conn.pid
                ):
                    owners.add(conn.pid)
            except Exception:  # noqa: BLE001 — one odd entry must not poison the scan
                continue
        return owners
    except Exception:  # noqa: BLE001 — AccessDenied on Windows: no attribution available
        return None


def _classify_gateway_owner(port: int, owners, own_pids: set[int]) -> CheckResult:
    """Who is on the gateway port, and is it us?

    A bound port used to be reported as "✓ Port N is bound (Gateway running)" no matter
    who owned it. On 2026-10-22 a gateway nobody intended held 8789 while this install's
    daemon was disabled on purpose — doctor would have shown a green row over a second
    NAVIG authority. "Something is listening" and "the thing listening is mine" are
    different questions; only the second one is a health signal.
    """
    if owners is None:
        return _check(
            "Port Occupation",
            False,
            f"Port {port} is bound but the owning process cannot be identified — "
            "a second NAVIG authority looks exactly like this; identify it with "
            f"`netstat -ano | findstr :{port}`",
            warn=True,
        )
    if not owners:
        return _check(
            "Port Occupation",
            False,
            f"Port {port} was reported bound but no listening PID was found — cannot attribute it",
            warn=True,
        )
    foreign = sorted(p for p in owners if p not in own_pids)
    if foreign:
        named = ", ".join(str(pid) for pid in foreign)
        return _check(
            "Port Occupation",
            False,
            f"Port {port} is held by pid(s) {named} which this install does not manage — "
            "a second NAVIG authority is running (stop it, or remove the stale process)",
        )
    named = ", ".join(str(pid) for pid in sorted(owners))
    return _check("Port Occupation", True, f"Port {port} is bound by this install (pid {named})")


def _gateway_owner_row(port: int) -> CheckResult:
    """Live row: classify the current owner of *port* against our own PID files."""
    return _classify_gateway_owner(port, _port_owner_pids(port), _managed_daemon_pids())


def _brain_link_path():
    """Where the NAVIG OS app records "my brain lives elsewhere" (see :mod:`navig.brain.link`)."""
    from navig.brain.link import brain_link_path  # noqa: PLC0415

    return brain_link_path()


def _local_brain_running() -> bool:
    """True when a local daemon or gateway is up on this machine."""
    try:
        pids = _managed_daemon_pids()
        if pids:
            try:
                import psutil  # noqa: PLC0415

                if any(psutil.pid_exists(pid) for pid in pids):
                    return True
            except Exception:  # noqa: BLE001 — fall through to the port probe
                pass
        import socket  # noqa: PLC0415

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            return probe.connect_ex(("127.0.0.1", _GATEWAY_PORT)) == 0
    except Exception:  # noqa: BLE001 — unknown is not "running"
        return False


def _classify_brain_role(link, local_running: bool) -> CheckResult:
    """Is this install a client, the brain, or both at once?

    A brain-link file means "my brain lives elsewhere"; a local daemon means "my brain
    lives here". Both at once is the 2026-10-22 defect: NEURON held a link to
    `outpost-server` and ran a gateway, and both hosts answered "canonical": true while
    every existing doctor row described only one half of the contradiction.
    """
    if isinstance(link, dict) and link.get("__corrupt__"):
        return _check(
            "Brain role",
            False,
            "a brain-link file exists but cannot be read — cannot verify whether this "
            "install is a client or the brain",
            warn=True,
        )
    if link:
        name = link.get("brainName") or "a remote brain"
        if local_running:
            return _check(
                "Brain role",
                False,
                f"this install is a client of {name} AND a local brain is running — two "
                "authorities; stop the local one or remove brain-link.json",
            )
        return _check("Brain role", True, f"client of {name}; local brain not running")
    if local_running:
        return _check(
            "Brain role", True, "this install is the brain (no brain link, local daemon running)"
        )
    return _check(
        "Brain role", True, "no brain link and no local brain — nothing is claiming the role"
    )


def _brain_role_row() -> CheckResult:
    """Live row: read this machine's brain link and compare it with what is running."""
    import json  # noqa: PLC0415

    link = None
    try:
        path = _brain_link_path()
        if path.exists():
            try:
                link = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — present but unreadable: cannot verify
                link = {"__corrupt__": True}
    except Exception:  # noqa: BLE001
        link = {"__corrupt__": True}
    return _classify_brain_role(link, _local_brain_running())


def _brain_data_routing_row() -> CheckResult:
    """Where this install sends data another machine owns — the capability contract, live.

    Doctor already reports whether a local Brain is running (:func:`_brain_role_row`). It
    did not report the other half: with a link in place, ``memory_key_facts_*`` and
    ``recall_*`` are the Brain's rows, and a client must not answer them from its own
    store. Printing the resolved route is how an operator sees that before a model writes
    a fact to the wrong database.
    """
    from navig.brain.link import read_brain_link_state  # noqa: PLC0415
    from navig.mcp import capability  # noqa: PLC0415

    state = read_brain_link_state()
    role = capability.install_role(use_cache=False)
    brain_owned = sorted(
        {
            contract.family
            for contract in capability.contracts().values()
            if contract.data_authority == capability.AUTHORITY_BRAIN
        }
    )
    families = ", ".join(brain_owned) or "none"

    if state.is_unreadable:
        return _check(
            "Brain data routing",
            False,
            f"brain-owned data ({families}) cannot be routed: the brain link is unreadable "
            f"({state.state}: {state.detail}) — reads and writes that belong to the Brain "
            "are refused until it is fixed",
            warn=True,
        )

    route = capability.resolve(brain_owned[0] if brain_owned else "", state=state, role=role)
    if route.is_local:
        where = (
            "this install (it is the Brain)" if role in ("brain", "all-in-one") else "this install"
        )
        return _check(
            "Brain data routing",
            True,
            f"{families} are served by {where}; files, shell and browser actions stay local",
        )
    if route.is_refused:
        return _check(
            "Brain data routing",
            False,
            f"this install is a {role} with no usable brain link, so {families} have no "
            "authority to answer — memory and recall calls are refused rather than served "
            "from a local copy; run `navig os link` against the Brain",
        )
    return _check(
        "Brain data routing",
        True,
        f"{families} are served by the Brain at {state.link.base_url if state.link else '?'} "
        f"(token {state.link.token_fingerprint or 'absent' if state.link else 'absent'}); files, "
        "shell and browser actions run here",
    )


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
                # NOT automatically ✓: ask WHO holds it. A bound port answers "is something
                # listening"; a second NAVIG authority answers "is my thing listening" with a
                # no, and used to be reported as a healthy gateway.
                results.append(_gateway_owner_row(target_port))
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
                        _check(
                            "Daemon Port", True, f"Port {_DAEMON_PORT} is bound (daemon running)"
                        )
                    )
                else:
                    results.append(_bindability_row("Daemon Port", _DAEMON_PORT))
        except Exception as e:  # noqa: BLE001
            results.append(
                _check("Daemon Port", False, f"Socket error on port {_DAEMON_PORT}: {e}")
            )

    stale_row = _stale_pid_files_row()
    if stale_row is not None:
        results.append(stale_row)

    try:
        results.append(_brain_role_row())
    except Exception as exc:  # noqa: BLE001 — a role probe must never break the report
        results.append(
            _check("Brain role", False, f"could not determine ({type(exc).__name__})", warn=True)
        )

    try:
        results.append(_brain_data_routing_row())
    except Exception as exc:  # noqa: BLE001 — routing probe must never break the report
        results.append(
            _check(
                "Brain data routing",
                False,
                f"could not determine ({type(exc).__name__})",
                warn=True,
            )
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
    """Check skills: how many the LOADER finds, across every directory it scans.

    This used to look for ``<core>/skills`` — a directory that has not existed since
    the builtin skills moved under ``navig/builtin/skills`` and ``navig/skills/builtin``
    — so every install, forever, warned ``Skills dir not found (non-fatal)`` while
    ``navig skill tree`` listed 50+ skills one command later. A warning that can never
    go green trains the operator to skim past the one row that might matter. Ask the
    same loader the CLI uses; it already knows every root (builtin, store, workspace,
    project-local).
    """
    try:
        from navig.skills.loader import get_skill_dirs, load_all_skills

        dirs = [d for d in get_skill_dirs() if d.exists()]
        skills = load_all_skills(dirs)
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        return [_check("Skills", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not skills:
        return [
            _check(
                "Skills", False, "none found — builtin skills missing from this install", warn=True
            )
        ]
    return [_check("Skills", True, f"{len(skills)} found across {len(dirs)} dir(s)")]


def _gateway_owner(port: int) -> str | None:
    """Command line of the process LISTENING on *port* — ``None`` when unknown.

    Reachability alone cannot answer "is the gateway running": once no gateway holds
    the port, the telegram worker takes it (same split-brain as gateway.json) and
    answers, so this row stayed green while there was no gateway — and therefore no
    Lighthouse uplink — at all. ``None`` (psutil absent, permissions) means we only
    know the port answers; callers must then claim reachability only.
    """
    try:
        import psutil  # noqa: PLC0415 - optional dependency, and only needed for this row
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.pid:
                continue
            if getattr(conn.laddr, "port", None) == port:
                return " ".join(psutil.Process(conn.pid).cmdline() or [])
    except Exception:  # noqa: BLE001 — an unreadable process must not fail the report
        return None
    return None


def _owner_label(cmdline: str) -> str:
    """A short, non-path name for an owning process — its `-m` module, else the exe.

    The `-m` module IS the identity (`navig.daemon.telegram_worker` vs the gateway);
    a path regex is wrong here, because the install path itself usually contains
    `navig` (``C:\\…\\navig\\venv\\Scripts\\pythonw.exe``) and wins the match.
    """
    parts = cmdline.split()
    if "-m" in parts:
        idx = parts.index("-m")
        module = parts[idx + 1] if idx + 1 < len(parts) else ""
        if module.lower() in ("navig", "navig.exe") and idx + 2 < len(parts):
            return f"navig {parts[idx + 2]}"  # `-m navig gateway …` → "navig gateway"
        return module or "unknown"
    first = parts[0] if parts else "unknown"
    return first.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]


def _is_gateway_cmdline(cmdline: str) -> bool:
    """Is this the gateway itself — ``python[w] -m navig gateway start [--port N]``?"""
    return "navig gateway start" in " ".join(cmdline.split()).lower()


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
        owner = _gateway_owner(port)
        if owner is None:
            # Reachability IS verified — claim only that, never the gateway's identity.
            results.append(_check("Gateway", True, f"Responding at 127.0.0.1:{port}"))
        elif _is_gateway_cmdline(owner):
            results.append(_check("Gateway", True, f"Responding at 127.0.0.1:{port} (gateway up)"))
        else:
            # Something else answers on the gateway's port: with no gateway there is
            # no uplink either, so a green "Responding" here is the phantom green
            # that hid a two-day outage.
            results.append(
                _check(
                    "Gateway",
                    False,
                    f"answering on 127.0.0.1:{port}, but that port belongs to "
                    f"{_owner_label(owner)} — no gateway process "
                    "(start with: navig service start)",
                    warn=True,
                )
            )
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


def check_uplink(port: int | None = None) -> list[tuple[str, bool, str]]:
    """Is the brain's PUBLIC path up — the outbound Lighthouse uplink?

    The row exists for a measured outage: with `gateway: false` in
    `~/.navig/daemon/config.json`, every `navig service` boot started the Telegram
    worker and never a gateway — so no uplink. The edge then answered
    `503 {"offline":true}` for EVERY inbound webhook (Stripe sales, the cloud Deck,
    edge-routed Telegram) for two days while `service status`, `gateway.json` and the
    Gateway row all stayed green: no `Registered gateway` in `daemon.log` between
    2026-10-18 13:04 and 2026-10-20, found by POSTing to the real edge URL.

    It asks the daemon itself (`GET /api/deck/cloud/status` — the same read
    `navig lighthouse status` prints). Could-not-verify is a warning, never a tick.
    The row appears only where an edge is configured: cloud off, or a non-lighthouse
    mode, means there is no such public path to report on.
    """
    if port is None:
        try:
            from navig.gateway_client import gateway_live_defaults

            port = gateway_live_defaults()[0]
        except Exception:  # noqa: BLE001
            port = _GATEWAY_PORT

    try:
        from navig.config import ConfigManager
        from navig.core.coerce import coerce_bool

        # `global_config` is the NESTED tree (`cloud: {mode, enabled, …}`), not the flat
        # dotted-key view `navig.config.get("cloud.mode")` exposes. Read the section
        # dict and coerce its leaf: the stored value is a STRING (`navig config set
        # cloud.enabled false`), so `bool("false")` is True — hence coerce_bool, and
        # hence no raw `gcfg.get("cloud.enabled", …)` default here at all (the guard in
        # tests/quality/test_config_booleans_are_coerced.py fails that read, correctly).
        gcfg = ConfigManager().global_config or {}
        cloud = gcfg.get("cloud")
        if not isinstance(cloud, dict):
            return []  # no cloud section: this install has no public path to report on
        if not coerce_bool(cloud.get("enabled", True), default=True):
            return []  # the operator turned the public path off — nothing to report
        mode = str(cloud.get("mode") or "").strip().lower()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        return [_check("Uplink", False, f"COULD NOT VERIFY (config: {exc})", warn=True)]
    if mode != "lighthouse":
        return []  # no Lighthouse edge in this install — the question does not apply

    if not _gateway_reachable("127.0.0.1", port):
        # The Gateway row already fails loudly when the daemon is down — degrade the
        # way the event processor row does instead of duplicating that failure.
        return [_check("Uplink", False, "not checked — gateway not running", warn=True)]

    try:
        import requests  # noqa: PLC0415

        from navig.gateway_client import gateway_request_headers  # noqa: PLC0415

        resp = requests.get(
            f"http://127.0.0.1:{port}/api/deck/cloud/status",
            headers=gateway_request_headers(),
            timeout=_STATUS_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return [_check("Uplink", False, f"COULD NOT VERIFY ({exc})", warn=True)]
    if resp.status_code != 200:
        return [_check("Uplink", False, f"COULD NOT VERIFY (HTTP {resp.status_code})", warn=True)]
    try:
        info = resp.json()
    except ValueError:
        return [
            _check("Uplink", False, "COULD NOT VERIFY (status endpoint sent non-JSON)", warn=True)
        ]

    lh = info.get("lighthouse") or {}
    status = str(lh.get("status") or info.get("status") or "").strip().lower()
    if status == "online":
        return [_check("Uplink", True, "online — the edge can reach this brain")]
    return [
        _check(
            "Uplink",
            False,
            f"{status or 'unknown'} — the edge will 503 every webhook "
            "(bring it up: navig service restart)",
            warn=True,
        )
    ]


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
        return [
            _check("Event processor", False, "COULD NOT VERIFY (malformed event stats)", warn=True)
        ]

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
                _check(
                    "Default",
                    False,
                    "no default connection set (navig connect default <id>)",
                    warn=True,
                )
            )

        broken = [c for c in conns if not c.get("is_routable") and not c.get("is_default")]
        if broken:
            names = ", ".join(str(c.get("name") or c.get("connection_id")) for c in broken[:3])
            results.append(_check("Needs re-auth", False, f"{len(broken)}: {names}", warn=True))

        # In-flight logins. Only worth a line when there ARE any — an abandoned one
        # is the visible trace of a login that never completed.
        pending = report.get("pending_logins")
        if pending:
            results.append(_check("Logins in progress", True, f"{pending} (expire after 10 min)"))

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


def _invocation_repo_root() -> Path | None:
    """Repo root for doctor's repo-scoped sections.

    Delegates to ``resolve_repo_root`` so there is ONE precedence rule in the tree,
    not a second copy that can drift from it. What matters here is step 2 of that
    rule: the CLI chdir's to the ACTIVE SPACE during startup, so a bare
    ``repo_root()`` asks about the space. With the default space
    (``~/.navig-os/workspaces/my-workspace``, not a git repo) every repo-scoped
    section evaluated to empty, so the documented Repo Guard row silently never
    ran; with a space that IS a repo the row would describe that repo while
    claiming to describe yours.
    """
    from navig.commands.repo import resolve_repo_root  # noqa: PLC0415

    return resolve_repo_root()


def check_runtime_inventory() -> list[tuple[str, bool, str]]:
    """Which first-party source plugins THIS interpreter actually has, and loaded.

    The neighbours answer different questions: ``Plugin Sources`` judges the few
    plugins ``[tool.uv.sources]`` declares, the ``other Python`` rows look past this
    interpreter, and the entry-point seam check LOADS entry points (it imports them).
    None of them can say "41 source plugins, 7 installed, 7 loaded by the running
    gateway" — which is how ``navig-msstore`` sat missing from the live brain for days
    while every row stayed green (measured 2026-10-20).

    Collection is metadata-only: no plugin is imported to measure it. The rows carry the
    full per-plugin inventory (state, interpreter, entry points, location, activation,
    repair) as ``data`` for ``--json``; the console shows the summary plus one line per
    genuinely broken install.
    """
    from navig.plugins.runtime_inventory import (  # noqa: PLC0415 — lazy, like every check
        inventory_rows,
        runtime_inventory,
    )

    try:
        inventory = runtime_inventory()
    except Exception as exc:  # noqa: BLE001 — a health check must never crash the doctor
        return [_check("Runtime plugins", False, f"COULD NOT VERIFY ({exc})", warn=True)]
    rows = inventory_rows(inventory)
    return [
        _check(r["label"], r["ok"], r["detail"], warn=bool(r.get("warn")), data=r.get("data"))
        for r in rows
    ]


def check_plugin_sources() -> list[tuple[str, bool, str]]:
    """Plugins declared editable-local that actually resolve to an INSTALLED copy.

    Empty (section skipped) outside a development checkout of this repo — an end user
    who installed from PyPI has no ``core/pyproject.toml`` and nothing to compare
    against.

    Inside a checkout this catches a failure that is invisible from every other
    surface: ``[tool.uv.sources]`` says a plugin resolves to ``plugins/navig-<x>``, but
    a non-editable install shadows it, so the CLI executes a stale copy while the repo
    source moves on. Measured 2026-09-06: 4 of 12 declared plugins were shadowed, and
    the mobile plugin's ``ui`` sub-app answered "No such command" for a group
    registered unconditionally in the source — while that plugin's own 39 tests passed,
    because the gate runs ``python -m pytest`` with the plugin dir as cwd and ``-m``
    puts the source on ``sys.path`` first.

    The detection itself lives in ``navig.plugins.sources`` so that this row and
    ``navig plugin list``'s per-row state cannot drift apart.
    """
    from navig.plugins.sources import (  # noqa: PLC0415
        audit_plugin_sources,
        editable_install_command,
        uninstall_command,
        with_local_dependencies,
    )

    audit = audit_plugin_sources()
    if audit is None:
        return []

    results: list[tuple[str, bool, str]] = []
    if audit.shadowed:
        results.append(
            _check(
                "editable plugins",
                False,
                f"{len(audit.shadowed)} of {audit.declared} resolve to an installed copy, "
                f"not your source: {', '.join(audit.shadowed)} — the CLI runs the stale "
                f"code while tests read the repo. Fix: "
                f"{editable_install_command(with_local_dependencies(audit.fix_paths))}",
                warn=True,
            )
        )
    elif audit.verified:
        results.append(
            _check(
                "editable plugins",
                True,
                f"{len(audit.verified)} of {audit.declared} resolve to your source",
            )
        )
    if audit.stale:
        # Pointing at the source is not the same as being current. pip records the
        # version at install time; a later bump of the plugin and of core's floor on it
        # leaves that record behind, and the next `pip install -e core` then replaces
        # the plugin with the PyPI wheel. One command for all of them — see
        # audit_plugin_sources for why a piecemeal repair re-opens the trap.
        drift = ", ".join(f"{d} {have}→{want}" for d, have, want in audit.stale)
        fix = editable_install_command(audit.stale_fix_paths)
        if audit.below_floor:
            results.append(
                _check(
                    "stale installs",
                    False,
                    f"{', '.join(audit.below_floor)} recorded below core's own floor — "
                    f"the next `pip install -e core` replaces them with the PyPI copy. "
                    f"{len(audit.stale)} stale in all ({drift}). Fix, in one call: {fix}",
                )
            )
        else:
            results.append(
                _check(
                    "stale installs",
                    False,
                    f"{len(audit.stale)} carry the version pip recorded at install time "
                    f"({drift}); commands added since may not register, and the next "
                    f"floor bump in core pulls them from PyPI. Fix, in one call: {fix}",
                    warn=True,
                )
            )
    if audit.dead:
        # Editable from a folder that is gone: it still registers its commands, and
        # every one of them fails to import. Uninstalling removes only the record —
        # the code it pointed at no longer exists.
        results.append(
            _check(
                "dead installs",
                False,
                "installed from a folder that no longer exists: "
                + ", ".join(f"{d} ({where})" for d, where in audit.dead)
                + f" — its commands cannot load. Fix: "
                f"{uninstall_command([d for d, _ in audit.dead])}",
            )
        )
    if audit.unresolved:
        # Not installed is not a failure — an optional extra may simply be absent —
        # but it IS unverified, so it never renders as a tick.
        results.append(
            _check(
                "unverified",
                False,
                ", ".join(f"{dist} ({why})" for dist, why in audit.unresolved),
                warn=True,
            )
        )
    return results


def check_command_ownership() -> list[tuple[str, bool, str]]:
    """One owner per plugin command — two plugins declaring one verb is reported, not hidden.

    ✓ only when discovery ran and found no collision; a discovery failure warns (it could
    not verify), it never renders green.
    """
    try:
        from navig.cli.registration import command_ownership_report

        rep = command_ownership_report()
    except Exception as exc:  # noqa: BLE001 — a doctor row must not crash the report
        return [_check("Command ownership", False, f"discovery failed: {exc}", warn=True)]
    if rep.get("error"):
        return [_check("Command ownership", False, f"discovery failed: {rep['error']}", warn=True)]
    rows: list[tuple[str, bool, str]] = []
    for name, owners in sorted(rep["collisions"].items()):
        rows.append(
            _check(
                f"navig {name}",
                False,
                f"declared by {', '.join(owners)} — {owners[0]} owns it; uninstall or rename the other",
                warn=True,
            )
        )
    for name, owner in sorted(rep["shadowed"].items()):
        rows.append(
            _check(
                f"navig {name}",
                False,
                f"{owner} declares a core command; core keeps it — the plugin's verb never runs",
                warn=True,
            )
        )
    if not rows:
        rows.append(
            _check(
                "Command ownership", True, f"{rep['commands']} plugin command(s), one owner each"
            )
        )
    return rows


def check_other_interpreters() -> list[tuple[str, bool, str]]:
    """navig in the OTHER Pythons on this machine, each judged against the source tree.

    The rows above describe only the interpreter doctor runs in. Measured 2026-10-03:
    ``navig.exe`` and the daemon ran on 3.13, the repo guard's hooks imported navig
    under the PATH ``python`` (3.14), and 3.14 kept three records below core's floor
    after 3.13 was repaired — invisible, because nothing looked past its own Python.
    Neither copy can be removed (each serves a different caller), so each gets a row
    and a repair command spelled with its own executable.
    """
    from navig.plugins.interpreters import audit_other_interpreters  # noqa: PLC0415
    from navig.plugins.sources import (  # noqa: PLC0415
        editable_install_command,
        uninstall_command,
    )

    audits = audit_other_interpreters()
    if not audits:
        return []
    results: list[tuple[str, bool, str]] = []
    for a in audits:
        label = f"other Python ({a.executable})"
        if a.ok:
            results.append(_check(label, True, "every first-party record matches your source"))
            continue
        fixes = []
        if a.fix_paths:
            fixes.append(editable_install_command(a.fix_paths, executable=a.executable))
        if a.dead:
            fixes.append(uninstall_command([d for d, _ in a.dead], executable=a.executable))
        fix = " && ".join(fixes)
        problems = []
        if a.dead:
            problems.append(
                "installed from a folder that no longer exists: "
                + ", ".join(f"{d} ({where})" for d, where in a.dead)
            )
        if a.shadowed:
            problems.append(f"installed as a plain copy, not your source: {', '.join(a.shadowed)}")
        if a.below_floor:
            problems.append(
                f"below core's floor (its next core install swaps them for PyPI): "
                f"{', '.join(a.below_floor)}"
            )
        if a.stale:
            problems.append(
                f"{len(a.stale)} stale: "
                + ", ".join(f"{d} {have}→{want}" for d, have, want in a.stale)
            )
        results.append(
            _check(
                label,
                False,
                "; ".join(problems) + f". Fix: {fix}",
                warn=not (a.shadowed or a.below_floor or a.dead),
            )
        )
    return results


# ── Release: do the tree, the newest tag and PyPI agree on what is shipped? ────
#
# Three places each answer "what version is navig?" and nothing compared them. Measured
# 2026-09-19: pyproject.toml 3.25.0, PyPI 3.25.0, newest origin tag v3.24.0 — 3.25.0 had
# been twine-uploaded by hand with no tag (the org's Actions is billing-blocked, so the tag
# workflow never fires), and that stayed invisible for two weeks. The row only exists inside
# a development checkout (an end user has no pyproject.toml to compare against).

_PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"(?P<v>\d+\.\d+\.\d+)"\s*$')
_TAG_VERSION_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def _semver_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _judge_release(
    tree: str,
    manifest: str | None,
    tag: str | None,
    pypi: str | None,
    *,
    pending_entries: int = 0,
    pending_fragments: int = 0,
    pypi_error: str | None = None,
) -> list[CheckResult]:
    """Three rows from the four facts. Pure, so every disagreement shape is pinned.

    ``tag`` and ``pypi`` are plain ``X.Y.Z`` (or None); ``pypi_error`` says why PyPI could
    not be read, which is a WARN — never a tick over an unknown.
    """
    rows: list[CheckResult] = []

    if manifest is None:
        rows.append(
            _check(
                "Release · tree",
                False,
                f"pyproject {tree} · latest.json missing or unreadable — python tools/_version_sync.py",
                warn=True,
            )
        )
    elif manifest != tree:
        rows.append(
            _check(
                "Release · tree",
                False,
                f"pyproject {tree} but latest.json says {manifest} — python tools/_version_sync.py",
            )
        )
    else:
        rows.append(_check("Release · tree", True, f"pyproject {tree} · latest.json {tree}"))

    if tag is None:
        rows.append(
            _check(
                "Release · tag",
                False,
                f"no v* tag in this checkout — {tree} has never been tagged here (git fetch --tags?)",
                warn=True,
            )
        )
    elif tag == tree:
        rows.append(_check("Release · tag", True, f"v{tree} tagged"))
    elif _semver_key(tag) < _semver_key(tree):
        rows.append(
            _check(
                "Release · tag",
                False,
                f"newest tag is v{tag}, tree is {tree} — not tagged yet: bash tools/release.sh {tree}",
                warn=True,
            )
        )
    else:
        rows.append(
            _check(
                "Release · tag",
                False,
                f"tag v{tag} exists but the tree says {tree} — that tag points at a tree of another version",
            )
        )

    pending = ""
    if pending_entries or pending_fragments:
        parts = []
        if pending_entries:
            parts.append(
                f"{pending_entries} [Unreleased] entr{'y' if pending_entries == 1 else 'ies'}"
            )
        if pending_fragments:
            parts.append(f"{pending_fragments} fragment{'s' if pending_fragments != 1 else ''}")
        pending = " · " + " + ".join(parts) + " await a release"

    if pypi is None:
        rows.append(
            _check(
                "Release · PyPI",
                False,
                f"not checked ({pypi_error or 'unreachable'}){pending}",
                warn=True,
            )
        )
    elif pypi == tree:
        rows.append(_check("Release · PyPI", True, f"{tree} published{pending}"))
    elif _semver_key(pypi) < _semver_key(tree):
        rows.append(
            _check(
                "Release · PyPI",
                False,
                f"PyPI has {pypi}, tree is {tree} — unreleased: bash tools/release.sh {tree} --publish{pending}",
                warn=True,
            )
        )
    else:
        rows.append(
            _check(
                "Release · PyPI",
                False,
                f"PyPI has {pypi} but this tree is {tree} — published from elsewhere, or the tree is behind",
            )
        )
    return rows


_NOT_ON_PYPI = "not on PyPI"


def _pypi_latest(package: str = "navig", timeout: float = 3.0) -> tuple[str | None, str | None]:
    """``(version, None)`` from PyPI's JSON, or ``(None, why)``. Offline is a reason, not a version.

    A 404 is reported as ``_NOT_ON_PYPI`` — a FACT (the package was never uploaded), unlike
    a timeout, which is an unknown. The hard-deps row judges the two differently.
    """
    if os.environ.get("NAVIG_VERSION_SYNC_OFFLINE"):
        return None, "NAVIG_VERSION_SYNC_OFFLINE"
    import json  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(
        f"https://pypi.org/pypi/{package}/json", headers={"User-Agent": "navig-doctor"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            data = json.loads(resp.read().decode("utf-8"))
        version = str(data["info"]["version"])
        return (
            (version, None)
            if _TAG_VERSION_RE.match(f"v{version}")
            else (None, f"unexpected version {version!r}")
        )
    except urllib.error.HTTPError as exc:
        return None, _NOT_ON_PYPI if exc.code == 404 else f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - offline, timeout: "not checked"
        return None, type(exc).__name__


_DEPS_BLOCK_RE = re.compile(r"(?ms)^dependencies\s*=\s*\[(.*?)^\]")
_FIRST_PARTY_DEP_RE = re.compile(r'"(navig-[a-z0-9-]+)\s*>=\s*(\d+\.\d+\.\d+)')


def _hard_first_party_deps(pyproject_text: str) -> list[tuple[str, str]]:
    """``(name, floor)`` for every ``navig-*`` package in core's REQUIRED dependencies.

    Extras are deliberately out of scope: a missing extra breaks ``pip install navig[x]``,
    a missing HARD dependency breaks ``pip install navig`` itself. Comment lines are skipped
    so prose naming a package cannot count as a dependency.
    """
    m = _DEPS_BLOCK_RE.search(pyproject_text)
    if not m:
        return []
    out: list[tuple[str, str]] = []
    for line in m.group(1).splitlines():
        if line.strip().startswith("#"):
            continue
        dep = _FIRST_PARTY_DEP_RE.search(line)
        if dep:
            out.append((dep.group(1), dep.group(2)))
    return out


def _judge_hard_deps(
    deps: list[tuple[str, str, str | None, str | None]],
) -> CheckResult | None:
    """One row from ``(name, floor, published_version, error)`` per hard first-party dep.

    Measured 2026-09-27: core has required ``navig-contacts>=0.1.0`` since #1306 and that
    package was never uploaded (404) — so the NEXT core wheel cannot be installed by anyone,
    while every Release row stayed green. ``release.sh``'s install gate refuses to ship it,
    but only at release time; this says so on every doctor run.
    """
    if not deps:
        return None
    missing = [n for n, _f, _v, e in deps if e == _NOT_ON_PYPI]
    behind = [
        f"{n} {v} < {f}"
        for n, f, v, _e in deps
        if v is not None and _semver_key(v) < _semver_key(f)
    ]
    unknown = [f"{n} ({e})" for n, _f, v, e in deps if v is None and e != _NOT_ON_PYPI]
    if missing or behind:
        parts = [f"{n} not on PyPI" for n in missing] + [f"PyPI has {b}" for b in behind]
        names = " ".join(n for n in missing) or " ".join(b.split()[0] for b in behind)
        return _check(
            "Release · hard deps",
            False,
            "; ".join(parts)
            + " — the next core wheel is uninstallable (the release gate will refuse it). "
            + f"Publish in the same session as core: node scripts/publish-plugins.mjs {names} --publish",
        )
    if unknown:
        return _check("Release · hard deps", False, "not checked: " + ", ".join(unknown), warn=True)
    return _check(
        "Release · hard deps", True, " · ".join(f"{n} {v} published" for n, _f, v, _e in deps)
    )


def _newest_local_tag(root: Path) -> str | None:
    """The highest ``vX.Y.Z`` tag this checkout knows, as ``X.Y.Z``; None when there is none."""
    import subprocess  # noqa: PLC0415

    try:
        res = subprocess.run(
            ["git", "-C", str(root), "tag", "-l", "v[0-9]*", "--sort=-v:refname"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        m = _TAG_VERSION_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def _pending_changelog(core_dir: Path) -> tuple[int, int]:
    """``(entries in [Unreleased], fragments in changelog.d)`` — what the next release folds."""
    entries = 0
    try:
        text = (core_dir / "CHANGELOG.md").read_text(encoding="utf-8")
        start = text.index("## [Unreleased]")
        nxt = text.find("\n## ", start + 1)
        block = text[start : nxt if nxt != -1 else len(text)]
        entries = sum(1 for ln in block.splitlines() if ln.startswith("- **"))
    except (OSError, ValueError):
        pass
    fragments = 0
    frag_dir = core_dir / "changelog.d"
    try:
        if frag_dir.is_dir():
            fragments = sum(
                1 for p in frag_dir.iterdir() if p.suffix == ".md" and p.name != "README.md"
            )
    except OSError:
        pass
    return entries, fragments


def _invocation_checkout_root() -> Path | None:
    """The checkout the operator is STANDING in — a linked worktree's own root, not main's.

    ``_invocation_repo_root`` deliberately resolves a worktree to the MAIN tree (right for
    the repo-guard rows, which key on main's ``.dev/``). A release row's subject is the
    tree's own ``pyproject.toml``/``latest.json``/``CHANGELOG.md``: measured 2026-09-29,
    doctor run from a worktree on current main described the main checkout 8 commits
    behind (two hard deps instead of four).
    """
    import subprocess  # noqa: PLC0415

    from navig.platform.paths import invocation_cwd  # noqa: PLC0415

    try:
        res = subprocess.run(
            ["git", "-C", str(invocation_cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return _invocation_repo_root()
    if res.returncode != 0 or not res.stdout.strip():
        return _invocation_repo_root()
    return Path(res.stdout.strip())


def check_release() -> list[tuple[str, bool, str]]:
    """Tree · tag · PyPI agreement for the core package, inside a development checkout only."""
    root = _invocation_checkout_root()
    if root is None:
        return []
    core_dir = root / "core" if (root / "core" / "pyproject.toml").is_file() else root
    pyproject = core_dir / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        # A pyproject that exists but cannot be read is an unknown, not "nothing to report".
        return [_check("Release · tree", False, f"COULD NOT VERIFY ({exc})", warn=True)]
    if 'name = "navig"' not in text:
        return []  # some other project's checkout
    m = _PYPROJECT_VERSION_RE.search(text)
    if not m:
        return [
            _check("Release · tree", False, 'pyproject.toml has no `version = "X.Y.Z"`', warn=True)
        ]
    tree = m.group("v")

    manifest: str | None = None
    try:
        import json  # noqa: PLC0415

        manifest = (
            str(
                json.loads((core_dir / "latest.json").read_text(encoding="utf-8")).get("version")
                or ""
            )
            or None
        )
    except (OSError, ValueError):
        manifest = None

    tag = _newest_local_tag(root)
    pypi, pypi_error = _pypi_latest()
    entries, fragments = _pending_changelog(core_dir)
    rows: list[tuple[str, bool, str]] = list(
        _judge_release(
            tree,
            manifest,
            tag,
            pypi,
            pending_entries=entries,
            pending_fragments=fragments,
            pypi_error=pypi_error,
        )
    )
    deps = [(n, f, *_pypi_latest(n)) for n, f in _hard_first_party_deps(text)]
    deps_row = _judge_hard_deps(deps)
    if deps_row is not None:
        rows.append(deps_row)
    return rows


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
                f"{len(foreign)} running that NAVIG did not launch (pid {pids}) — left untouched",
            )
        )
    return results


#: Below this much regenerable data the profile store is not worth a doctor line.
#: 512 MB is roughly one Chrome cache set; the on-device model alone is 4 GB per profile.
_PROFILE_RECLAIM_THRESHOLD = 512 * 1024 * 1024


def check_browser_profiles() -> list[tuple[str, bool, str]]:
    """Disk held by browser profiles that can be reclaimed WITHOUT losing a login.

    `~/.navig/cdp-profiles` reached 11.7 GB on the machine this was written for, and was
    read for months as "logins we can't touch". Measured: 8.4 GB of it was Chrome's
    on-device AI model, downloaded separately into two profiles; the logins were 7 MB.
    `navig cdp profile usage` says so — if someone remembers to run it. This turns that
    command into a light that comes on.

    Two rows, each only when there is something to reclaim: regenerable data (the model,
    caches — `vacuum` keeps every login) and orphaned dirs no profile points at (`prune`).
    Returns [] when the store is lean, so the section stays quiet on a healthy machine.
    """
    try:
        from navig.browser import cdp_actions
        from navig.commands.cdp import _fmt_bytes

        usage = cdp_actions.profile_usage()
        reclaimable = [r for r in usage["named"] if not r.get("real") and not r.get("running")]
        regen = sum(cdp_actions.regenerable_bytes(r["user_data_dir"]) for r in reclaimable)
        orphans = usage.get("orphans") or []
        orphan_bytes = sum(int(o.get("bytes") or 0) for o in orphans)
    except Exception as exc:  # noqa: BLE001 — doctor must never be the thing that breaks
        # Never `return []` on failure: a hidden section reads as "nothing to reclaim".
        logger.debug("browser profile disk check failed: %s", exc)
        return [_check("Browser profiles", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    results: list[tuple[str, bool, str]] = []
    if regen >= _PROFILE_RECLAIM_THRESHOLD:
        results.append(
            _check(
                "Browser profiles",
                False,
                f"{_fmt_bytes(regen)} of caches + Chrome's on-device model across "
                f"{len(reclaimable)} profile(s) — every login is kept: navig cdp profile vacuum --all",
                warn=True,
            )
        )
    if orphans:
        results.append(
            _check(
                "Orphaned profile dirs",
                False,
                f"{len(orphans)} dir(s) no profile points at ({_fmt_bytes(orphan_bytes)}) — "
                f"navig cdp profile prune {orphans[0]['name']}",
                warn=True,
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
    results.append(
        _check(
            "ffmpeg",
            bool(ffmpeg),
            ffmpeg or "not on PATH — video/audio transcription and frame OCR are skipped",
            warn=True,
        )
    )

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
            results.append(
                _check(
                    "OCR (on-screen text)",
                    False,
                    f"{detail} — but {gap}. Text in that script reads as noise.",
                    warn=True,
                )
            )
        else:
            lang = ocr_language()
            results.append(
                _check(
                    "OCR (on-screen text)",
                    True,
                    f"{detail} · reads {lang}" if lang else detail,
                )
            )
    else:
        results.append(
            _check(
                "OCR (on-screen text)",
                False,
                f"{reason} — image/video text is silently skipped. Fix: {OCR_INSTALL_HINT}",
                warn=True,
            )
        )

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
                _check(
                    "Telegram webhook", False, "custom host (not the lighthouse edge)", warn=True
                )
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
    return _webhook_tenant_rows() + _miniapp_button_row() + _os_remote_row()


def check_payments() -> list[tuple[str, bool, str]]:
    """Payments rows — its own section, because this one makes an OUTBOUND network call
    and `check_reachability()` is a tested contract (its consumers assert the exact rows,
    and none of them should inherit a live HTTPS request)."""
    return _stripe_webhook_row()


#: The payments/API host — the same default `_BROKER_DEFAULT` uses, and the host every
#: checkout redirect comes from.
_API_BASE = "https://api.navig.run"

#: The Stripe account that sells NAVIG (an identifier, not a secret). An organization key
#: reaches several accounts, so every request names this one in `Stripe-Context`.
_NAVIG_STRIPE_ACCOUNT = "acct_1TfjeOGk0ZWNfvDo"

#: How far back a failed delivery still counts. Stripe retries a failed delivery for up to
#: three days, so a week covers every retry of anything that has failed recently.
_DELIVERY_WINDOW_DAYS = 7


def _navig_billing_key() -> str | None:
    """The vault's ``stripe/master`` key — present ONLY on the install that runs NAVIG's own
    billing. ``None`` means "this install does not sell NAVIG", which is every user's install.

    Deliberately not wrapped in a try: a vault that RAISES must surface as a warning in the
    row, not as a silently missing row (``test_no_check_hides_its_own_failure_...``).
    """
    from navig.vault import get_vault  # noqa: PLC0415

    cred = get_vault().get("stripe", profile_id="master", caller="navig doctor")
    if cred is None:
        return None
    key = str(cred.get_secret("api_key") or "").strip()
    return key or None


def _stripe_webhook_row() -> list[tuple[str, bool, str]]:
    """Is Stripe delivering NAVIG's payments events? Shown only where NAVIG's billing runs.

    The failure this catches is silent by construction: a rotated webhook secret, a moved
    endpoint, or a Worker answering 500 stops `checkout.session.completed` — and every
    renewal — while every other light stays green (the bot-deaf shape: nothing errors, the
    channel is simply gone).

    It asks STRIPE, not the silence. The first version of this row inferred failure from how
    long ago a webhook last arrived, and that cannot work: the endpoint subscribes to five
    SALE-driven events only, so a week with no sales and a dead channel look identical. Stripe
    knows the difference — an endpoint it has given up on is ``status: disabled``, and every
    event it could not deliver is listed by ``GET /v1/events?delivery_success=false`` — and
    both answer with or without traffic.

    Owner-only: this row is about navig.run's own billing, so it appears only on the install
    whose vault holds the ``stripe/master`` key. A customer's doctor shows nothing here.

    Could-not-verify is a warning, never a tick; a disabled endpoint is a verified failure.
    """
    try:
        key = _navig_billing_key()
    except Exception as exc:  # noqa: BLE001 - doctor must never crash on a check
        return [_check("Stripe webhook", False, f"COULD NOT VERIFY (vault: {exc})", warn=True)]
    if not key:
        return []  # not the install that sells NAVIG — there is nothing of ours to report

    endpoint_url = f"{_API_BASE}/api/webhooks/stripe"
    headers = {"Authorization": f"Bearer {key}", "Stripe-Version": "2025-03-31.basil"}
    if key.startswith("sk_org_"):
        headers["Stripe-Context"] = _NAVIG_STRIPE_ACCOUNT  # an org key must name its account
    since = int(time.time()) - _DELIVERY_WINDOW_DAYS * 86400

    try:
        import requests  # noqa: PLC0415

        eps = requests.get(
            "https://api.stripe.com/v1/webhook_endpoints",
            params={"limit": 100},
            headers=headers,
            timeout=4.0,
        )
        if eps.status_code != 200:
            return [
                _check(
                    "Stripe webhook",
                    False,
                    f"COULD NOT VERIFY (Stripe answered HTTP {eps.status_code})",
                    warn=True,
                )
            ]
        failed = requests.get(
            "https://api.stripe.com/v1/events",
            params={"delivery_success": "false", "created[gte]": since, "limit": 100},
            headers=headers,
            timeout=4.0,
        )
        if failed.status_code != 200:
            return [
                _check(
                    "Stripe webhook",
                    False,
                    f"COULD NOT VERIFY (Stripe answered HTTP {failed.status_code})",
                    warn=True,
                )
            ]
        endpoints = eps.json().get("data") or []
        undelivered = failed.json().get("data") or []
    except Exception as exc:  # noqa: BLE001 - doctor must never crash on a check
        return [_check("Stripe webhook", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    ours = [e for e in endpoints if str(e.get("url") or "").rstrip("/") == endpoint_url]
    if not ours:
        return [
            _check(
                "Stripe webhook",
                False,
                f"no Stripe endpoint points at {endpoint_url} — no sale can mint a licence",
                warn=True,
            )
        ]
    disabled = [e for e in ours if e.get("status") != "enabled"]
    if disabled:
        # Verified, not inferred: Stripe has stopped trying. An ✗, not a ⚠.
        return [
            _check(
                "Stripe webhook",
                False,
                f"Stripe has DISABLED {disabled[0].get('id')} — no sale reaches us; "
                "re-enable it in Stripe → Developers → Webhooks, then fix what it failed on",
            )
        ]
    if undelivered:
        n = f"{len(undelivered)}+" if len(undelivered) >= 100 else str(len(undelivered))
        kinds = ", ".join(sorted({str(e.get("type")) for e in undelivered})[:3])
        return [
            _check(
                "Stripe webhook",
                False,
                f"{n} event(s) not delivered in {_DELIVERY_WINDOW_DAYS}d ({kinds}) — "
                "Stripe is retrying; check the endpoint's failures in the Dashboard",
                warn=True,
            )
        ]
    return [
        _check(
            "Stripe webhook",
            True,
            f"endpoint enabled · every delivery succeeded ({_DELIVERY_WINDOW_DAYS}d)",
        )
    ]


def _os_remote_row() -> list[tuple[str, bool, str]]:
    """Can a paired phone reach NAVIG OS here? Only asked once remote access is in play.

    The failure this catches is silent by construction: an edge older than 1.3.0 answers
    every phone with "update NAVIG" or a dead socket, while the desktop, the bot and every
    other light here stay green. Could-not-verify is a warning, never a tick.
    """
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool
        from navig.store.os_devices import get_os_device_store

        cm = get_config_manager()
        mode = str(cm.get("cloud.mode") or "").strip().lower()
        edge = str(cm.get("cloud.lighthouse_url") or "").strip().rstrip("/")
        devices = get_os_device_store().list_devices()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        return [_check("Remote NAVIG OS", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not devices and mode != "lighthouse":
        return []  # no relay and nothing paired — the question does not apply
    n = len(devices)
    paired = f"{n} paired device{'s' if n != 1 else ''}"
    if not coerce_bool(cm.get("os.remote.enabled"), default=True):
        return [_check("Remote NAVIG OS", True, f"turned off ({paired})")]
    if mode != "lighthouse" or not edge:
        return [
            _check(
                "Remote NAVIG OS",
                False,
                f"{paired}, but no Lighthouse relay is configured — set one up: navig lighthouse login",
                warn=True,
            )
        ]

    from navig.cloud.os_relay import edge_supports_os
    from navig.commands.lighthouse import _deployed_worker_version

    version = _deployed_worker_version(edge)
    if not version:
        return [
            _check(
                "Remote NAVIG OS",
                False,
                f"COULD NOT VERIFY (relay at {edge} did not answer)",
                warn=True,
            )
        ]
    if not edge_supports_os(version):
        return [
            _check(
                "Remote NAVIG OS",
                False,
                f"relay v{version} predates remote NAVIG OS — update it: navig lighthouse redeploy",
                warn=True,
            )
        ]
    return [_check("Remote NAVIG OS", True, f"relay v{version} · {paired}")]


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
        return [_check("Pending approvals", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not pending and not resumable:
        return []

    # Rows go through `_check(..., warn=True)` like every other section. These two used
    # to be raw `(label, False, text)` tuples, and the renderer prints `row[2]` verbatim
    # while `_shape_report` reads the glyph from `row[0]` — so on the terminal they had no
    # ⚠, no indent and no label, and in `--json` they counted as FAILED with an empty
    # label. A pending approval is a warning: something is waiting on the operator, not
    # something broken.
    rows: list[tuple[str, bool, str]] = []
    if pending:
        newest = max((e.get("asked_at") or 0) for e in pending.values())
        age_min = max(0, int((time.time() - newest) / 60))
        rows.append(
            _check(
                "Waiting on you",
                False,
                f"{len(pending)} approval(s) unanswered (most recent {age_min}m ago) — "
                "answer from Telegram or the deck",
                warn=True,
            )
        )

    stale = [rid for rid, e in resumable.items() if resume.is_too_old(e)]
    if stale:
        rows.append(
            _check(
                "Too old to resume",
                False,
                f"{len(stale)} approved-too-late record(s) past "
                f"{int(resume.max_age_seconds() / 60)}m — answering these now re-runs nothing; "
                "re-issue the request instead",
                warn=True,
            )
        )
    return rows


def check_gateway_auth() -> list[tuple[str, bool, str]]:
    """Is anything actually authenticating requests to the local gateway?

    With no token, ``require_bearer_auth`` now **fails closed** (503 on every guarded
    route, except explicit ``allow_anonymous`` ones) — so a missing token means the
    seventeen route modules behind it, including ``POST /approval/{id}/respond``, REFUSE
    every request: the gateway is unusable rather than unauthenticated. The row's job is
    to say which of the two worlds the operator is in, and how to get the token back.

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
        host = (
            gateway_cfg.get("host", "127.0.0.1") if isinstance(gateway_cfg, dict) else "127.0.0.1"
        )

        if token:
            return [_check("Gateway auth", True, f"bearer token set · bound to {host}")]

        has_run = _gateway_has_ever_run()

        if not has_run:
            return [
                _check(
                    "Gateway auth",
                    True,
                    "no token yet — one is generated and saved the first time the gateway starts",
                )
            ]

        local_only = str(host).strip() in {"127.0.0.1", "localhost", "::1"}
        detail = (
            f"this gateway has run but has NO token, so its admin routes — including "
            f"approval responses — REFUSE every request with 503 (fail-closed; the "
            f"startup mint could not write to your config). Bound to {host}"
            + (
                " (local processes only — the CLI and deck are locked out). Set one "
                "now: navig config set gateway.auth.token <secret>"
                if local_only
                else " — this is reachable off this machine, so every client is "
                "locked out until it is fixed. Set one NOW: "
                "navig config set gateway.auth.token <secret>"
            )
        )
        # Bound to a non-loopback address with no token is not a warning, it is a hole.
        return [_check("Gateway auth", False, detail, warn=local_only)]
    except Exception as exc:  # noqa: BLE001 — could-not-look is a warning, never a ✓
        return [_check("Gateway auth", False, str(exc)[:120], warn=True)]


def check_policy_default() -> list[tuple[str, bool, str]]:
    """What PolicyGate does with actions no rule matches — posture, not health.

    ``allow`` is a legitimate operator choice (it is the shipped default), so the
    row reports it green with the fail-closed endpoint spelled out; a TYPO'd value
    is a fault because Gate 0 made PolicyGate treat it as ``deny`` — the config and
    the running behavior must not silently disagree. An unreadable config is a
    warning, never a ✓ (doctor-honesty rule).
    """
    try:
        from navig.config import ConfigManager

        gw = ConfigManager().get("gateway", {}) or {}
        policy = gw.get("policy") if isinstance(gw, dict) else None
        if not isinstance(policy, dict):
            policy = {}
        default = str(policy.get("default", "allow")).strip().lower()
        rules = policy.get("rules") or []
        rule_n = len(rules) if isinstance(rules, list) else 0
        if default in ("deny", "require_approval"):
            return [
                _check(
                    "Gateway policy",
                    True,
                    f"default: {default} · {rule_n} rule(s) — unlisted actions are "
                    "refused or approval-gated (fail-closed)",
                )
            ]
        if default == "allow":
            return [
                _check(
                    "Gateway policy",
                    True,
                    f"default: allow · {rule_n} rule(s) — unlisted actions pass; "
                    "fail-closed endpoint: "
                    "navig config set gateway.policy.default deny",
                )
            ]
        return [
            _check(
                "Gateway policy",
                False,
                f"unknown default {default!r} — PolicyGate fails CLOSED (denies) "
                "until gateway.policy.default is fixed "
                "(allow | require_approval | deny)",
                warn=True,
            )
        ]
    except Exception as exc:  # noqa: BLE001 — could-not-look is a warning, never a ✓
        return [_check("Gateway policy", False, str(exc)[:120], warn=True)]


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
            mistyped = configured is not None and str(configured).strip().lower() not in {
                t.value for t in ServerTrust
            }

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
                results.append(_check("Incidents", False, incidents.describe(entry), warn=True))
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
                    _check(
                        "Key recovery",
                        True,
                        "deck.api_key is mirrored — a config wipe is survivable",
                    )
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
        results.append(_check("In-flight ops", True, f"{running} operation(s) currently running"))
    return results


def check_desktop_apps() -> list[tuple[str, bool, str]]:
    """Which Anchor is installed, whether it is running, and whether login starts THAT one.

    Anchor ran at every login for weeks as a development binary
    (``target/debug/navig-anchor.exe``, whose frontend is a dev server that is never up
    at boot), showing a browser error page -- and nothing on the machine could say so.
    Version alone cannot tell two builds apart (every rebuild is "0.2.0"), so the
    identity is the binary's own path and mtime, read live, exactly as About shows them.

    Three rows, each about one fact, none green over an unknown:
    - installed: the per-user (NSIS) or per-machine path, with the build time;
    - running: a live process from THAT path (a running dev copy is reported as such);
    - startup: the HKCU Run entry -- present, pointing at the installed binary, and
      quoted (an unquoted path under a directory with spaces lets ``C:\\Program.exe``
      win the race at login). Pointing at a dev binary is the original outage and is a
      warn with the fix named.
    Windows only; elsewhere there is nothing to report and no row is emitted.
    """
    if sys.platform != "win32":
        return []
    results: list[tuple[str, bool, str]] = []
    try:
        import os as _os

        candidates = [
            Path(_os.environ.get("LOCALAPPDATA", "")) / "NAVIG Anchor" / "navig-anchor.exe",
            Path(_os.environ.get("ProgramFiles", "")) / "NAVIG Anchor" / "navig-anchor.exe",
        ]
        installed = next((p for p in candidates if p.is_file()), None)
        if installed is None:
            results.append(
                _check(
                    "Anchor",
                    True,
                    "not installed (optional) — npm run build:anchor, then run the installer",
                )
            )
            return results

        import datetime as _dt

        built = _dt.datetime.fromtimestamp(installed.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        results.append(_check("Anchor installed", True, f"{installed} · built {built}"))

        # Running? Look for the process by PATH, not by name: a dev copy is also
        # "navig-anchor.exe", and telling them apart is the whole point.
        try:
            import psutil  # noqa: PLC0415 - optional

            exes: list[str | None] = []
            for proc in psutil.process_iter(["name", "exe"]):
                if (proc.info.get("name") or "").lower() != "navig-anchor.exe":
                    continue
                exes.append(proc.info.get("exe") or None)
            results.append(_judge_anchor_running(exes, installed))
        except ImportError:
            results.append(
                _check("Anchor running", False, "not checked (psutil missing)", warn=True)
            )

        # Startup registration: the exact regression check for the original outage.
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                raw, _kind = winreg.QueryValueEx(key, "NAVIG Anchor")
            command = str(raw).strip()
        except FileNotFoundError:
            results.append(
                _check("Anchor at login", True, "off — enable in Settings → Launch at login")
            )
            return results

        results.append(_judge_anchor_login(command, installed))
    except Exception as exc:  # noqa: BLE001 — never crash the doctor; say what was not checked
        results.append(
            _check("Anchor", False, f"not checked ({type(exc).__name__}: {exc})", warn=True)
        )
    return results


def _judge_anchor_running(exes: list[str | None], installed: Path) -> CheckResult:
    """One row from the exe path of every live ``navig-anchor.exe`` (``None`` = unreadable).

    A process whose path could not be read -- an elevated Anchor seen from a non-elevated
    doctor, where psutil hands back no ``exe`` -- is neither "ours" nor "a different one".
    It is an unknown, and an unknown is a warn: never a tick, and never an accusation
    naming a blank path.
    """
    readable = [e for e in exes if e]
    unreadable = len(exes) - len(readable)
    target = installed.resolve()
    same = [e for e in readable if Path(e).resolve() == target]
    other = [e for e in readable if e not in same]
    if same:
        detail = f"pid(s) from the installed binary: {len(same)}"
        if other:
            detail += f" · a different copy is ALSO running: {other[0]}"
        return _check("Anchor running", True, detail)
    if other:
        return _check(
            "Anchor running",
            False,
            f"a DIFFERENT navig-anchor.exe is running: {other[0]} — close it and launch the installed one",
            warn=True,
        )
    if unreadable:
        return _check(
            "Anchor running",
            False,
            f"not checked — {unreadable} navig-anchor.exe process(es) whose path could not be read "
            "(running elevated? run doctor from an elevated shell to tell)",
            warn=True,
        )
    return _check("Anchor running", True, "no — launch it from the Start menu")


def _judge_anchor_login(command: str, installed: Path) -> CheckResult:
    """The verdict on a `Run` entry, given the binary that SHOULD be registered.

    Pure, so the three failure shapes can be pinned without a registry: the entry
    points at a different binary (the original outage was ``target\\debug``), the entry
    points at the right one but unquoted, or it is correct.
    """
    head = command.lstrip('"').lower()
    target = str(installed).lower()
    points_here = head.startswith(target) and head[len(target) : len(target) + 1] in ("", '"', " ")
    if not points_here:
        dev = "target\\debug" in head or "target/debug" in head
        hint = " (a development build — it shows 'can't reach this page' at login)" if dev else ""
        return _check(
            "Anchor at login",
            False,
            f"registered binary is NOT the installed one{hint}: {command} — "
            "re-enable Launch at login from the installed Anchor's Settings",
            warn=True,
        )
    if not command.startswith('"'):
        return _check(
            "Anchor at login",
            False,
            "path is unquoted — under a directory with spaces, C:\\Program.exe can win the race; "
            "toggle Launch at login off and on to re-register it quoted",
            warn=True,
        )
    return _check("Anchor at login", True, "on — points at the installed binary, quoted")


# git's own merge strategies — `merge=<name>` with one of these needs no git config entry.
_BUILTIN_MERGE_DRIVERS = frozenset({"text", "binary", "union"})


def _merge_drivers_row(root: Path) -> tuple[str, bool, str] | None:
    """A row for the custom merge drivers ``.gitattributes`` routes files to, or None.

    A driver named in ``.gitattributes`` lives in per-clone git CONFIG, which is never
    committed. When a clone lacks it, git does not error — it silently falls back to a
    line merge, so the file conflicts exactly as if the driver did not exist (navig's
    generated command list conflicted on 36 of 39 replayed concurrent pairs without one).
    Silent when the repo declares no custom driver. Reads the root ``.gitattributes`` only.
    """
    import re
    import subprocess

    try:
        text = (root / ".gitattributes").read_text(encoding="utf-8")
    except OSError:
        return None
    declared: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for name in re.findall(r"(?:^|\s)merge=([^\s]+)", line):
            if name not in _BUILTIN_MERGE_DRIVERS:
                declared.add(name)
    if not declared:
        return None

    missing: list[str] = []
    for name in sorted(declared):
        try:
            got = subprocess.run(
                ["git", "-C", str(root), "config", "--get", f"merge.{name}.driver"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _check("Merge drivers", False, f"COULD NOT VERIFY ({exc})", warn=True)
        if got.returncode != 0 or not got.stdout.strip():
            missing.append(name)

    if not missing:
        return _check("Merge drivers", True, f"registered: {', '.join(sorted(declared))}")
    # navig's own repo registers its drivers from scripts/ci-local.mjs (run by `npm install`'s
    # prepare hook); any other repo gets git's own command, since we cannot know its setup.
    hint = (
        "register it: npm install"
        if (root / "scripts" / "ci-local.mjs").is_file()
        else f'register it: git config merge.{missing[0]}.driver "<command> %O %A %B %P"'
    )
    return _check(
        "Merge drivers",
        False,
        f"{', '.join(missing)} named in .gitattributes but not registered in this clone — "
        f"git falls back to a line merge; {hint}",
        warn=True,
    )


_TWIN_TEST = "scripts/test/heavy-slot.test.mjs"
_TWIN_FILES_RE = re.compile(r"const\s+TWIN_FILES\s*=\s*\[([^\]]*)\]", re.S)
_TWIN_PIN_RE = re.compile(r"const\s+PROTOCOL_SHA256\s*=\s*'([0-9a-f]{64})'")
_TWIN_REPO_RE = re.compile(r"const\s+TWIN_REPO\s*=\s*'([^']+)'")
_TWIN_NAME_RE = re.compile(r"'([^']+)'")


def _twin_spec(test: Path) -> tuple[str, str, tuple[str, ...]] | None:
    """``(twin repo name, pinned sha, files)`` from the repo's own twin test.

    None when the file cannot be read or does not declare all three: no verdict is
    the only honest answer when the single source of truth is unavailable.
    """
    try:
        text = test.read_text(encoding="utf-8")
    except OSError:
        return None
    files_m = _TWIN_FILES_RE.search(text)
    pin_m = _TWIN_PIN_RE.search(text)
    repo_m = _TWIN_REPO_RE.search(text)
    if not (files_m and pin_m and repo_m):
        return None
    files = tuple(_TWIN_NAME_RE.findall(files_m.group(1)))
    if not files:
        return None
    return repo_m.group(1), pin_m.group(1), files


def _twin_checkout(root: Path, repo_name: str) -> Path | None:
    """Where the twin repo lives on this machine, exactly as the test resolves it.

    The twin is a sibling of the MAIN checkout, not of the running worktree, and
    ``HEAVY_SLOT_TWIN_REPO`` overrides it in both places.
    """
    import subprocess  # noqa: PLC0415

    env = os.environ.get("HEAVY_SLOT_TWIN_REPO")
    if env:
        return Path(env) if (Path(env) / ".git").exists() else None
    try:
        common = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.strip()
    except OSError:
        return None
    if not common:
        return None
    # git-common-dir is <main>/.git; the twin sits beside <main>.
    twin = Path(common).parent.parent / repo_name
    return twin if (twin / ".git").exists() else None


def _protocol_sha(parts: Any) -> str:
    """sha256 of the parts joined with a NUL, each CRLF-normalised.

    The construction the twin test uses: ``TWIN_FILES`` joined with ``\\0`` after
    stripping CRLF from each file. Normalising HERE rather than in one caller
    matters — the two checkouts have historically differed in line endings, so
    hashing the twin's raw bytes reported drift that did not exist.
    """
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(b"\0".join(part.replace(b"\r\n", b"\n") for part in parts)).hexdigest()


def _twin_protocol_sha(twin: Path, files: tuple[str, ...]) -> str | None:
    """What the twin's ``origin/main`` hashes to, or None when git cannot read it.

    A failed ``git show`` must never become a hash of nothing: the digest of an
    empty string looks exactly like a real pin, so "could not read" would be
    reported as "in step". Absence of an answer is not an answer.
    """
    import subprocess  # noqa: PLC0415

    chunks = []
    for rel in files:
        try:
            res = subprocess.run(
                ["git", "-C", str(twin), "show", f"origin/main:{rel}"],
                capture_output=True,
                check=False,
            )
        except OSError:
            return None
        if res.returncode != 0 or not res.stdout:
            return None
        chunks.append(res.stdout)
    return _protocol_sha(chunks)


def check_ci_runners() -> list[CheckResult]:
    """Self-hosted runner host health (services, shared lane dir, disk, repo ACL).

    Empty when no runner service exists on this machine. The rules live in
    ``repo_ci.ci_runner_rows`` so they are testable without a doctor run."""
    try:
        from navig.commands.repo_ci import ci_runner_rows  # noqa: PLC0415
    except ImportError:
        return []
    root = _invocation_checkout_root()
    try:
        rows = ci_runner_rows(checkout=root)
    except Exception as exc:  # noqa: BLE001 — a probe failure is an unknown verdict, never a green row
        return [
            _check(
                "CI runners",
                False,
                f"COULD NOT VERIFY — {exc.__class__.__name__}: {exc}",
                warn=True,
            )
        ]
    return [_check(label, ok, detail, warn=warn) for label, ok, detail, warn in rows]


def check_shared_protocol() -> list[tuple[str, bool, str]]:
    """Do navig and Schema still carry the SAME push-queue protocol?

    ``scripts/heavy-slot*.mjs`` is a two-repo protocol: both copies must be
    byte-identical and both tests pin the same sha256. When Schema changes them
    first, navig's pin no longer matches its ``origin/main`` and **every push
    fails** with "the twins drifted" — measured 2026-10-09, which blocked a push
    for hours with no signal short of running the gate.

    The test is the single source: this row reads ``TWIN_FILES``, ``PROTOCOL_SHA256``
    and ``TWIN_REPO`` from it, so doctor can never check a different protocol than
    the gate enforces. Empty outside a development checkout, or when the twin
    checkout is absent — the test skips in that case, and a skip is not a failure.
    """
    # The CHECKOUT ROOT, not the main tree: the gate hashes the files of whatever
    # tree is being pushed, and a worktree's copy is what its twin test reads.
    root = _invocation_checkout_root()
    if root is None:
        return []
    test = root / _TWIN_TEST
    if not test.is_file():
        return []
    spec = _twin_spec(test)
    if spec is None:
        return []
    repo_name, pin, files = spec

    try:
        ours = _protocol_sha((root / rel).read_bytes() for rel in files)
    except OSError as exc:
        # Never an empty row: "could not read my own copy" is an unknown verdict, not
        # "nothing to report", and doctor's honesty check fails on exactly that shape.
        return [
            _check(
                "Shared protocol",
                False,
                f"COULD NOT VERIFY — could not read the push-queue files ({exc})",
                warn=True,
            )
        ]

    if ours != pin:
        return [
            _check(
                "Shared protocol",
                False,
                f"the {len(files)} push-queue files changed in this checkout but "
                f"PROTOCOL_SHA256 in {_TWIN_TEST} was not updated, so the twin test will "
                f"fail here — update the pin (and the same constant in {repo_name}'s test)",
            )
        ]

    twin = _twin_checkout(root, repo_name)
    if twin is None:
        return []  # the test skips when the twin is not on this machine
    theirs = _twin_protocol_sha(twin, files)
    if theirs is None:
        return [
            _check(
                "Shared protocol",
                False,
                f"could not read {repo_name}'s origin/main — unverified, not in step",
                warn=True,
            )
        ]
    if theirs == pin:
        return [
            _check(
                "Shared protocol",
                True,
                f"identical to {repo_name} origin/main — the twin test agrees",
            )
        ]
    return [
        _check(
            "Shared protocol",
            False,
            f"{repo_name} origin/main has moved off our pin ({theirs[:12]}…): every push "
            f"fails with 'the twins drifted'. Copy each of {', '.join(files)} from "
            f"{repo_name}'s origin/main, then update PROTOCOL_SHA256 in BOTH tests",
        )
    ]


def check_running_code() -> list[tuple[str, bool, str]]:
    """Which code the daemon runs: an installed package, or a checkout on which branch.

    The daemon runs the editable install of the main checkout, so a feature branch or
    uncommitted edits left there ARE the live bot. ✓ only when it is a clean ``main``
    (or an installed package); anything else — including "could not tell" — warns.
    """
    try:
        from navig.core.code_source import describe, summary  # noqa: PLC0415

        src = describe()
    except Exception as exc:  # noqa: BLE001
        return [_check("Code the daemon runs", False, f"could not check: {exc}", warn=True)]
    return [_check("Code the daemon runs", src.ok, summary(src), warn=not src.ok)]


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
            _guard_script_state,
            lock_state,
            read_lock,
        )
        from navig.guard import HOOK_FILES

        root = _invocation_repo_root()
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
            # Wired is not current. This row used to check the EVENTS and the lock and
            # render ✓ over hook scripts that could be any age — a repo fully wired to the
            # pre-#1481 agent_lock.py (the one that blocked `navig repo new` from a worktree)
            # read "active". `guard status` has always known the difference; a green light
            # that does not consult it is the "green over unknown" trap this file names.
            stale = {}
            for name in HOOK_FILES:
                state, _path = _guard_script_state(root, settings, name)
                if state != "current":
                    stale[name] = state
            lk = lock_state(read_lock(root))
            if lk["state"] == "free":
                detail = "active — lock free"
            else:
                detail = (
                    f"active — lock {lk['state']} by session {lk.get('session', '?')} "
                    f"(age {lk.get('age_minutes', '?')}m, branch {lk.get('branch') or '?'})"
                )
            if stale:
                what = ", ".join(f"{n} {st}" for n, st in sorted(stale.items()))
                results.append(
                    _check(
                        "Repo Guard",
                        False,
                        f"{detail}; hook script(s) {what} — re-run: navig repo guard install",
                        warn=True,
                    )
                )
            else:
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

        drivers_row = _merge_drivers_row(root)
        if drivers_row is not None:
            results.append(drivers_row)
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
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        if full.returncode != 0 or not full.stdout.strip():
            return None
        short = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        s = (
            short.stdout.strip()
            if short.returncode == 0 and short.stdout.strip()
            else full.stdout.strip()[:8]
        )
        return full.stdout.strip(), s
    except Exception:  # noqa: BLE001
        return None


def _git_count(src_dir: Path, rng: str) -> int | None:
    """``git rev-list --count <rng>`` (e.g. ``A..B``); None on any failure."""
    import subprocess

    try:
        r = subprocess.run(
            ["git", "-C", str(src_dir), "rev-list", "--count", rng],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def check_daemon_log_location() -> list[tuple[str, bool, str]]:
    """Name the file the RUNNING daemon logs to — and warn when it is not the one
    everyone reads.

    `navig.log` is attached at `ConfigManager.base_dir / "navig.log"`, and base_dir
    follows the cwd into a project's `.navig/` when the daemon was restarted from
    inside one. Then `~/.navig/navig.log` stops advancing with nothing to say why:
    a reader greps it for a warning, finds a clean absence, and reads that as
    proof — in a file that received zero lines. ✓ means the daemon writes the
    global log; ⚠ names the file it writes instead. A daemon that predates the
    tracking gets ⚠ too, not silence: "unknown" is not "fine".

    Returns [] when the daemon is stopped, like the freshness row beside it.
    """
    try:
        from navig.daemon.supervisor import NavigDaemon
        from navig.platform.paths import config_dir
    except Exception:  # noqa: BLE001
        return []
    try:
        if not NavigDaemon.is_running():
            return []
        state = NavigDaemon.read_state() or {}
    except Exception as exc:  # noqa: BLE001
        return [_check("Daemon log", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    log_file = state.get("log_file") if isinstance(state, dict) else None
    if not log_file:
        return [
            _check(
                "Daemon log",
                False,
                "location unknown — the running daemon predates log tracking; "
                "restart once to record it",
                warn=True,
            )
        ]
    try:
        expected = (config_dir() / "navig.log").resolve()
        actual = Path(str(log_file)).resolve()
    except Exception:  # noqa: BLE001
        return [_check("Daemon log", False, f"writing {log_file}", warn=True)]
    if actual == expected:
        return [_check("Daemon log", True, f"writing {actual}")]
    cwd = state.get("cwd") if isinstance(state, dict) else None
    where = f" (restarted from {cwd})" if cwd else ""
    return [
        _check(
            "Daemon log",
            False,
            f"writing {actual}{where} — NOT {expected}, which will not advance; "
            "restart from outside any project to put it back",
            warn=True,
        )
    ]


def check_daemon_parent() -> list[tuple[str, bool, str]]:
    """Does the RUNNING daemon have a living parent — or is it orphan-shaped?

    A daemon spawned detached from a CLI that then exits has no parent, and that
    is the exact shape an orphan-reaping process sweep kills: the operator's
    hourly cleanup did so twice on 2026-09-14, and the death was invisible until
    its log was read. Every daemon launch path goes through the scheduled task
    (parent = the service host), so ⚠ here means an older daemon or a fallback
    spawn — and the fix is one restart. ✓ names the parent. Never ✓ over unknown.

    Returns [] when the daemon is stopped, like the rows beside it.
    """
    try:
        from navig.daemon.supervisor import NavigDaemon
    except Exception:  # noqa: BLE001
        return []
    try:
        if not NavigDaemon.is_running():
            return []
        pid = NavigDaemon.read_pid()
        info = NavigDaemon.parent_of(pid) if pid else {"shape": "unknown"}
    except Exception as exc:  # noqa: BLE001
        return [_check("Daemon parent", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    shape = info.get("shape")
    name = info.get("name") or "?"
    ppid = info.get("ppid")
    if shape == "service":
        return [
            _check("Daemon parent", True, f"{name} (pid={ppid}) — launched by the service/task")
        ]
    if shape == "orphan":
        return [
            _check(
                "Daemon parent",
                False,
                f"NONE — parent pid {ppid} is gone; this daemon was spawned detached and is "
                "orphan-shaped, which process sweeps kill (it happened twice on 2026-09-14). "
                "Relaunch it through the scheduled task: navig service restart",
                warn=True,
            )
        ]
    if shape == "process":
        return [
            _check(
                "Daemon parent",
                True,
                f"{name} (pid={ppid}) — alive; a foreground/dev launch, the daemon lives while it does",
            )
        ]
    return [
        _check("Daemon parent", False, "unknown — could not read the parent process", warn=True)
    ]


def check_daemon_heartbeat() -> list[tuple[str, bool, str]]:
    """Is the RUNNING supervisor's loop actually ticking?

    Every other daemon check asks "is the pid alive?" — which a supervisor stuck
    in a blocking call answers yes to while it restarts nothing, so a dead bot
    child stays dead with every light green. The supervisor touches
    ``state.json`` every ``heartbeat_s``; a heartbeat older than three intervals
    on a live process is a wedge. ⚠ for a daemon that predates heartbeats ("I
    cannot tell" is not "fine") and for a reading that could not be taken.

    Returns [] when the daemon is stopped, like the rows beside it.
    """
    try:
        from navig.daemon.supervisor import HEARTBEAT_S, HEARTBEAT_STALE_S, NavigDaemon
    except Exception:  # noqa: BLE001
        return []
    try:
        if not NavigDaemon.is_running():
            return []
        state = NavigDaemon.read_state() or {}
        last = NavigDaemon.last_seen_alive()
    except Exception as exc:  # noqa: BLE001
        return [_check("Daemon heartbeat", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    interval = state.get("heartbeat_s") if isinstance(state, dict) else None
    if not interval or not last:
        return [
            _check(
                "Daemon heartbeat",
                False,
                "none — the running daemon predates heartbeat tracking; restart once to enable it",
                warn=True,
            )
        ]
    from datetime import datetime, timezone  # noqa: PLC0415

    try:
        beat = datetime.fromisoformat(last)
        age = (datetime.now(timezone.utc) - beat).total_seconds()
        stale_after = float(interval) * (HEARTBEAT_STALE_S / HEARTBEAT_S)
    except (TypeError, ValueError) as exc:
        return [_check("Daemon heartbeat", False, f"COULD NOT VERIFY ({exc})", warn=True)]
    if age > stale_after:
        return [
            _check(
                "Daemon heartbeat",
                False,
                f"the supervisor process is alive but its loop has not ticked for "
                f"{int(age)}s (expected every {int(float(interval))}s) — a wedged supervisor "
                "cannot restart a dead child; restart it (navig service restart)",
                warn=True,
            )
        ]
    return [_check("Daemon heartbeat", True, f"supervising — last tick {int(age)}s ago")]


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
        ("Config", check_config() + check_install_profile()),
        (
            "Runtime",
            check_runtime()
            + check_path_health()
            + check_hard_dependencies()
            + check_editable_installs(),
        ),
        ("Storage", check_storage() + check_databases() + check_vault()),
        ("Filesystem", check_cache_dir() + check_logs()),
        ("Network Sockets", check_sockets(port)),
        ("Formations", check_formations()),
        ("Skills", check_skills()),
        (
            "Gateway",
            check_gateway(port=port)
            + check_uplink(port=port)
            + check_event_processor(port=port)
            + check_gateway_auth()
            + check_policy_default(),
        ),
        ("AI Providers", check_ai_providers() + check_model_fallback() + check_decider()),
        ("Identity", check_identity()),
        ("Media Tools", check_media_tools()),
        ("Wiring", check_wiring()),
    ]

    freshness_results = (
        check_daemon_freshness()
        + check_daemon_log_location()
        + check_daemon_heartbeat()
        + check_daemon_parent()
    )
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

    payments_results = check_payments()
    if payments_results:  # one row, but the one that catches a silently dead revenue channel
        secs.append(("Payments", payments_results))

    ledger_results = check_ledger()
    if ledger_results:  # only when there are in-flight / interrupted operations
        secs.append(("Operations Ledger", ledger_results))

    repo_guard_results = check_repo_guard()
    if repo_guard_results:  # only inside a git repo
        secs.append(("Repo Guard", repo_guard_results))

    protocol_results = check_shared_protocol()
    if protocol_results:  # only inside a navig development checkout with its twin
        secs.append(("Shared Protocol", protocol_results))

    ci_runner_results = check_ci_runners()
    if ci_runner_results:  # only on a machine that hosts self-hosted CI runners
        secs.append(("CI Runners", ci_runner_results))

    secs.append(("Running Code", check_running_code()))

    desktop_results = check_desktop_apps()
    if desktop_results:  # Windows only; the Anchor rows
        secs.append(("Desktop Apps", desktop_results))

    plugin_source_results = check_plugin_sources() + check_other_interpreters()
    if plugin_source_results:  # only inside a navig development checkout
        secs.append(("Plugin Sources", plugin_source_results))

    runtime_plugin_results = check_runtime_inventory()
    if runtime_plugin_results:  # only inside a navig development checkout
        secs.append(("Runtime Plugins", runtime_plugin_results))

    secs.append(("Command Ownership", check_command_ownership()))

    release_results = check_release()
    if release_results:  # only inside a navig development checkout
        secs.append(("Release", release_results))

    browser_results = check_browsers() + check_browser_profiles()
    if browser_results:  # only when a debug browser is running unowned, or disk can be reclaimed
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
            entry = {
                "label": _plain_text(getattr(row, "label", "")),
                "ok": ok,
                "warn": warn,
                "detail": _plain_text(getattr(row, "detail", row[2])),
            }
            payload = getattr(row, "data", None)
            if payload is not None:
                entry["data"] = payload
            checks.append(entry)
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


def check_model_fallback() -> list[tuple[str, bool, str]]:
    """Is any primary model being stood in for by a fallback right now?

    Reads the fallback policy's degraded state (navig.llm.model_fallback). A
    degraded primary is a ⚠ — replies still work, but on a different model than
    the operator chose. An unreadable state is ⚠ too, never ✓ (the doctor rule:
    a green light over an unknown is worse than a red one).
    """
    try:
        from navig.llm import model_fallback

        deg = model_fallback.degraded()
        state_path = model_fallback._state_path()
        if state_path.exists():
            import json as _json

            raw = _json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("degraded"), dict):
                raise ValueError("malformed fallback state file")
    except Exception as exc:  # noqa: BLE001
        return [
            _check(
                "Model fallback",
                False,
                f"state unreadable ({type(exc).__name__}) — run `navig ai fallback`",
                warn=True,
            )
        ]
    if not deg:
        return [
            _check(
                "Model fallback",
                True,
                "no primary model is being stood in for (fallback state clean)",
            )
        ]
    parts = []
    for primary, entry in sorted(deg.items()):
        modes = ",".join(entry.get("modes") or []) or "?"
        parts.append(
            f"{primary} → {entry.get('fallback') or '?'} [{modes}] "
            f"({model_fallback.reason_text(entry.get('reason'))})"
        )
    return [
        _check(
            "Model fallback",
            False,
            f"{len(deg)} primary model(s) degraded: "
            + "; ".join(parts)
            + " — details: navig ai fallback",
            warn=True,
        )
    ]


def check_decider() -> list[tuple[str, bool, str]]:
    """The decision model (Cloudflare Clef, ``navig decider``) — optional, off by default.

    Never makes a call: a ✓ is only shown when the LAST real call succeeded, because the
    one thing this row must not do is report a working model it never saw answer.
    """
    label = "Decision model (Clef)"
    try:
        from navig.agent import decide

        st = decide.status()
    except Exception as exc:  # noqa: BLE001
        return [_check(label, False, f"could not read its status: {exc}", warn=True)]
    if not st["enabled"]:
        return [
            _check(
                label,
                True,
                'off (optional) — try it: navig decider test "…", then navig decider on',
            )
        ]
    on_uses = [u["id"] for u in st["uses"] if u["on"]]
    shadow = " · shadow mode (logged, not applied)" if st["shadow"] else ""
    if not st["credential"]:
        return [
            _check(
                label,
                False,
                "on, but no Cloudflare credential — every decision uses the previous method. "
                "Connect: navig lighthouse login",
                warn=True,
            )
        ]
    if not on_uses:
        return [
            _check(label, True, "on, but every use is switched off — navig decider on --use <id>")
        ]
    last = st["last_call"]
    if last is None:
        return [
            _check(
                label,
                False,
                f'on ({", ".join(on_uses)}){shadow}, but no call has succeeded yet — verify: navig decider test "…"',
                warn=True,
            )
        ]
    if not last["ok"]:
        hint = (
            decide.grant_hint()
            if last["kind"] == "auth"
            else "decisions fell back to the previous method"
        )
        return [
            _check(
                label,
                False,
                f"last call FAILED ({last['kind']}) at {last['at']} — {hint}",
                warn=True,
            )
        ]
    return [
        _check(
            label,
            True,
            f"on ({', '.join(on_uses)}) · {last['model']} · last call {last['at']} "
            f"{last['latency_ms']} ms · {st['calls_today']} calls today{shadow}",
        )
    ]


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
            results.append(
                _check(
                    "Identity source",
                    False,
                    "nothing resolved — the agent will fall back to a built-in identity",
                    warn=True,
                )
            )
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
        results.append(
            _check(
                "Plugin packages",
                failed == 0,
                tracker.summary_line(),
                warn=report.get("degraded", 0) > 0,
            )
        )
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
        results.append(
            _check(
                "Module registry",
                not stale,
                f"{len(modules)} modules · {locked} locked"
                + (f" · stale overrides: {', '.join(stale)}" if stale else ""),
                warn=bool(stale),
            )
        )
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
            detail += (
                f" · not in shipped map ({', '.join(drift)}) — 3rd-party is fine; "
                "if first-party, run scripts/gen_command_providers.py"
            )
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
            results.append(
                _check(
                    "Disabled commands",
                    not stale_cmds,
                    f"{len(mapping)} entries"
                    + (
                        f" · stale (plugin uninstalled): {', '.join(stale_cmds)}"
                        if stale_cmds
                        else ""
                    ),
                    warn=bool(stale_cmds),
                )
            )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Disabled commands", False, str(exc)[:120], warn=True))

    # Store one-liner.
    try:
        from navig.hub import store_status

        summary = store_status()
        by_state = summary["by_state"]
        results.append(
            _check(
                "Store",
                not summary["broken"],
                f"{by_state.get('wired', 0)} wired · {by_state.get('unwired', 0)} unwired · "
                f"{len(summary['broken'])} broken",
                warn=bool(summary["degraded"]),
            )
        )
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
                plan.append(
                    {
                        "src": str(child),
                        "action": "skill-bundle",
                        "dest": str(plugins_dir / child.name),
                    }
                )
            elif (child / "navig.package.json").exists():
                plan.append(
                    {
                        "src": str(child),
                        "action": "handler-pack",
                        "dest": str(plugins_dir / child.name),
                    }
                )
            else:
                plan.append({"src": str(child), "action": "skip", "reason": "unrecognized layout"})

    if json_output:
        print(_json.dumps({"dry_run": not apply, "plan": plan}, indent=2))
    else:
        if not plan:
            print("  ✓ Nothing to migrate — ~/.navig/packs is clean.")
            return
        for step in plan:
            print(
                f"  {step['action']:>13}  {step['src']}"
                + (
                    f" → {step.get('dest', '')}"
                    if step.get("dest")
                    else f"  ({step.get('reason', '')})"
                )
            )
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
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
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
