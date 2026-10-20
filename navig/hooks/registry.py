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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

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
    #: Where it was declared — a hooks.yaml path or ``plugin:<id>`` — for display.
    source: str = ""

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
        self._global_dir = global_dir or config_dir()
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

        # Installed CC/NAVIG plugins: translate each plugin's `hooks/hooks.json`
        # (Claude Code schema) into NAVIG definitions. Appended last so explicit
        # user/project hooks fire first; skipped entirely when hooks are disabled.
        if self._enabled:
            self._load_plugin_hooks()

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

    def all_definitions(self) -> list[HookDefinition]:
        """Every loaded definition regardless of event — for listing surfaces."""
        if not self._loaded:
            self.load()
        return list(self._definitions)

    def config_paths(self) -> list[Path]:
        """The two hooks.yaml files this registry reads (existing or not)."""
        return [self._global_dir / _HOOKS_FILENAME, self._project_dir / _HOOKS_FILENAME]

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

        hooks_section = raw.get("hooks", {}) if isinstance(raw, dict) else None
        if not isinstance(hooks_section, dict):
            logger.warning("hooks.registry: %s has no `hooks:` mapping — skipping", path)
            return

        # Master switch (default True if file exists + has a definitions key)
        # coerce_bool, not bool(): a quoted `"false"` is a truthy string, so the
        # master switch read as ON and — worse — `allow_network: "false"` below
        # read as ALLOW, failing a network guard open.
        from navig.core.coerce import coerce_bool

        enabled_val = hooks_section.get("enabled", True)
        self._enabled = coerce_bool(enabled_val, default=True)
        if not self._enabled:
            return

        # Global timeout override
        if "timeout_seconds" in hooks_section:
            try:
                self._timeout_seconds = int(hooks_section["timeout_seconds"])
            except (TypeError, ValueError):
                pass

        # Network allow flag
        self._allow_network = coerce_bool(hooks_section.get("allow_network"), default=False)

        # Parse definitions
        definitions = hooks_section.get("definitions") or []
        for entry in definitions if isinstance(definitions, list) else []:
            if not isinstance(entry, dict):
                logger.warning("hooks.registry: non-mapping entry in %s — skipping", path)
                continue
            defn = self._parse_entry(entry)
            if defn is not None:
                defn.source = str(path)
                self._definitions.append(defn)

    def _load_plugin_hooks(self) -> None:
        """Append hooks translated from installed plugins' `hooks/hooks.json`."""
        try:
            from navig.hooks.cc_bridge import load_plugin_hook_definitions

            self._definitions.extend(
                load_plugin_hook_definitions(default_timeout=self._timeout_seconds)
            )
        except Exception as exc:  # noqa: BLE001 — a bad plugin never blocks hook loading
            logger.warning("hooks.registry: plugin hook load failed: %s", exc)

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

        try:
            timeout = int(entry.get("timeout_seconds", self._timeout_seconds))
        except (TypeError, ValueError):
            # One bad timeout raised out of load() and dropped EVERY hook in every file.
            logger.warning("hooks.registry: bad timeout_seconds for %s — using default", event_raw)
            timeout = self._timeout_seconds

        return HookDefinition(
            event=event,
            command=command,
            tool_filter=str(entry.get("tool", "")),
            timeout_seconds=timeout,
            description=str(entry.get("description", "")),
        )
