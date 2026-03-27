"""File system watcher for reactive automation."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Lazy import
_watchdog = None


def _init_watchdog():
    """Lazy import of watchdog."""
    global _watchdog
    if _watchdog is None:
        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer

            _watchdog = {
                "Observer": Observer,
                "FileSystemEventHandler": FileSystemEventHandler,
                "FileSystemEvent": FileSystemEvent,
            }
        except ImportError as _exc:
            raise ImportError(
                "watchdog not installed. Install with: pip install watchdog"
            ) from _exc
    return _watchdog


@dataclass
class WatchConfig:
    """File watch configuration."""

    path: str
    patterns: list[str] = field(default_factory=list)  # e.g., ["*.py", "*.json"]
    ignore_patterns: list[str] = field(default_factory=list)
    recursive: bool = True
    events: list[str] = field(
        default_factory=lambda: ["created", "modified", "deleted", "moved"]
    )
    debounce_seconds: float = 0.5  # Debounce rapid changes


@dataclass
class FileEvent:
    """File system event."""

    event_type: str  # created, modified, deleted, moved
    src_path: str
    dest_path: str | None = None  # For moved events
    is_directory: bool = False
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "src_path": self.src_path,
            "dest_path": self.dest_path,
            "is_directory": self.is_directory,
            "timestamp": self.timestamp.isoformat(),
        }


class _NavigFileHandler:
    """Internal file system event handler."""

    def __init__(
        self,
        callback: Callable,
        patterns: list[str] = None,
        ignore_patterns: list[str] = None,
        events: list[str] = None,
        debounce_seconds: float = 0.5,
    ):
        self.callback = callback
        self.patterns = patterns or []
        self.ignore_patterns = ignore_patterns or []
        self.events = events or ["created", "modified", "deleted", "moved"]
        self.debounce_seconds = debounce_seconds

        self._loop = None
        self._last_events: dict[str, float] = {}  # path -> timestamp for debouncing

    def set_loop(self, loop):
        """Set the event loop for async callbacks."""
        self._loop = loop

    def _should_handle(self, event_type: str, src_path: str) -> bool:
        """Check if event should be handled."""
        import time
        from fnmatch import fnmatch

        # Check event type
        if event_type not in self.events:
            return False

        filename = Path(src_path).name

        # Check ignore patterns
        for pattern in self.ignore_patterns:
            if fnmatch(filename, pattern):
                return False

        # Check patterns (if specified)
        if self.patterns:
            matched = False
            for pattern in self.patterns:
                if fnmatch(filename, pattern):
                    matched = True
                    break
            if not matched:
                return False

        # Debounce
        now = time.time()
        key = f"{event_type}:{src_path}"
        last_time = self._last_events.get(key, 0)
        if now - last_time < self.debounce_seconds:
            return False
        self._last_events[key] = now

        return True

    def on_created(self, event):
        self._handle_event("created", event)

    def on_modified(self, event):
        self._handle_event("modified", event)

    def on_deleted(self, event):
        self._handle_event("deleted", event)

    def on_moved(self, event):
        self._handle_event("moved", event)

    def _handle_event(self, event_type: str, raw_event):
        """Handle a file system event."""
        if raw_event.is_directory:
            return

        if not self._should_handle(event_type, raw_event.src_path):
            return

        # Create event object
        file_event = FileEvent(
            event_type=event_type,
            src_path=raw_event.src_path,
            dest_path=getattr(raw_event, "dest_path", None),
            is_directory=raw_event.is_directory,
        )

        # Call callback in event loop
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._call_callback(file_event), self._loop
            )
        else:
            # Synchronous fallback
            try:
                if asyncio.iscoroutinefunction(self.callback):
                    asyncio.run(self.callback(file_event))
                else:
                    self.callback(file_event)
            except Exception as e:
                logger.error(f"File watcher callback error: {e}")

    async def _call_callback(self, event: FileEvent):
        """Call callback asynchronously."""
        try:
            if asyncio.iscoroutinefunction(self.callback):
                await self.callback(event)
            else:
                self.callback(event)
        except Exception as e:
            logger.error(f"File watcher callback error: {e}")


class FileWatcher:
    """
    File system watcher for reactive automation.

    Monitors directories for file changes and triggers callbacks.

    Example:
        watcher = FileWatcher()

        async def on_change(event):
            print(f"{event.event_type}: {event.src_path}")

        await watcher.watch(
            "/path/to/dir",
            on_change,
            patterns=["*.py"],
            events=["modified", "created"],
        )

        await watcher.start()
        # ... watcher is running ...
        await watcher.stop()
    """

    def __init__(self):
        self._observers: dict[str, Any] = {}  # path -> observer
        self._handlers: dict[str, _NavigFileHandler] = {}  # path -> handler
        self._started = False

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._started

    @property
    def watched_paths(self) -> list[str]:
        """Get list of watched paths."""
        return list(self._observers.keys())

    async def watch(
        self,
        path: str,
        callback: Callable[[FileEvent], Any],
        patterns: list[str] = None,
        ignore_patterns: list[str] = None,
        events: list[str] = None,
        recursive: bool = True,
        debounce_seconds: float = 0.5,
    ):
        """
        Add a path to watch.

        Args:
            path: Directory path to watch
            callback: Function called on file events (async or sync)
            patterns: File patterns to match (e.g., ["*.py"])
            ignore_patterns: Patterns to ignore
            events: Event types to watch ("created", "modified", "deleted", "moved")
            recursive: Watch subdirectories
            debounce_seconds: Minimum time between same events
        """
        watchdog = _init_watchdog()

        path = str(Path(path).expanduser().resolve())

        if not Path(path).exists():
            raise ValueError(f"Path does not exist: {path}")

        # Create handler
        handler = _NavigFileHandler(
            callback=callback,
            patterns=patterns,
            ignore_patterns=ignore_patterns,
            events=events,
            debounce_seconds=debounce_seconds,
        )

        # Get event loop for async callbacks
        try:
            loop = asyncio.get_event_loop()
            handler.set_loop(loop)
        except RuntimeError:
            pass  # no event loop yet; safe to ignore

        # Create watchdog handler wrapper
        class WatchdogHandler(watchdog["FileSystemEventHandler"]):
            def on_created(self, event):
                handler.on_created(event)

            def on_modified(self, event):
                handler.on_modified(event)

            def on_deleted(self, event):
                handler.on_deleted(event)

            def on_moved(self, event):
                handler.on_moved(event)

        # Create observer
        observer = watchdog["Observer"]()
        observer.schedule(WatchdogHandler(), path, recursive=recursive)

        self._observers[path] = observer
        self._handlers[path] = handler

        logger.info(f"Watching: {path} (patterns={patterns}, recursive={recursive})")

        # Auto-start if already running
        if self._started:
            observer.start()

    async def unwatch(self, path: str):
        """Stop watching a path."""
        path = str(Path(path).expanduser().resolve())

        observer = self._observers.pop(path, None)
        self._handlers.pop(path, None)

        if observer:
            observer.stop()
            observer.join(timeout=5)
            logger.info(f"Stopped watching: {path}")

    async def start(self):
        """Start all observers."""
        if self._started:
            return

        self._started = True

        for observer in self._observers.values():
            if not observer.is_alive():
                observer.start()

        logger.info(f"FileWatcher started with {len(self._observers)} paths")

    async def stop(self):
        """Stop all observers."""
        self._started = False

        for observer in self._observers.values():
            observer.stop()

        for observer in self._observers.values():
            observer.join(timeout=5)

        self._observers.clear()
        self._handlers.clear()

        logger.info("FileWatcher stopped")

    def get_status(self) -> dict[str, Any]:
        """Get watcher status."""
        return {
            "running": self._started,
            "watched_paths": [
                {
                    "path": path,
                    "alive": (
                        observer.is_alive() if hasattr(observer, "is_alive") else False
                    ),
                }
                for path, observer in self._observers.items()
            ],
        }
