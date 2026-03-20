"""
Memory Watcher - File system monitoring for automatic reindexing.

Features:
- Watches ~/.navig/memory/ for file changes
- Debounced reindexing (1.5s default) to batch rapid changes
- Background thread for non-blocking operation
- Graceful shutdown support
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Set

if TYPE_CHECKING:
    from navig.memory.manager import MemoryManager


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger
        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:
        pass


class MemoryWatcher:
    """
    Watches memory directory for changes and triggers reindexing.
    
    Uses polling (cross-platform) with debouncing to efficiently
    detect and batch file changes.
    
    Usage:
        watcher = MemoryWatcher(manager)
        watcher.start()
        
        # Later...
        watcher.stop()
    """

    # File extensions to watch
    WATCHED_EXTENSIONS = {'.md', '.markdown', '.txt'}

    def __init__(
        self,
        manager: 'MemoryManager',
        debounce_seconds: float = 1.5,
        poll_interval: float = 1.0,
        on_indexed: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Initialize file watcher.
        
        Args:
            manager: MemoryManager instance
            debounce_seconds: Wait time before indexing after changes
            poll_interval: How often to check for changes
            on_indexed: Optional callback(files_indexed, chunks_created)
        """
        self.manager = manager
        self.debounce_seconds = debounce_seconds
        self.poll_interval = poll_interval
        self.on_indexed = on_indexed

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # State tracking
        self._file_mtimes: dict[str, float] = {}
        self._pending_changes: Set[str] = set()
        self._last_change_time: float = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start watching for file changes."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()

        # Build initial file state
        self._scan_files()

        # Start watcher thread
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="MemoryWatcher",
            daemon=True,
        )
        self._thread.start()

        _debug_log(f"MemoryWatcher started: {self.manager.memory_dir}")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop watching and wait for thread to finish."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

        _debug_log("MemoryWatcher stopped")

    def _watch_loop(self) -> None:
        """Main watching loop."""
        while not self._stop_event.is_set():
            try:
                # Check for file changes
                self._check_changes()

                # Process pending changes if debounce time passed
                self._process_pending()

            except Exception as e:
                _debug_log(f"Watcher error: {e}")

            # Wait for next poll
            self._stop_event.wait(self.poll_interval)

    def _scan_files(self) -> dict[str, float]:
        """Scan directory and return file modification times."""
        mtimes = {}

        try:
            for path in self.manager.memory_dir.rglob('*'):
                if path.is_file() and path.suffix.lower() in self.WATCHED_EXTENSIONS:
                    rel_path = str(path.relative_to(self.manager.memory_dir))
                    try:
                        mtimes[rel_path] = path.stat().st_mtime
                    except OSError:
                        pass
        except Exception as e:
            _debug_log(f"Error scanning files: {e}")

        return mtimes

    def _check_changes(self) -> None:
        """Check for new/modified/deleted files."""
        current_mtimes = self._scan_files()

        with self._lock:
            # Check for new or modified files
            for path, mtime in current_mtimes.items():
                if path not in self._file_mtimes:
                    # New file
                    self._pending_changes.add(path)
                    self._last_change_time = time.time()
                    _debug_log(f"New file detected: {path}")
                elif mtime != self._file_mtimes[path]:
                    # Modified file
                    self._pending_changes.add(path)
                    self._last_change_time = time.time()
                    _debug_log(f"File modified: {path}")

            # Check for deleted files
            deleted = set(self._file_mtimes.keys()) - set(current_mtimes.keys())
            for path in deleted:
                self._pending_changes.add(f"deleted:{path}")
                self._last_change_time = time.time()
                _debug_log(f"File deleted: {path}")

            # Update state
            self._file_mtimes = current_mtimes

    def _process_pending(self) -> None:
        """Process pending changes if debounce time has passed."""
        if not self._pending_changes:
            return

        time_since_change = time.time() - self._last_change_time
        if time_since_change < self.debounce_seconds:
            return

        with self._lock:
            changes = self._pending_changes.copy()
            self._pending_changes.clear()

        if not changes:
            return

        # Separate deletions from updates
        deleted = {p.replace('deleted:', '') for p in changes if p.startswith('deleted:')}
        updated = {p for p in changes if not p.startswith('deleted:')}

        files_indexed = 0
        chunks_created = 0

        try:
            # Handle deletions
            for path in deleted:
                self.manager.storage.delete_file(path)
                _debug_log(f"Removed from index: {path}")

            # Handle new/modified files
            for rel_path in updated:
                full_path = self.manager.memory_dir / rel_path
                if full_path.exists():
                    result = self.manager.index_file(full_path)
                    files_indexed += result.files_processed
                    chunks_created += result.chunks_created

            if files_indexed > 0 or deleted:
                _debug_log(
                    f"Reindexed: {files_indexed} files, "
                    f"{chunks_created} chunks, {len(deleted)} deleted"
                )

            # Callback
            if self.on_indexed and (files_indexed > 0 or deleted):
                self.on_indexed(files_indexed, chunks_created)

        except Exception as e:
            _debug_log(f"Error processing changes: {e}")

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running

    def get_status(self) -> dict:
        """Get watcher status."""
        with self._lock:
            return {
                'running': self._running,
                'watched_files': len(self._file_mtimes),
                'pending_changes': len(self._pending_changes),
                'debounce_seconds': self.debounce_seconds,
                'poll_interval': self.poll_interval,
            }


class WatcherContext:
    """
    Context manager for temporary watching.
    
    Usage:
        with WatcherContext(manager) as watcher:
            # Do stuff, files will be watched
            pass
        # Watcher automatically stopped
    """

    def __init__(
        self,
        manager: 'MemoryManager',
        **kwargs,
    ):
        self.watcher = MemoryWatcher(manager, **kwargs)

    def __enter__(self) -> MemoryWatcher:
        self.watcher.start()
        return self.watcher

    def __exit__(self, *args) -> None:
        self.watcher.stop()


# Optional: Watchdog-based watcher for better performance
# (requires: pip install watchdog)

def _create_watchdog_watcher(
    manager: 'MemoryManager',
    debounce_seconds: float = 1.5,
    on_indexed: Optional[Callable[[int, int], None]] = None,
) -> Optional['WatchdogWatcher']:
    """
    Create a watchdog-based watcher if available.
    
    Returns None if watchdog is not installed.
    """
    try:
        from watchdog.events import FileModifiedEvent, FileSystemEventHandler
        from watchdog.observers import Observer

        return WatchdogWatcher(manager, debounce_seconds, on_indexed)
    except ImportError:
        return None


class WatchdogWatcher:
    """
    File watcher using watchdog for better performance.
    
    Only available if watchdog is installed.
    """

    def __init__(
        self,
        manager: 'MemoryManager',
        debounce_seconds: float = 1.5,
        on_indexed: Optional[Callable[[int, int], None]] = None,
    ):
        from watchdog.observers import Observer

        self.manager = manager
        self.debounce_seconds = debounce_seconds
        self.on_indexed = on_indexed

        self._observer = Observer()
        self._handler = _WatchdogHandler(
            manager=manager,
            debounce_seconds=debounce_seconds,
            on_indexed=on_indexed,
        )

    def start(self) -> None:
        """Start watching."""
        self._observer.schedule(
            self._handler,
            str(self.manager.memory_dir),
            recursive=True,
        )
        self._observer.start()
        _debug_log(f"WatchdogWatcher started: {self.manager.memory_dir}")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop watching."""
        self._observer.stop()
        self._observer.join(timeout=timeout)
        _debug_log("WatchdogWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._observer.is_alive()


class _WatchdogHandler:
    """Internal event handler for watchdog."""

    WATCHED_EXTENSIONS = {'.md', '.markdown', '.txt'}

    def __init__(
        self,
        manager: 'MemoryManager',
        debounce_seconds: float,
        on_indexed: Optional[Callable[[int, int], None]],
    ):

        self.manager = manager
        self.debounce_seconds = debounce_seconds
        self.on_indexed = on_indexed

        self._pending: Set[str] = set()
        self._last_change: float = 0
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def dispatch(self, event) -> None:
        """Handle any file system event."""
        # Skip directories
        if event.is_directory:
            return

        # Check extension
        path = Path(event.src_path)
        if path.suffix.lower() not in self.WATCHED_EXTENSIONS:
            return

        with self._lock:
            rel_path = str(path.relative_to(self.manager.memory_dir))
            self._pending.add(rel_path)
            self._last_change = time.time()

            # Cancel existing timer
            if self._timer:
                self._timer.cancel()

            # Schedule new timer
            self._timer = threading.Timer(
                self.debounce_seconds,
                self._process_pending,
            )
            self._timer.start()

    def _process_pending(self) -> None:
        """Process pending changes."""
        with self._lock:
            changes = self._pending.copy()
            self._pending.clear()
            self._timer = None

        if not changes:
            return

        try:
            result = self.manager.index(force=False)

            if result.files_processed > 0:
                _debug_log(
                    f"Watchdog reindex: {result.files_processed} files, "
                    f"{result.chunks_created} chunks"
                )

            if self.on_indexed and result.files_processed > 0:
                self.on_indexed(result.files_processed, result.chunks_created)

        except Exception as e:
            _debug_log(f"Watchdog processing error: {e}")
