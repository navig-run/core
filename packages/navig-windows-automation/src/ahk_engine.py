"""Compatibility wrapper for the shared NAVIG AutoHotkey adapter."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _ensure_repo_root_on_path() -> None:
    """Make the monorepo root importable for direct script execution."""
    repo_root = Path(__file__).resolve().parents[3]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_path()

from navig.adapters.automation.ahk import AHKAdapter as _CoreAHKAdapter, AHKStatus
from navig.adapters.automation.types import ExecutionResult, WindowInfo


def _result_to_dict(result: ExecutionResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "status": result.status,
    }


class AHKAdapter(_CoreAHKAdapter):
    """Package-local compatibility layer over the shared AHK adapter."""

    def run_script(self, script: str, timeout: float | None = None) -> dict[str, Any]:
        """Execute inline script content or an existing .ahk file path."""
        script_text = script.strip()
        script_path = Path(script_text)

        if "\n" not in script_text and "\r" not in script_text and script_path.is_file():
            result = self.execute_file(script_path, timeout=timeout)
        else:
            result = self.execute(script_text, timeout=timeout)

        return _result_to_dict(result)

    def send_input(
        self, text: str, window_title: str | None = None
    ) -> ExecutionResult:
        """Preserve the legacy package API expected by handler.py."""
        if window_title:
            activation = self.activate_window(window_title)
            if not activation.success:
                return activation
        return self.type_text(text)


__all__ = ["AHKAdapter", "AHKStatus", "ExecutionResult", "WindowInfo"]
