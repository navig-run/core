"""
Proactive Assistant Utilities

Cross-platform directory management and helper functions for the AI assistant system.
"""

import json
import os
import sys
from pathlib import Path

from navig import console_helper as ch

_IS_WINDOWS: bool = sys.platform == "win32"


def ensure_navig_directory() -> Path:
    """
    Ensure NAVIG directory structure exists with proper permissions.

    Creates:
    - Main directory: ~/.navig/ or ~/Documents/.navig/
    - Subdirectories: ai_context/, baselines/
    - Initial JSON files with proper schemas

    Returns:
        Path to .navig directory
    """
    from navig.platform.paths import config_dir

    navig_dir = config_dir()

    # Create main directory
    try:
        navig_dir.mkdir(parents=True, exist_ok=True)

        # Set permissions on Unix-like systems
        if not _IS_WINDOWS:
            try:
                os.chmod(navig_dir, 0o755)
            except (OSError, PermissionError):
                pass  # best-effort: skip on access/IO error
        # Create subdirectories
        subdirs = ["ai_context", "baselines"]
        for subdir in subdirs:
            subdir_path = navig_dir / subdir
            subdir_path.mkdir(parents=True, exist_ok=True)

            if not _IS_WINDOWS:
                try:
                    os.chmod(subdir_path, 0o755)
                except (OSError, PermissionError):
                    pass  # best-effort: skip on access/IO error
        # Initialize JSON files if they don't exist
        _initialize_json_files(navig_dir)

        return navig_dir

    except Exception as e:
        ch.error(f"Failed to create NAVIG directory: {e}")
        raise


def _initialize_json_files(navig_dir: Path):
    """Initialize JSON storage files with proper schemas."""

    ai_context_dir = navig_dir / "ai_context"

    # Define initial schemas for each JSON file
    json_files = {
        "command_history.json": [],
        "error_log.json": [],
        "error_patterns.json": _get_default_error_patterns(),
        "solutions.json": _get_default_solutions(),
        "performance_baselines.json": {},
        "workflow_patterns.json": {},
        "detected_issues.json": [],
        "config_rules.json": _get_default_config_rules(),
        "assistant_audit.log": "",  # Text file, not JSON
    }

    for filename, default_content in json_files.items():
        file_path = ai_context_dir / filename

        if not file_path.exists():
            try:
                if filename.endswith(".log"):
                    # Text file
                    file_path.write_text(default_content)
                else:
                    # JSON file
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(default_content, f, indent=2)

                if not _IS_WINDOWS:
                    try:
                        os.chmod(file_path, 0o644)
                    except (OSError, PermissionError):
                        pass  # best-effort: skip on access/IO error
            except Exception as e:
                ch.dim(f"Could not initialize {filename}: {e}")


def _get_default_error_patterns() -> list:
    """Get default error pattern definitions."""
    return [
        {"pattern": "Connection refused", "category": "network", "severity": "high"},
        {
            "pattern": "Access denied.*MySQL",
            "category": "permission",
            "severity": "high",
        },
        {
            "pattern": "Permission denied",
            "category": "permission",
            "severity": "medium",
        },
        {
            "pattern": "No such file or directory",
            "category": "file",
            "severity": "medium",
        },
        {
            "pattern": "Disk.*full|No space left",
            "category": "resource_exhaustion",
            "severity": "critical",
        },
        {
            "pattern": "Out of memory|OOM",
            "category": "resource_exhaustion",
            "severity": "critical",
        },
        {"pattern": "Timeout|timed out", "category": "network", "severity": "medium"},
        {"pattern": "Syntax error", "category": "syntax", "severity": "low"},
    ]


def _get_default_solutions() -> list:
    """Get default solution database."""
    return []  # Will be populated by Module 3


def _get_default_config_rules() -> list:
    """Get default configuration anti-patterns."""
    return []  # Will be populated by Module 1
