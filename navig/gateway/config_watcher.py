"""
Config Watcher - Hot-reload configuration changes

Watches for changes to:
- Global config (~/.navig/config.yaml)
- Project config (.navig/config.yaml)
- Workspace files (AGENTS.md, SOUL.md, etc.)
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()


class FileWatcher:
    """Watches a single file for changes."""

    def __init__(self, path: Path):
        self.path = path
        self.last_modified: float | None = None
        self.last_hash: str | None = None

        # Initialize if file exists
        if path.exists():
            self.last_modified = path.stat().st_mtime

    def has_changed(self) -> bool:
        """Check if file has changed since last check."""
        if not self.path.exists():
            if self.last_modified is not None:
                # File was deleted
                self.last_modified = None
                return True
            return False

        current_mtime = self.path.stat().st_mtime

        if self.last_modified is None:
            # File was created
            self.last_modified = current_mtime
            return True

        if current_mtime > self.last_modified:
            self.last_modified = current_mtime
            return True

        return False


class ConfigWatcher:
    """
    Watches configuration files and triggers reloads.

    Monitors:
    - Global config
    - Project configs
    - Workspace files
    - Agent configurations
    """

    def __init__(self, gateway: "NavigGateway", poll_interval: float = 5.0):
        self.gateway = gateway
        self.poll_interval = poll_interval

        self._running = False
        self._task: asyncio.Task | None = None

        # File watchers
        self._watchers: dict[str, FileWatcher] = {}

        # Callbacks by file type
        self._callbacks: dict[str, list[Callable]] = {
            "config": [],
            "workspace": [],
            "agents": [],
        }

        # Initialize watchers
        self._init_watchers()

    def _init_watchers(self) -> None:
        """Initialize file watchers."""
        config_manager = self.gateway.config_manager

        # Global config
        global_config_path = config_manager.global_config_dir / "config.yaml"
        if global_config_path.exists():
            self._watchers["global_config"] = FileWatcher(global_config_path)
            logger.debug(f"Watching global config: {global_config_path}")

        # Workspace files
        workspace_path = config_manager.global_config_dir / "workspace"
        if workspace_path.exists():
            for ws_file in [
                "AGENTS.md",
                "SOUL.md",
                "USER.md",
                "TOOLS.md",
                "HEARTBEAT.md",
                "MEMORY.md",
            ]:
                file_path = workspace_path / ws_file
                if file_path.exists():
                    self._watchers[f"ws_{ws_file}"] = FileWatcher(file_path)
                    logger.debug(f"Watching workspace file: {file_path}")

        # Project config (if in project context)
        project_config_path = Path(".navig/config.yaml")
        if project_config_path.exists():
            self._watchers["project_config"] = FileWatcher(project_config_path)
            logger.debug(f"Watching project config: {project_config_path}")

    def on_config_change(self, callback: Callable[[], None]) -> None:
        """Register callback for config changes."""
        self._callbacks["config"].append(callback)

    def on_workspace_change(self, callback: Callable[[str], None]) -> None:
        """Register callback for workspace file changes."""
        self._callbacks["workspace"].append(callback)

    def on_agents_change(self, callback: Callable[[], None]) -> None:
        """Register callback for agent configuration changes."""
        self._callbacks["agents"].append(callback)

    async def start(self) -> None:
        """Start watching for changes."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("Config watcher started")

    async def stop(self) -> None:
        """Stop watching."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        logger.info("Config watcher stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self._running:
            try:
                await self._check_changes()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in config watcher: {e}")
                await asyncio.sleep(self.poll_interval)

    async def _check_changes(self) -> None:
        """Check all watched files for changes."""
        config_changed = False
        workspace_changed: set[str] = set()

        for name, watcher in self._watchers.items():
            if watcher.has_changed():
                logger.info(f"Detected change: {name}")

                if name.endswith("_config"):
                    config_changed = True
                elif name.startswith("ws_"):
                    ws_file = name[3:]  # Remove 'ws_' prefix
                    workspace_changed.add(ws_file)

        # Trigger callbacks
        if config_changed:
            await self._handle_config_change()

        for ws_file in workspace_changed:
            await self._handle_workspace_change(ws_file)

    async def _handle_config_change(self) -> None:
        """Handle configuration file change."""
        logger.info("Reloading configuration...")

        # Reload config manager
        self.gateway.config_manager.load_config()

        # Update gateway config
        self.gateway._load_config()

        # Call registered callbacks
        for callback in self._callbacks["config"]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"Error in config change callback: {e}")

        # Emit system event
        if self.gateway.event_queue:
            await self.gateway.event_queue.emit("config_reloaded", {"source": "config_watcher"})

    async def _handle_workspace_change(self, filename: str) -> None:
        """Handle workspace file change."""
        logger.info(f"Workspace file changed: {filename}")

        # Read new content
        workspace_path = self.gateway.config_manager.global_config_dir / "workspace"
        file_path = workspace_path / filename

        content = ""
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")

        # Sync USER.md changes to UserProfile
        if filename == "USER.md":
            try:
                from navig.workspace import WorkspaceManager

                wm = WorkspaceManager()
                if wm.sync_to_user_profile():
                    logger.info("Synced USER.md preferences to UserProfile")
            except Exception as e:
                logger.warning(f"Failed to sync USER.md to UserProfile: {e}")

        # Call registered callbacks
        for callback in self._callbacks["workspace"]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(filename)
                else:
                    callback(filename)
            except Exception as e:
                logger.error(f"Error in workspace change callback: {e}")

        # Emit system event
        if self.gateway.event_queue:
            await self.gateway.event_queue.emit(
                "workspace_file_changed",
                {
                    "filename": filename,
                    "content_length": len(content),
                },
            )

    def add_watch(self, path: Path, name: str) -> None:
        """Add a new file to watch."""
        if name not in self._watchers:
            self._watchers[name] = FileWatcher(path)
            logger.debug(f"Added watch: {name} -> {path}")

    def remove_watch(self, name: str) -> None:
        """Remove a file from watching."""
        if name in self._watchers:
            del self._watchers[name]
            logger.debug(f"Removed watch: {name}")


class WorkspaceManager:
    """
    Manages workspace context files.

    These files provide persistent context for the AI agent:
    - AGENTS.md - Agent capabilities and bindings
    - SOUL.md - Agent personality and behavior
    - USER.md - User preferences and history
    - TOOLS.md - Available tools and shortcuts
    - HEARTBEAT.md - System monitoring instructions
    - MEMORY.md - Long-term memories and notes
    """

    DEFAULT_FILES = {
        "AGENTS.md": """# NAVIG Agents

## Available Agents

### default
The primary NAVIG assistant. Handles system operations (infrastructure,
databases, Docker, deployments) and life operations (tasks, knowledge,
routines, productivity workflows).

### monitor
Background monitoring agent. Checks host health, disk space,
certificate expiry, and other automated checks.

## Bindings

Configure in config.yaml:
```yaml
agents:
  bindings:
    - channel: telegram
      peer: "12345678"
      agentId: default
```
""",
        "SOUL.md": """# Agent Soul

## Personality
I am NAVIG, an operations assistant for both systems and life management.
I help manage remote hosts, applications, databases, workflows, and personal productivity.

## Behavior Principles
1. Be direct and concise
2. Prefer action over asking for confirmation
3. Warn about destructive operations
4. Log all significant actions
5. Learn from user preferences

## Response Style
- Use emojis sparingly for status (✅ ❌ ⚠️)
- Format command outputs in code blocks
- Summarize long outputs
- Offer next steps when helpful
""",
        "USER.md": """# User Context

## Preferences
- Notify on errors only (not successes)
- Preferred output format: concise
- Timezone: UTC

## Known Patterns
(Agent learns and updates this section)

## Quick Notes
(User can add notes here)
""",
        "TOOLS.md": """# Available Tools

## NAVIG Commands
- `host list` - List all configured hosts
- `host use <name>` - Set active host
- `app list` - List applications on host
- `run <cmd>` - Execute command on active host
- `db query <sql>` - Run database query
- `workflow run <name>` - Execute workflow

## Shortcuts
(Define custom shortcuts in config.yaml)
""",
        "HEARTBEAT.md": """# Heartbeat Instructions

## What to Check
1. Host connectivity (ping each configured host)
2. Disk space (warn if >80%)
3. Memory usage (warn if >90%)
4. Certificate expiry (warn if <14 days)
5. Service status (check critical services)

## Response Format
If everything is OK:
- Return exactly: HEARTBEAT_OK
- This suppresses notifications

If issues found:
- List issues with severity
- Suggest remediation steps
- Always notify user

## Schedule
Default: Every 30 minutes
Configure in config.yaml:
```yaml
heartbeat:
  interval: 30m
  enabled: true
```
""",
        "MEMORY.md": """# Long-term Memory

## Important Events
(Agent records significant events here)

## Learned Preferences
(Agent updates based on user behavior)

## Notes
(Persistent notes across sessions)

---
Last updated: Never
""",
    }

    def __init__(self, base_path: Path):
        self.base_path = base_path / "workspace"

    def ensure_files(self) -> None:
        """Ensure all workspace files exist with defaults."""
        self.base_path.mkdir(parents=True, exist_ok=True)

        for filename, content in self.DEFAULT_FILES.items():
            file_path = self.base_path / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                logger.info(f"Created workspace file: {filename}")

    def read_file(self, filename: str) -> str:
        """Read a workspace file."""
        file_path = self.base_path / filename
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""

    def write_file(self, filename: str, content: str) -> None:
        """Write to a workspace file."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        file_path = self.base_path / filename
        file_path.write_text(content, encoding="utf-8")
        logger.debug(f"Updated workspace file: {filename}")

    def append_to_file(self, filename: str, content: str) -> None:
        """Append content to a workspace file."""
        existing = self.read_file(filename)
        self.write_file(filename, existing + "\n" + content)

    def get_context(self) -> str:
        """Get all workspace files as context string."""
        context_parts = []

        for filename in self.DEFAULT_FILES.keys():
            content = self.read_file(filename)
            if content:
                context_parts.append(f"## {filename}\n\n{content}")

        return "\n\n---\n\n".join(context_parts)

    def update_memory(self, entry: str) -> None:
        """Add an entry to MEMORY.md."""

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry_line = f"- [{timestamp}] {entry}"

        memory = self.read_file("MEMORY.md")

        # Insert before "---" line if it exists
        if "---" in memory:
            parts = memory.split("---", 1)
            memory = parts[0] + entry_line + "\n\n---" + parts[1]
        else:
            memory += f"\n{entry_line}"

        # Update last updated timestamp
        memory = memory.replace("Last updated: Never", f"Last updated: {timestamp}")

        self.write_file("MEMORY.md", memory)
