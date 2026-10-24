"""Local retention sweep — the production caller both cleanup helpers were missing.

Two TTL-bounded cleanups shipped with **no production caller**:

* :func:`navig.agent.session_store.cleanup_old_sessions` — ``sessions/`` reached 5,395
  files on a live install (oldest June) because nothing ever ran the sweep;
* :func:`navig.agent.tool_caps.cleanup_spillover` — ``tmp/tool_spillover`` kept its files
  past the documented 1-hour TTL.

:func:`run_retention` is the ONE startup sweep, called from
``navig.gateway.server.NavigGateway.start()``. Besides those two helpers it prunes
``SessionManager``'s ``<key>.json`` session files, which the session-store helper does not
own (it only understands the ``<id>.meta.json`` + ``<id>.jsonl`` pair format — one
directory can hold both).

**Nothing is deleted.** Every pruned record is MOVED into
``<paths.trash_dir()>/retention-<yyyyMMddTHHMMSS>/`` (the owner's "delete → trash, never
unlink" rule), with a ``manifest.json`` recording where each file came from. A sweep can be
undone by moving files back; ``incidents.record(RETENTION_SWEPT, …)`` puts the counts in
``navig doctor`` → Config Health, and one INFO line names the trash directory.

Safety rules — each one is a test:

* a record is pruned only when its age signals are **all** old: the recorded
  ``last_active`` and the newest companion file mtime must both be older than the window;
* a session currently loaded in gateway memory (``keep_session_keys``) is never pruned;
* an unreadable / malformed / non-session record is skipped, never pruned;
* a failed move leaves the file exactly where it was (never a partial delete);
* ``retention.enabled: false`` does no filesystem work at all;
* under pytest the sweep is a no-op unless ``NAVIG_RETENTION_FORCE=1`` — the suite builds
  gateways with the real config dir, and a test run must never touch operator history.

Knobs (all optional; the defaults keep existing history):

===========================  ======  ===============================================
key                          default meaning
===========================  ======  ===============================================
``retention.enabled``        true    master switch for the boot sweep
``retention.sessions_days``  30      age window for session files (clamped to >= 1 day)
``retention.spillover_days`` 14      age window for tool-spillover files (>= 1 day)
===========================  ======  ===============================================
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DAYS = 30
DEFAULT_SPILLOVER_DAYS = 14

#: Set to ``1`` to run the sweep inside the test suite (the default is a no-op there).
FORCE_ENV = "NAVIG_RETENTION_FORCE"

#: Subdirectory prefix for one sweep's recoverable set, e.g. ``retention-20261022T134500``.
TRASH_PREFIX = "retention-"


@dataclass
class RetentionReport:
    """What one sweep did. ``removed`` is the total, ``skipped_reason`` why it was a no-op."""

    sessions_removed: int = 0
    spillover_removed: int = 0
    skipped_reason: str = ""
    trash_dir: str = ""
    #: Original path -> trash path for everything moved (also written to manifest.json).
    moved: dict[str, str] = field(default_factory=dict)
    failed: int = 0

    @property
    def removed(self) -> int:
        return self.sessions_removed + self.spillover_removed

    def summary(self) -> str:
        where = f" into {self.trash_dir}" if self.trash_dir else ""
        text = (
            f"moved {self.sessions_removed} session file(s) and "
            f"{self.spillover_removed} spillover file(s){where} (recoverable, nothing deleted)"
        )
        if self.failed:
            text += f"; {self.failed} file(s) could not be moved and were left in place"
        return text


def _setting(config: object, key: str, default: object) -> object:
    """Read *key* from *config* when it has a ``get`` (ConfigManager / SharedConfig)."""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception:  # noqa: BLE001 — a broken config read must not break the sweep
        return default
    return default if value is None else value


def _enabled(config: object) -> bool:
    from navig.core.coerce import coerce_bool  # noqa: PLC0415 — keep import cost off boot path

    return coerce_bool(_setting(config, "retention.enabled", True), default=True)


def _window_days(config: object, key: str, default: int) -> int:
    """Configured window in days, clamped to at least 1 (0 would prune everything)."""
    raw = _setting(config, key, default)
    try:
        days = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        days = default
    return max(days, 1)


def _under_pytest() -> bool:
    return "pytest" in sys.modules


def _forced() -> bool:
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(os.environ.get(FORCE_ENV), default=False)


def new_trash_root(now: float | None = None) -> Path:
    """``<config_dir>/.trash/retention-<stamp>`` — one dated set per sweep."""
    from navig.platform.paths import trash_dir  # noqa: PLC0415

    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(time.time() if now is None else now))
    return trash_dir() / f"{TRASH_PREFIX}{stamp}"


def _move_to_trash(path: Path, trash_root: Path | None) -> Path | None:
    """Move *path* into *trash_root* (delegates to the one recoverable-delete helper)."""
    if trash_root is None:
        return None
    from navig.core.trash import move_to_trash  # noqa: PLC0415

    return move_to_trash(path, trash_root)


def _sanitized_key(session_key: str) -> str:
    """Mirror of ``SessionManager._sanitize_key`` (that is how the file is named).

    Kept as a 3-line mirror instead of importing the gateway class: retention runs at boot
    and must not drag the whole gateway module in for one ``str.replace``.
    """
    return session_key.replace(":", "_").replace("/", "_").replace("\\", "_")


def _looks_like_session_record(path: Path) -> bool:
    """A single-file gateway session is a JSON object with ``key`` or ``messages``.

    Anything else in the directory (an unrelated ``*.json``) is left alone — the sweep only
    owns session records, and deleting a file it cannot identify is exactly the class of
    accident the trash rule exists to survive.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and ("key" in data or isinstance(data.get("messages"), list))


def sweep_gateway_sessions(
    directory: Path,
    max_age_days: int,
    keep_keys: Iterable[str] = (),
    *,
    trash_root: Path | None = None,
    report: RetentionReport | None = None,
) -> int:
    """Move stale ``SessionManager`` ``<key>.json`` sessions into the trash.

    The agent store's ``<id>.meta.json`` pair files are left to
    :func:`~navig.agent.session_store.cleanup_old_sessions`; only single-file sessions are
    considered here. A file is never touched when its mtime is inside the window, its key is
    in *keep_keys*, or it does not parse as a session record (see
    :func:`_looks_like_session_record`).
    """
    if not directory.is_dir():
        return 0

    cutoff = time.time() - max_age_days * 86_400
    protected: set[str] = set()
    for key in keep_keys:
        protected.add(key)
        protected.add(_sanitized_key(key))

    removed = 0
    for path in directory.glob("*.json"):
        if path.name.endswith(".meta.json"):
            continue  # agent-store sidecar, owned by cleanup_old_sessions
        if path.stem in protected:
            logger.debug("Retention: keeping active session %s", path.name)
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError as exc:
            logger.debug("Retention: cannot stat %s: %s", path.name, exc)
            continue
        if not _looks_like_session_record(path):
            logger.debug("Retention: skipping non-session json %s", path.name)
            continue
        trashed = _move_to_trash(path, trash_root)
        if trashed is None:
            if report is not None:
                report.failed += 1
            continue
        if report is not None:
            report.moved[str(path)] = str(trashed)
        removed += 1
        logger.debug("Retention: moved stale gateway session %s → %s", path.name, trashed)
    return removed


def run_retention(
    *,
    sessions_dir: Path | None = None,
    gateway_sessions_dir: Path | None = None,
    spillover_dir: Path | None = None,
    trash_root: Path | None = None,
    config: object = None,
    keep_session_keys: Iterable[str] = (),
    force: bool = False,
) -> RetentionReport:
    """Run the boot retention sweep. Never raises; reports what it moved.

    Args:
        sessions_dir: Agent-store base dir (``<id>.meta.json`` pairs). Defaults to
            ``<config_dir>/sessions``, resolved at call time.
        gateway_sessions_dir: Where ``SessionManager`` keeps ``<key>.json`` files. Defaults
            to *sessions_dir* (the same directory on a default install).
        spillover_dir: Tool-spillover dir. Defaults to :func:`tool_caps._spillover_dir`.
        trash_root: Where pruned records go. Defaults to
            ``<paths.trash_dir()>/retention-<stamp>``; one dated set per sweep.
        config: Config source (``ConfigManager``, ``SharedConfig`` or a fake with ``get``).
        keep_session_keys: Sessions that must survive (e.g. loaded in gateway memory).
        force: Run even under pytest (the tests set this with an isolated directory).
    """
    if not force and _under_pytest() and not _forced():
        return RetentionReport(skipped_reason="pytest")
    if not _enabled(config):
        return RetentionReport(skipped_reason="retention.enabled is false")

    from navig.agent.session_store import cleanup_old_sessions  # noqa: PLC0415
    from navig.agent.tool_caps import cleanup_spillover  # noqa: PLC0415
    from navig.platform.paths import config_dir  # noqa: PLC0415

    sessions_days = _window_days(config, "retention.sessions_days", DEFAULT_SESSIONS_DAYS)
    spillover_days = _window_days(
        config, "retention.spillover_days", DEFAULT_SPILLOVER_DAYS
    )

    base = Path(sessions_dir) if sessions_dir is not None else config_dir() / "sessions"
    gateway_base = Path(gateway_sessions_dir) if gateway_sessions_dir is not None else base
    trash = Path(trash_root) if trash_root is not None else new_trash_root()

    report = RetentionReport(trash_dir=str(trash))
    kept = tuple(keep_session_keys)
    failed_moves: list[str] = []

    report.sessions_removed += cleanup_old_sessions(
        max_age_days=sessions_days, base_dir=base, keep=kept,
        trash_root=trash, moved=report.moved, failed=failed_moves,
    )
    if gateway_base != base:
        report.sessions_removed += cleanup_old_sessions(
            max_age_days=sessions_days, base_dir=gateway_base, keep=kept,
            trash_root=trash, moved=report.moved, failed=failed_moves,
        )
    report.sessions_removed += sweep_gateway_sessions(
        gateway_base,
        max_age_days=sessions_days,
        keep_keys=kept,
        trash_root=trash,
        report=report,
    )

    report.spillover_removed = cleanup_spillover(
        max_age=spillover_days * 86_400,
        spillover_dir=spillover_dir,
        trash_root=trash,
        moved=report.moved,
        failed=failed_moves,
    )
    # Per-file move failures (a partially moved session pair, a locked spillover file) are
    # reported alongside the gateway sweep's own failures instead of vanishing from counts.
    report.failed += len(failed_moves)

    if report.removed:
        _write_manifest(report)
        _record_incident(report)
        logger.info("Local retention: %s", report.summary())
    return report


def _write_manifest(report: RetentionReport) -> None:
    """Record where each moved file came from, so a recovery is mechanical."""
    try:
        root = Path(report.trash_dir)
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "swept_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sessions": report.sessions_removed,
            "spillover": report.spillover_removed,
            "files": report.moved,
        }
        (root / "manifest.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Retention: moved files but could not write the trash manifest: %s", exc)


def _record_incident(report: RetentionReport) -> None:
    """Put the sweep in `navig doctor` → Config Health. Never breaks the sweep."""
    try:
        from navig.core import incidents  # noqa: PLC0415

        incidents.record(
            incidents.RETENTION_SWEPT,
            sessions=report.sessions_removed,
            spillover=report.spillover_removed,
            failed=report.failed,
            trash_dir=report.trash_dir,
        )
    except Exception as exc:  # noqa: BLE001 — an observation must never break the sweep
        logger.debug("Retention: could not record the sweep incident: %s", exc)
