"""Recoverable deletes: move a file into a dated trash set instead of unlinking it.

The rule on this machine is "delete → trash, never unlink". Retention sweeps and any other
automatic prune call :func:`move_to_trash` with a per-operation root under
``paths.trash_dir()`` (``<config_dir>/.trash/retention-<stamp>/``), so a mistake is a
directory move back rather than lost history.

A move is deliberately best-effort: ``None`` means "could not move, source untouched"
(a Windows sharing violation, permissions). Callers must never treat that as pruned.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def move_to_trash(path: Path, trash_root: Path) -> Path | None:
    """Move *path* into *trash_root*, creating it lazily.

    Returns the destination, or ``None`` when the move failed — in which case the source is
    still exactly where it was and the caller must not count it as pruned.
    """
    try:
        trash_root.mkdir(parents=True, exist_ok=True)
        destination = trash_root / path.name
        if destination.exists():  # same name from another directory in one sweep
            destination = trash_root / f"{path.stem}.{int(time.time() * 1000)}{path.suffix}"
        path.rename(destination)
        return destination
    except OSError as exc:
        logger.debug("trash: cannot move %s to %s: %s", path, trash_root, exc)
        return None
