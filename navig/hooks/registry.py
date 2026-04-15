"""
Hook registry — discovers and validates hook definitions from YAML config files.

Hook config schema (``~/.navig/hooks.yaml`` or ``.navig/hooks.yaml``)::

    hooks:
      enabled: true          # master switch (default: true if file exists)
      timeout_seconds: 30    # per-hook subprocess timeout
      allow_network: false   # allow HTTP hooks (SSRF guard still applies)
      definitions:
        - event: PreToolUse
          tool: "bash"          # optional tool filter (glob-matched, empty = all)
          command: "/usr/local/bin/my-pre-hook"
          # Additional allowed keys: description, timeout_seconds (override)
        - event: PostToolUse
          command: "~/.navig/hooks/post_tool.sh"
        - event: SessionStart
          command: "~/.navig/hooks/session_start.py"

Project ``.navig/hooks.yaml`` definitions are *merged after* global ones so
project-level hooks can extend or override global behaviour by adding entries
for the same event.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import HookEvent

logger = logging.getLogger("navig.hooks.registry")

# Module-level constants
_DEFAULT_HOOK_TIMEOUT_SECONDS: int = 30
_HOOKS_FILENAME = "hooks.yaml"


@dataclass
class HookDefinition:
    """One registered hook entry."""

    event: HookEvent
    command: str           # shell command or path to executable
    tool_filter: str = ""  # empty = match all tools; supports glob (e.g. "bash*")
    timeout_seconds: int = _DEFAULT_HOOK_TIMEOUT_SECONDS
    description: str = ""

    def matches_tool(self, tool_name: str) -> bool:
        """Return True if this hook applies to *tool_name*."""
        if not self.tool_filter:
            return True
        return fnmatch.fnmatch(tool_name.lower(), self.tool_filter.lower())


class HookRegistry:
    """Loads and serves hook definitions from global and project YAML files.

    Definitions from the project file are appended *after* global ones so
    per-project hooks fire last (and can observe global hook results).
    """

    def __init__(
        self,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_dir = global_dir or Path.home() / ".navig"
        self._project_dir = project_dir or Path(".navig")
        self._definitions: list[HookDefinition] = []
        self._enabled: bool = True
        self._timeout_seconds: int = _DEFAULT_HOOK_TIMEOUT_SECONDS
        self._allow_network: bool = False
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """(Re)load hook definitions from both config files."""
        self._definitions = []

        global_path = self._global_dir / _HOOKS_FILENAME
        project_path = self._project_dir / _HOOKS_FILENAME

        # Global config first, project config extends/overrides
        for path in (global_path, project_path):
            if path.exists():
                self._load_file(path)

        self._loaded = True
        logger.debug(
            "hooks.registry: loaded %d definitions (enabled=%s)",
            len(self._definitions),
            self._enabled,
        )

    def get_hooks_for_event(
        self, event: HookEvent, tool_name: str = ""
    ) -> list[HookDefinition]:
        """Return all definitions that match *event* and *tool_name*."""
        if not self._loaded:
            self.load()
        if not self._enabled:
            return []
        return [
            d
            for d in self._definitions
            if d.event == event and d.matches_tool(tool_name)
        ]

    @property
    def enabled(self) -> bool:
        if not self._loaded:
            self.load()
        return self._enabled

    @property
    def allow_network(self) -> bool:
        if not self._loaded:
            self.load()
        return self._allow_network

    @property
    def default_timeout(self) -> int:
        return self._timeout_seconds

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_file(self, path: Path) -> None:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("hooks.registry: PyYAML not installed — skipping %s", path)
            return

        try:
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("hooks.registry: failed to read %s: %s", path, exc)
            return

        hooks_section = raw.get("hooks", {})

        # Master switch (default True if file exists + has a definitions key)
        enabled_val = hooks_section.get("enabled", True)
        self._enabled = bool(enabled_val)
        if not self._enabled:
            return

        # Global timeout override
        if "timeout_seconds" in hooks_section:
            try:
                self._timeout_seconds = int(hooks_section["timeout_seconds"])
            except (TypeError, ValueError):
                pass

        # Network allow flag
        self._allow_network = bool(hooks_section.get("allow_network", False))

        # Parse definitions
        for entry in hooks_section.get("definitions", []):
            defn = self._parse_entry(entry)
            if defn is not None:
                self._definitions.append(defn)

    def _parse_entry(self, entry: dict[str, Any]) -> HookDefinition | None:
        event_raw = entry.get("event", "")
        # Accept both short name ("PreToolUse") and enum value equivalents
        event_map = {e.value: e for e in HookEvent}
        event_map.update({e.name: e for e in HookEvent})
        event = event_map.get(event_raw)
        if event is None:
            logger.warning(
                "hooks.registry: unknown event '%s' — skipping entry", event_raw
            )
            return None

        command = str(entry.get("command", "")).strip()
        if not command:
            logger.warning("hooks.registry: empty command for event %s — skipping", event_raw)
            return None

        # Expand ~ in command path
        command = os.path.expanduser(command)

        timeout = int(entry.get("timeout_seconds", self._timeout_seconds))

        return HookDefinition(
            event=event,
            command=command,
            tool_filter=str(entry.get("tool", "")),
            timeout_seconds=timeout,
            description=str(entry.get("description", "")),
        )
