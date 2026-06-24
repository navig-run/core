"""Shared dataclass types for automation adapters (AHK, Linux, macOS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """Result of an automation command execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0
    status: str = "COMPLETED"


@dataclass
class WindowInfo:
    """Information about an OS window."""

    title: str
    id: str  # HWND on Windows, XID on Linux
    pid: int
    class_name: str
    x: int
    y: int
    width: int
    height: int
    process_name: str | None = None
    is_minimized: bool = False
    is_maximized: bool = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "id": self.id,
            "pid": self.pid,
            "class_name": self.class_name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "process_name": self.process_name,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
            # Convenience state string for callers expecting ahk-style output
            "state": (
                "minimized"
                if self.is_minimized
                else ("maximized" if self.is_maximized else "normal")
            ),
        }


@dataclass
class Size:
    """Screen or window dimensions in pixels."""

    width: int
    height: int

    def to_string(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class DesktopStateInfo:
    """Metadata attached to a desktop snapshot result."""

    cursor_position: Optional[tuple[int, int]] = None
    screenshot_original_size: Optional[Size] = None
    screenshot_backend: Optional[str] = None
    # xyxy bounding box of the captured region, or None for full desktop.
    screenshot_region: Optional[tuple[int, int, int, int]] = None
    # Indices of monitors included in the screenshot.
    screenshot_displays: Optional[list[int]] = None
