"""Move the split ``knowledge.db`` copies onto the ONE canonical path — dry-run by default.

``paths.knowledge_db_path()`` is the authority (``<data_dir>/knowledge.db``). Before it,
the same logical store lived at up to three paths:

* ``<config_dir>/knowledge.db``          — gateway + deck read/write (``storage_dir``)
* ``<config_dir>/memory/knowledge.db``   — ``navig memory knowledge`` + the agent context reader
* ``<data_dir>/knowledge.db``            — ``KnowledgeBase``'s documented default, never written

Two of them were observed live on 2026-10-22, both opened by the running daemon, so a row
written through one surface was invisible through another.

This module is the migration path, and its rules are deliberately paranoid:

* **Dry run by default** — :func:`migrate_knowledge_dbs` writes nothing unless ``apply=True``.
* **Never merges.** If more than one legacy copy holds rows it returns ``conflict`` and stops:
  deciding which key wins is the operator's call, not a silent overwrite.
* **Backup first, always** — before any write, every existing copy (legacy AND canonical) is
  copied into ``<config_dir>/.backup/knowledge-db-<stamp>/`` with SQLite's online backup API.
  A failed backup aborts the migration; nothing is moved on a partial backup.
* **A copy we cannot read stops the migration** — no data is judged on a failed read.
* **Nothing is deleted** — the moved source is renamed ``<name>.migrated`` (plus ``-wal``/
  ``-shm``), so a rollback is a rename away.

A "row" here means a row in the ``knowledge`` table ``KnowledgeBase`` owns. A readable DB
without that table is treated as EMPTY (a fresh/never-used file), which is how an empty
legacy copy is distinguished from a populated one.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from navig.platform import paths

logger = logging.getLogger(__name__)

#: Legacy locations, relative to ``config_dir()``, in historical precedence order.
LEGACY_RELATIVE: tuple[tuple[str, ...], ...] = (
    ("knowledge.db",),
    ("memory", "knowledge.db"),
)

_TABLE = "knowledge"


@dataclass(frozen=True)
class KnowledgeDbCopy:
    """One candidate file: where it is, how big, how old, and how many rows it holds."""

    path: str
    exists: bool
    size: int = 0
    mtime: float = 0.0
    rows: int = 0
    readable: bool = True
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def legacy_knowledge_db_paths(config_dir: Path | None = None) -> list[Path]:
    """The legacy copies that exist right now (root first, then ``memory/``)."""
    base = Path(config_dir) if config_dir is not None else paths.config_dir()
    found: list[Path] = []
    for relative in LEGACY_RELATIVE:
        candidate = base.joinpath(*relative)
        if candidate.is_file():
            found.append(candidate)
    return found


def _inspect(path: Path) -> KnowledgeDbCopy:
    """Read-only inspection. Unreadable is reported, never guessed at."""
    try:
        stat = path.stat()
    except OSError as exc:
        return KnowledgeDbCopy(path=str(path), exists=False, readable=False, error=str(exc))

    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return KnowledgeDbCopy(
            path=str(path), exists=True, size=stat.st_size, mtime=stat.st_mtime,
            readable=False, error=str(exc),
        )
    try:
        try:
            rows = int(connection.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                rows = 0  # a readable file with no knowledge table = never used
            else:
                raise
        return KnowledgeDbCopy(
            path=str(path), exists=True, size=stat.st_size, mtime=stat.st_mtime,
            rows=rows, readable=True,
        )
    except sqlite3.Error as exc:
        return KnowledgeDbCopy(
            path=str(path), exists=True, size=stat.st_size, mtime=stat.st_mtime,
            readable=False, error=str(exc),
        )
    finally:
        connection.close()


#: One incident AND one warning per process — the boot path resolves the knowledge DB
#: repeatedly (every memory-context build calls the resolver), so recording per call would
#: append a line to ``perf/config_incidents.jsonl`` on every read until migration runs.
_legacy_read_warned = False


def _note_legacy_read(copy: KnowledgeDbCopy) -> None:
    """Record the incident (doctor Config Health) and warn ONCE per process.

    The sentinel is checked BEFORE the incident write, not just before the warning: measured
    on the verifier's box, five resolver calls produced five JSONL lines — the exact
    unbounded-growth class this branch exists to remove. One process sees one state, so one
    line is the honest record; the sweep that fixes it clears the state entirely.
    """
    global _legacy_read_warned

    if _legacy_read_warned:
        return
    _legacy_read_warned = True

    try:
        from navig.core import incidents  # noqa: PLC0415

        incidents.record(
            incidents.KNOWLEDGE_DB_LEGACY_READ,
            path=copy.path,
            canonical=str(paths.knowledge_db_path()),
            rows=copy.rows,
        )
    except Exception as exc:  # noqa: BLE001 — an observation must never break a read
        logger.debug("knowledge_db: could not record the legacy-read incident: %s", exc)

    message = (
        f"knowledge.db: reading rows from a LEGACY copy ({copy.path}) — the canonical "
        f"{paths.knowledge_db_path()} is missing or empty. Run "
        "`navig db local migrate --dry-run`, then `navig db local migrate` to consolidate."
    )
    logger.warning(message)
    try:
        from navig import console_helper as ch  # noqa: PLC0415

        ch.warning(message)
    except Exception:  # noqa: BLE001 — a warning must never break a read
        pass


def resolve_readable_knowledge_db_path() -> Path:
    """The DB readers should OPEN right now — canonical, or a populated LEGACY copy.

    The single-authority switch is safe for writes but NOT for visibility: an existing
    install keeps its rows in a legacy copy (``<config_dir>/knowledge.db`` or
    ``<config_dir>/memory/knowledge.db``) and would show an EMPTY Knowledge view with no
    explanation. So, mirroring the config/genesis resolver pattern, when the canonical file
    is ABSENT (or empty) and a populated legacy copy EXISTS, reads are served from that copy
    — plus an incident (``doctor`` → Config Health) and one warning telling the operator to
    run ``navig db local migrate``.

    Once the canonical DB exists with rows it always wins, and every write made through the
    returned path lands where the migration will consolidate. Readers only need to swap
    ``paths.knowledge_db_path()`` for this function; writers keep their single authority.
    """
    canonical = paths.knowledge_db_path()
    if canonical.is_file():
        current = _inspect(canonical)
        if current.readable and current.rows > 0:
            return canonical

    populated: list[KnowledgeDbCopy] = []
    for legacy in legacy_knowledge_db_paths():
        copy = _inspect(legacy)
        if copy.readable and copy.rows > 0:
            populated.append(copy)

    if not populated:
        return canonical

    chosen = max(populated, key=lambda copy: copy.mtime)
    _note_legacy_read(chosen)
    return Path(chosen.path)


def _online_copy(source: Path, destination: Path) -> None:
    """Copy a live SQLite DB safely (works while another process holds a connection)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _install(source: Path, destination: Path) -> None:
    """Land a readable copy at *destination* atomically (temp in the same directory)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.migrating")
    try:
        _online_copy(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _retire(source: Path) -> tuple[list[str], list[str]]:
    """Rename a migrated source (and its WAL sidecars) aside — never delete.

    Returns ``(moved, errors)``. An error means a renamed file could not be moved aside,
    which the caller must report as a NON-success: with the legacy DB still live, writes to
    it never reach the canonical database.
    """
    moved: list[str] = []
    errors: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{source}{suffix}")
        if not path.exists():
            continue
        target = path.with_name(f"{path.name}.migrated")
        try:
            path.rename(target)
            moved.append(str(target))
        except OSError as exc:
            logger.warning("knowledge_db migration: cannot retire %s: %s", path, exc)
            errors.append(f"{path}: {exc}")
    return moved, errors


def _record_retire_failure(source: str, canonical: Path, error: str) -> None:
    """Tell the operator (doctor Config Health) that the legacy source is still live."""
    try:
        from navig.core import incidents  # noqa: PLC0415

        incidents.record(
            incidents.KNOWLEDGE_DB_RETIRE_FAILED,
            source=source,
            canonical=str(canonical),
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 — an observation must never break the migration
        logger.debug("knowledge_db migration: could not record a retire incident: %s", exc)


def _backup_name(path: Path, config_dir: Path) -> str:
    try:
        relative = path.relative_to(config_dir)
    except ValueError:
        relative = Path(path.name)
    return str(relative).replace("\\", "-").replace("/", "-") + ".backup"


def _backup_copies(
    copies: list[KnowledgeDbCopy], backup_dir: Path, config_root: Path
) -> list[str]:
    """Back up EVERY existing copy into *backup_dir* (online backup; raises on failure)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up: list[str] = []
    for copy in copies:
        source = Path(copy.path)
        if not source.exists():
            continue
        destination = backup_dir / _backup_name(source, config_root)
        _online_copy(source, destination)
        backed_up.append(str(destination))
    return backed_up


def _retire_failure_report(
    report: dict[str, Any], source: str, canonical: Path, errors: list[str]
) -> dict[str, Any]:
    """Mark the installed-but-not-retired outcome (B2): loud, distinct, actionable."""
    report["status"] = "applied-partial"
    report["notes"].append(
        "installed at the canonical path, but the LEGACY source could not be retired "
        "(most likely a running service holds it) — writes to the old file do NOT reach "
        "the canonical database until it is moved. Stop NAVIG (`navig service stop`) and "
        "re-run `navig db local migrate`; the re-run now finishes the retirement without "
        "touching the canonical data."
    )
    _record_retire_failure(source, canonical, errors[0])
    logger.warning(
        "knowledge_db migration: installed %s → %s, but could not retire %s: %s",
        source, canonical, source, errors[0],
    )
    return report


def migrate_knowledge_dbs(*, apply: bool = False) -> dict[str, Any]:
    """Detect the split knowledge DBs and plan/apply the merge onto the canonical one.

    Args:
        apply: ``False`` (default) = DRY RUN: report the plan, touch nothing.

    Returns:
        A report dict (``status``, ``canonical``, ``copies``, ``candidate``, ``backup_dir``,
        ``backups``, ``actions``, ``notes``, and on apply ``retired``/``errors``). ``status``
        is one of ``nothing-to-do``, ``already-canonical``, ``dry-run``, ``retire-pending``,
        ``conflict``, ``blocked``, ``applied``, ``retired-now`` or ``applied-partial``.
        ``applied-partial`` means the canonical DB was installed but the legacy source could
        not be retired (a running service holds it); a RE-RUN converges — with exactly one
        populated leftover it keeps the canonical data and finishes the retirement
        (``retired-now``), no flag needed. Two populated leftovers under a populated
        canonical are still report-only: picking one is the operator's call, never a guess.
    """
    config_root = paths.config_dir()
    canonical = paths.knowledge_db_path()
    report: dict[str, Any] = {
        "status": "nothing-to-do",
        "canonical": str(canonical),
        "copies": [],
        "candidate": None,
        "backup_dir": None,
        "backups": [],
        "actions": [],
        "notes": [],
    }

    legacy = legacy_knowledge_db_paths(config_root)
    copies = [_inspect(path) for path in legacy]
    if canonical.exists():
        copies.append(_inspect(canonical))
    report["copies"] = [copy.as_dict() for copy in copies]

    unreadable = [copy for copy in copies if not copy.readable]
    if unreadable:
        report["status"] = "blocked"
        report["notes"].append(
            "a copy cannot be read — refusing to migrate on a failed read: "
            + ", ".join(copy.path for copy in unreadable)
        )
        return report

    canonical_copy = next((c for c in copies if Path(c.path) == canonical), None)
    legacy_copies = [c for c in copies if Path(c.path) != canonical]
    populated_legacy = [c for c in legacy_copies if c.rows > 0]

    if canonical_copy is not None and canonical_copy.rows > 0:
        if len(populated_legacy) > 1:
            report["status"] = "already-canonical"
            report["notes"].append(
                "canonical DB already has rows and MORE THAN ONE legacy copy still holds "
                "rows — refusing to pick one (no merge): "
                + ", ".join(f"{c.path} ({c.rows} rows)" for c in populated_legacy)
            )
            return report
        if not populated_legacy:
            report["status"] = "already-canonical"
            return report

        # Exactly one populated legacy copy is left behind — the normal shape after a
        # partial retire (the daemon held the file). A RE-RUN MUST CONVERGE: keep the
        # canonical data (never reinstall over it), back everything up, retire the leftover.
        leftover = populated_legacy[0]
        report["candidate"] = leftover.path
        backup_dir = config_root / ".backup" / f"knowledge-db-{time.strftime('%Y%m%d-%H%M%S')}"
        report["backup_dir"] = str(backup_dir)
        report["actions"] = [
            f"keep the canonical DB ({canonical_copy.rows} rows) — do not install",
            f"back up every existing copy into {backup_dir}",
            f"retire the leftover legacy source {leftover.path} as {leftover.path}.migrated",
        ]

        if not apply:
            report["status"] = "retire-pending"
            return report

        try:
            report["backups"] = _backup_copies(copies, backup_dir, config_root)
        except (OSError, sqlite3.Error) as exc:
            report["status"] = "blocked"
            report["notes"].append(f"backup failed — nothing retired: {exc}")
            return report

        retired, retire_errors = _retire(Path(leftover.path))
        report["retired"] = retired
        report["errors"] = retire_errors
        if retire_errors:
            return _retire_failure_report(report, leftover.path, canonical, retire_errors)

        report["status"] = "retired-now"
        report["notes"].append(
            "canonical DB already held the rows; the leftover LEGACY source was retired "
            "(backed up first, never deleted)"
        )
        logger.info(
            "knowledge_db migration: canonical %s kept; retired leftover legacy source %s",
            canonical, leftover.path,
        )
        return report

    if len(populated_legacy) > 1:
        report["status"] = "conflict"
        report["notes"].append(
            "more than one legacy copy holds rows — refusing to merge (operator decision): "
            + ", ".join(f"{c.path} ({c.rows} rows)" for c in populated_legacy)
        )
        return report

    if populated_legacy:
        candidate = populated_legacy[0]
    elif legacy_copies:
        candidate = max(legacy_copies, key=lambda copy: copy.mtime)
    else:
        if canonical_copy is not None:
            report["status"] = "already-canonical"
            report["notes"].append("canonical path exists and is empty; nothing to move")
        return report

    report["candidate"] = candidate.path
    backup_dir = config_root / ".backup" / f"knowledge-db-{time.strftime('%Y%m%d-%H%M%S')}"
    report["backup_dir"] = str(backup_dir)
    report["actions"] = [
        f"back up {len(copies)} existing copy/copies into {backup_dir}",
        f"install {candidate.path} as {canonical}",
        f"retire {candidate.path} as {candidate.path}.migrated (never deleted)",
    ]

    if not apply:
        report["status"] = "dry-run"
        return report

    # ── Back up EVERY existing copy before the first write ───────────────────
    try:
        report["backups"] = _backup_copies(copies, backup_dir, config_root)
    except (OSError, sqlite3.Error) as exc:
        report["status"] = "blocked"
        report["notes"].append(f"backup failed — nothing moved: {exc}")
        return report

    # ── Install the single candidate, retire the source ──────────────────────
    try:
        _install(Path(candidate.path), canonical)
    except (OSError, sqlite3.Error) as exc:
        report["status"] = "blocked"
        report["notes"].append(f"install failed; canonical untouched: {exc}")
        return report

    retired, retire_errors = _retire(Path(candidate.path))
    report["retired"] = retired
    report["errors"] = retire_errors
    if retire_errors:
        return _retire_failure_report(report, candidate.path, canonical, retire_errors)

    report["status"] = "applied"
    logger.info(
        "knowledge_db migration: installed %s → %s (backups in %s)",
        candidate.path, canonical, backup_dir,
    )
    return report
