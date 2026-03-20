"""Desktop automation module using pyautogui and watchdog."""

from .controller import DesktopConfig, DesktopController
from .watcher import FileWatcher, WatchConfig

__all__ = ['DesktopController', 'DesktopConfig', 'FileWatcher', 'WatchConfig']
