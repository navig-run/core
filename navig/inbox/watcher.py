"""
navig.inbox.watcher — Cross-platform filesystem watcher for the inbox directory.

Uses ``watchfiles`` (>= 0.21) when available; falls back to a polling
loop with configurable interval when watchfiles is not installed.

The watcher monitors ``~/.navig/inbox/`` (global) and optionally the
current project's ``.navig/wiki/inbox/`` (project).

Each detected new or modified file is passed to the pipeline:
    1. HookSystem.fire("before_classify", …)
    2. Classifier.classify(…)
    3. HookSystem.fire("after_classify", …)
    4. InboxRouter.route(…)
    5. HookSystem.fire("after_route", …)
    6. InboxStore.insert_event / insert_decision
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, List, Optional, Set

logger = logging.getLogger("navig.inbox.watcher")

# ── Callbacks ─────────────────────────────────────────────────

FileCB = Callable[[Path], None]  # called for each detected file


# ── Watched paths helpers ─────────────────────────────────────

def _global_inbox_dir() -> Path:
    try:
        from navig.platform.paths import navig_data_dir
        return navig_data_dir() / "inbox"
    except Exception:
        return Path.home() / ".navig" / "inbox"


def _project_inbox_dir(project_root: Optional[Path] = None) -> Path:
    root = project_root or Path.cwd()
    return root / ".navig" / "wiki" / "inbox"


# ── WatchfilesBackend ─────────────────────────────────────────

class _WatchfilesBackend:
    """Watcher using watchfiles library (async-friendly)."""

    def __init__(self, dirs: List[Path], callback: FileCB) -> None:
        self._dirs = [d for d in dirs if d.is_dir()]
        self._callback = callback
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            from watchfiles import watch as _watch
        except ImportError as _exc:
            raise RuntimeError("watchfiles not installed") from _exc

        logger.info("Inbox watcher started (watchfiles) on: %s", self._dirs)
        for changes in _watch(*self._dirs, stop_event=self._stop_event):
            for change_type, path_str in changes:
                path = Path(path_str)
                if path.is_file() and not path.name.startswith("."):
                    try:
                        self._callback(path)
                    except Exception as exc:
                        logger.exception("Callback error for %s: %s", path, exc)

    def stop(self) -> None:
        self._stop_event.set()


# ── PollingBackend ────────────────────────────────────────────

class _PollingBackend:
    """Fallback polling watcher when watchfiles is unavailable."""

    def __init__(
        self, dirs: List[Path], callback: FileCB, interval: float = 3.0
    ) -> None:
        self._dirs = dirs
        self._callback = callback
        self._interval = interval
        self._seen: Set[str] = set()
        self._stop = threading.Event()

    def _scan(self) -> None:
        for d in self._dirs:
            if not d.is_dir():
                continue
            for path in d.iterdir():
                if path.is_file() and not path.name.startswith("."):
                    key = f"{path}:{path.stat().st_mtime}"
                    if key not in self._seen:
                        self._seen.add(key)
                        try:
                            self._callback(path)
                        except Exception as exc:
                            logger.exception("Callback error for %s: %s", path, exc)

    def run(self) -> None:
        logger.info(
            "Inbox watcher started (polling, %.1fs) on: %s", self._interval, self._dirs
        )
        # Initial scan — don't fire callbacks for pre-existing files
        for d in self._dirs:
            if not d.is_dir():
                continue
            for path in d.iterdir():
                if path.is_file():
                    key = f"{path}:{path.stat().st_mtime}"
                    self._seen.add(key)

        while not self._stop.wait(self._interval):
            self._scan()

    def stop(self) -> None:
        self._stop.set()


# ── InboxWatcher (public API) ─────────────────────────────────

class InboxWatcher:
    """
    Monitor inbox directories and invoke a callback for each new file.

    Parameters
    ----------
    callback:
        Called with the Path of each detected file.
    project_root:
        Optional project root; adds ``.navig/wiki/inbox/`` to watched dirs.
    extra_dirs:
        Additional directories to watch.
    poll_interval:
        Polling interval in seconds (fallback mode only).
    """

    def __init__(
        self,
        callback: FileCB,
        project_root: Optional[Path] = None,
        extra_dirs: Optional[List[Path]] = None,
        poll_interval: float = 3.0,
    ) -> None:
        self._callback = callback

        self._dirs: List[Path] = [_global_inbox_dir()]
        if project_root:
            self._dirs.append(_project_inbox_dir(project_root))
        if extra_dirs:
            self._dirs.extend(extra_dirs)

        # Ensure directories exist
        for d in self._dirs:
            d.mkdir(parents=True, exist_ok=True)

        self._poll_interval = poll_interval
        self._backend: Optional[_WatchfilesBackend | _PollingBackend] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, *, daemon: bool = True) -> None:
        """Start the watcher in a background thread."""
        try:
            import watchfiles  # noqa: F401
            self._backend = _WatchfilesBackend(self._dirs, self._callback)
        except ImportError:
            self._backend = _PollingBackend(
                self._dirs, self._callback, self._poll_interval
            )

        self._thread = threading.Thread(
            target=self._backend.run,
            name="navig-inbox-watcher",
            daemon=daemon,
        )
        self._thread.start()
        logger.info("InboxWatcher started; dirs=%s", self._dirs)

    def stop(self) -> None:
        """Signal the watcher to stop and wait for thread to exit."""
        if self._backend:
            self._backend.stop()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def watched_dirs(self) -> List[Path]:
        return list(self._dirs)

    def __enter__(self) -> "InboxWatcher":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
