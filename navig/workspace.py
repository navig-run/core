"""
NAVIG Workspace Manager

Manages the workspace directory containing agent context files (bootstrap files).
Inspired by advanced workspace and agent template systems.
"""

import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.workspace_ownership import (
    USER_NAVIG_DIR,
    USER_WORKSPACE_DIR,
    detect_project_workspace_duplicates,
    is_project_workspace_path,
    resolve_personal_workspace_path,
)

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_NAVIG_DIR = USER_NAVIG_DIR
DEFAULT_WORKSPACE_DIR = USER_WORKSPACE_DIR
DEFAULT_CONFIG_FILE = DEFAULT_NAVIG_DIR / "navig.json"


class WorkspaceManager:
    """
    Manages NAVIG workspace and context injection.

    The workspace contains markdown files that define the agent's
    personality, capabilities, and user preferences. These are
    injected into the AI context at session start.
    """

    # Bootstrap files in priority order
    BOOTSTRAP_FILES = [
        "IDENTITY.md",  # Agent identity (name, emoji, personality)
        "SOUL.md",  # Persona, boundaries, ethics
        "AGENTS.md",  # Operating instructions
        "TOOLS.md",  # Available tools and conventions
        "USER.md",  # User profile and preferences
        "HEARTBEAT.md",  # Background task instructions (optional)
        "BOOTSTRAP.md",  # First-run only (deleted after)
    ]

    def __init__(
        self,
        workspace_path: Path | None = None,
        config_path: Path | None = None,
    ):
        """
        Initialize the workspace manager.

        Args:
            workspace_path: Custom workspace directory (default: ~/.navig/workspace)
            config_path: Custom config file path (default: ~/.navig/navig.json)
        """
        self.config_path = config_path or DEFAULT_CONFIG_FILE

        # Load config to get workspace path
        self.config = self._load_config()

        # Use provided path, config path, or default
        # AUDIT self-check: Correct implementation? yes - explicit non-project paths are honored, project-local paths are canonicalized.
        # AUDIT self-check: Break callers? no - implicit/default paths still use ownership resolution.
        # AUDIT self-check: Simpler alternative? yes - short explicit-path fast path.
        if workspace_path is not None:
            explicit_path = self._validated_workspace_override(Path(workspace_path))
            explicit_is_project_style = (
                explicit_path.name == "workspace"
                and explicit_path.parent.name == ".navig"
            )
            if explicit_is_project_style or is_project_workspace_path(
                explicit_path, project_root=Path.cwd()
            ):
                self.workspace_path, self.legacy_workspace_path = (
                    resolve_personal_workspace_path(
                        explicit_path,
                        project_root=Path.cwd(),
                    )
                )
            else:
                self.workspace_path = explicit_path
                self.legacy_workspace_path = None
        else:
            if self.config:
                requested_workspace = Path(
                    self.config.get("agents", {})
                    .get("defaults", {})
                    .get("workspace", str(DEFAULT_WORKSPACE_DIR))
                )
            else:
                requested_workspace = DEFAULT_WORKSPACE_DIR

            # Personal/state files are now canonical at ~/.navig/workspace.
            self.workspace_path, self.legacy_workspace_path = (
                resolve_personal_workspace_path(
                    requested_workspace,
                    project_root=Path.cwd(),
                )
            )

        if self.legacy_workspace_path:
            logger.warning(
                "Legacy project workspace path detected (%s). Personal files are now canonical at %s.",
                self.legacy_workspace_path,
                self.workspace_path,
            )
            duplicates = detect_project_workspace_duplicates(project_root=Path.cwd())
            if duplicates:
                logger.warning(
                    "Detected %d project-level personal workspace duplicate(s). "
                    "Using user-level copies as source of truth.",
                    len(duplicates),
                )

    def _validated_workspace_override(self, requested: Path) -> Path:
        """
        Validate explicit workspace override against trusted roots.

        Allowed roots:
        - User home directory
        - Current project directory
        - System temporary directory (for isolated tests)
        """
        resolved = requested.expanduser()
        try:
            resolved = resolved.resolve()
        except Exception:
            resolved = resolved.absolute()

        project_root = Path.cwd().resolve()
        allowed_roots = (
            Path.home().resolve(),
            project_root,
            Path(tempfile.gettempdir()).resolve(),
        )
        if any(root == resolved or root in resolved.parents for root in allowed_roots):
            return resolved

        logger.warning(
            "Rejected workspace_path outside trusted roots: %s. Falling back to %s.",
            requested,
            USER_WORKSPACE_DIR,
        )
        return USER_WORKSPACE_DIR

    def _candidate_workspace_paths(self) -> list[Path]:
        """Return workspace paths in read-priority order."""
        paths = [self.workspace_path]
        if self.legacy_workspace_path and self.legacy_workspace_path not in paths:
            paths.append(self.legacy_workspace_path)
        return paths

    def _load_config(self) -> dict[str, Any] | None:
        """Load configuration from JSON file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")
        return None

    def is_initialized(self) -> bool:
        """Check if workspace has been initialized."""
        for ws_path in self._candidate_workspace_paths():
            if ws_path.exists() and any(
                (ws_path / f).exists() for f in self.BOOTSTRAP_FILES
            ):
                return True
        return False

    def get_bootstrap_content(self, include_first_run: bool = True) -> str:
        """
        Get combined content from all bootstrap files.

        Args:
            include_first_run: Include BOOTSTRAP.md if it exists

        Returns:
            Combined markdown content from all bootstrap files
        """
        content_parts = []

        for filename in self.BOOTSTRAP_FILES:
            if filename == "BOOTSTRAP.md" and not include_first_run:
                continue

            file_path = self.workspace_path / filename
            if file_path.exists():
                try:
                    file_content = file_path.read_text(encoding="utf-8")
                    # Strip YAML frontmatter for cleaner context
                    if file_content.startswith("---"):
                        parts = file_content.split("---", 2)
                        if len(parts) >= 3:
                            file_content = parts[2].strip()

                    content_parts.append(f"## {filename}\n\n{file_content}")
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")

        return "\n\n---\n\n".join(content_parts)

    def get_file_content(self, filename: str) -> str | None:
        """Get content of a specific workspace file."""
        for ws_path in self._candidate_workspace_paths():
            file_path = ws_path / filename
            if file_path.exists():
                try:
                    return file_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")
        return None

    def update_file(self, filename: str, content: str) -> bool:
        """
        Update a workspace file.

        Args:
            filename: Name of the file to update
            content: New content

        Returns:
            True if successful
        """
        try:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            file_path = self.workspace_path / filename
            file_path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to update {filename}: {e}")
            return False

    def complete_bootstrap(self) -> bool:
        """
        Complete the first-run bootstrap.

        Removes BOOTSTRAP.md after the first successful interaction,
        as it's only meant for initial setup.

        Returns:
            True if BOOTSTRAP.md was removed
        """
        for ws_path in self._candidate_workspace_paths():
            bootstrap_path = ws_path / "BOOTSTRAP.md"
            if bootstrap_path.exists():
                try:
                    bootstrap_path.unlink()
                    logger.info("Bootstrap completed - BOOTSTRAP.md removed")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to remove BOOTSTRAP.md: {e}")
        return False

    def has_bootstrap_pending(self) -> bool:
        """Check if BOOTSTRAP.md exists (first-run not completed)."""
        return any(
            (ws_path / "BOOTSTRAP.md").exists()
            for ws_path in self._candidate_workspace_paths()
        )

    def get_agent_identity(self) -> dict[str, str]:
        """
        Extract agent identity from IDENTITY.md.

        Returns:
            Dict with name, emoji, and personality traits
        """
        identity = {
            "name": "NAVIG",
            "emoji": "�",
            "personality": "vigilant, decisive, protective",
        }

        content = self.get_file_content("IDENTITY.md")
        if content:
            # Simple parsing for key fields
            lines = content.split("\n")
            for line in lines:
                if line.startswith("**Name**:"):
                    identity["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("**Emoji**:"):
                    identity["emoji"] = line.split(":", 1)[1].strip()

        return identity

    def get_user_preferences(self) -> dict[str, Any]:
        """
        Extract user preferences from USER.md.

        Parses markdown fields using regex to handle variations like
        '**Preferred Editor**' vs '**Preferred Editor/IDE**'.

        Returns:
            Dict with user preferences including identity, work patterns,
            communication style, and technical stack details.
        """
        preferences = {
            # Identity
            "name": None,
            "preferred_name": None,
            "pronouns": None,
            "timezone": "UTC",
            "location": None,
            # Work patterns
            "work_hours": None,
            "do_not_disturb": None,
            "peak_productivity": None,
            "low_energy_times": None,
            "status_summaries": "daily",
            # Communication
            "verbosity": "normal",
            "confirm_destructive": True,
            "communication_style": "direct",
            "primary_language": None,
            "secondary_language": None,
            "notification_channels": {},
            # Technical
            "primary_languages": [],
            "other_languages": [],
            "preferred_editor": None,
            "os": None,
            "cloud_preference": None,
            "shell": [],
            "package_managers": [],
            "risk_tolerance": "medium",
            # NAVIG Features
            "proactive_assistance": False,
            "daily_logs": False,
            "voice_tts": False,
            # Life-OS
            "goals": [],
            "health_focus": None,
            "wealth_focus": None,
            "learning_targets": [],
            # Automation
            "autonomous_actions": [],
            "requires_confirmation": [],
        }

        content = self.get_file_content("USER.md")
        if not content:
            return preferences

        # Regex map for flexible field matching
        field_patterns = [
            # Identity
            (r"\*\*Name\*\*:", "name"),
            (r"\*\*Preferred Name\*\*:", "preferred_name"),
            (r"\*\*Pronouns\*\*:", "pronouns"),
            (r"\*\*Timezone\*\*:", "timezone"),
            (r"\*\*Primary Location\*\*:", "location"),
            # Work patterns
            (r"\*\*Work Hours\*\*:", "work_hours"),
            (r"\*\*Do Not Disturb\*\*:", "do_not_disturb"),
            (r"\*\*Peak (Productivity|Focus Windows?)\*\*:", "peak_productivity"),
            (r"\*\*Low.Energy Times?\*\*:", "low_energy_times"),
            (r"\*\*Status summaries\*\*:", "status_summaries"),
            # Communication
            (r"\*\*Verbosity\*\*:", "verbosity"),
            (r"\*\*Confirm Destructive( Actions)?\*\*:", "confirm_destructive"),
            (r"\*\*Tone\*\*:", "communication_style"),
            (r"\*\*Communication( Style)?\*\*:", "communication_style"),
            (r"\*\*Primary\*\*:", "primary_language"),
            (r"\*\*Secondary\*\*:", "secondary_language"),
            (r"\*\*Telegram\*\*:", "notification_telegram"),
            (r"\*\*Email\*\*:", "notification_email"),
            # Technical
            (r"\*\*Primary Languages?\*\*:", "primary_languages"),
            (r"\*\*Other Languages?\*\*:", "other_languages"),
            (r"\*\*Preferred Editor(/IDE)?\*\*:", "preferred_editor"),
            (r"\*\*OS( / Platforms)?\*\*:", "os"),
            (r"\*\*Cloud( / Hosting)? Preference\*\*:", "cloud_preference"),
            (r"\*\*Shells?( in Use)?\*\*:", "shell"),
            (r"\*\*Package Managers?\*\*:", "package_managers"),
            (r"\*\*Risk Tolerance( for Automation)?\*\*:", "risk_tolerance"),
            # NAVIG Features
            (r"\*\*Proactive Assistance\*\*:", "proactive_assistance"),
            (r"\*\*Daily Logs\*\*:", "daily_logs"),
            (r"\*\*Voice/TTS\*\*:", "voice_tts"),
            # Life-OS
            (r"\*\*Top 3 Current Goals\*\*:", "goals"),
            (r"\*\*Health & Longevity Focus\*\*:", "health_focus"),
            (r"\*\*Wealth & Work Focus\*\*:", "wealth_focus"),
            (r"\*\*Learning & Skill Targets?\*\*:", "learning_targets"),
        ]

        # Parse numbered goals (1. 2. 3.)
        goal_pattern = re.compile(r"^\s*\d+\.\s+(.+)$")
        in_goals_section = False

        for line in content.split("\n"):
            original_line = line
            line = line.strip()

            # Track goals section
            if "Top 3 Current Goals" in line or "## 5. Life" in line:
                in_goals_section = True
            elif line.startswith("## ") and in_goals_section:
                in_goals_section = False

            # Parse numbered goals
            if in_goals_section:
                goal_match = goal_pattern.match(original_line)
                if goal_match:
                    goal_text = goal_match.group(1).strip()
                    if goal_text and not goal_text.startswith("…"):
                        preferences["goals"].append(goal_text)

            # Allow bullet or no bullet
            if line.startswith("- "):
                line = line[2:]
            elif line.startswith("-"):
                line = line[1:]

            for pattern, key in field_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    raw_value = line[match.end() :].strip()

                    # Handle empty values or placeholders
                    if not raw_value or (
                        raw_value.startswith("(") and raw_value.endswith(")")
                    ):
                        continue

                    # Cleanup comments
                    if "(" in raw_value and key in ("do_not_disturb", "work_hours"):
                        raw_value = raw_value.split("(")[0].strip()

                    # Parse types
                    if key == "confirm_destructive":
                        preferences[key] = "yes" in raw_value.lower()
                    elif key == "verbosity":
                        preferences[key] = raw_value.split()[0].lower()
                    elif key in (
                        "primary_languages",
                        "other_languages",
                        "shell",
                        "package_managers",
                        "learning_targets",
                    ):
                        preferences[key] = [
                            l.strip() for l in raw_value.split(",") if l.strip()
                        ]
                    elif key == "risk_tolerance":
                        lower_val = raw_value.lower()
                        if "low" in lower_val:
                            preferences[key] = "low"
                        elif "high" in lower_val:
                            preferences[key] = "high"
                        elif "medium" in lower_val:
                            preferences[key] = "medium"
                    elif key in ("proactive_assistance", "daily_logs", "voice_tts"):
                        preferences[key] = (
                            "enabled" in raw_value.lower() or "yes" in raw_value.lower()
                        )
                    elif key == "notification_telegram":
                        preferences["notification_channels"]["telegram"] = raw_value
                    elif key == "notification_email":
                        preferences["notification_channels"]["email"] = raw_value
                    elif key == "goals":
                        pass  # Handled separately above
                    else:
                        preferences[key] = raw_value
                    break

        return preferences

    def get_user_greeting(self) -> str:
        """Get personalized greeting for the user."""
        prefs = self.get_user_preferences()
        name = prefs.get("preferred_name") or prefs.get("name")
        return f"Hey {name}!" if name else "Hey there!"

    def is_do_not_disturb(self) -> bool:
        """
        Check if current time is within Do Not Disturb hours.

        Returns:
            True if notifications should be suppressed
        """
        prefs = self.get_user_preferences()
        dnd = prefs.get("do_not_disturb")

        if not dnd:
            return False

        try:
            # Normalize separators
            dnd = dnd.replace("\u2013", "-").replace("\u2014", "-").replace(" to ", "-")

            start_hour, end_hour = 0, 0

            # Match 24h: 23:00-07:00
            m24 = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", dnd)
            if m24:
                start_hour = int(m24.group(1))
                end_hour = int(m24.group(3))
            else:
                # Match 12h: 11 PM - 7 AM
                match = re.search(
                    r"(\d{1,2})\s*(AM|PM)?\s*-\s*(\d{1,2})\s*(AM|PM)?",
                    dnd,
                    re.IGNORECASE,
                )
                if not match:
                    return False

                h1 = int(match.group(1))
                p1 = (match.group(2) or "").upper()
                h2 = int(match.group(3))
                p2 = (match.group(4) or "").upper()

                def to_24(h, p):
                    if p == "PM" and h != 12:
                        return h + 12
                    if p == "AM" and h == 12:
                        return 0
                    return h

                start_hour = to_24(h1, p1)
                end_hour = to_24(h2, p2)

            now = datetime.now().hour

            if start_hour > end_hour:
                return now >= start_hour or now < end_hour
            else:
                return start_hour <= now < end_hour

        except Exception:
            return False

    def build_system_prompt(self, base_prompt: str = "") -> str:
        """
        Build a complete system prompt with workspace context.

        Args:
            base_prompt: Base system prompt to extend

        Returns:
            Complete system prompt with injected workspace context
        """
        parts = []

        # Add base prompt if provided
        if base_prompt:
            parts.append(base_prompt)

        # Add workspace context
        if self.is_initialized():
            workspace_content = self.get_bootstrap_content()
            if workspace_content:
                parts.append("\n\n# Agent Context\n\n" + workspace_content)

        return "\n".join(parts)

    def add_memory(self, key: str, value: str) -> bool:
        """
        Add a memory entry to AGENTS.md.

        Args:
            key: Memory key (e.g., "servers", "preferences")
            value: Memory value

        Returns:
            True if successful
        """
        agents_path = self.workspace_path / "AGENTS.md"
        if not agents_path.exists():
            return False

        try:
            content = agents_path.read_text(encoding="utf-8")

            # Find the Memory section and add entry
            if "## Memory" in content:
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if "## Memory" in line:
                        # Find the end of the Memory section
                        j = i + 1
                        while j < len(lines) and not lines[j].startswith("## "):
                            j += 1
                        # Insert new memory entry before next section
                        entry = f"\n- **{key}**: {value}"
                        lines.insert(j, entry)
                        break

                content = "\n".join(lines)
                agents_path.write_text(content, encoding="utf-8")
                return True
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")

        return False

    def sync_to_user_profile(self) -> bool:
        """
        Sync USER.md preferences to the UserProfile system.

        This bridges the human-editable markdown file (USER.md) with the
        programmatic JSON profile (~/.navig/memory/user_profile.json).

        Returns:
            True if sync was successful
        """
        try:
            from navig.memory.user_profile import get_profile
        except ImportError:
            logger.warning("UserProfile system not available")
            return False

        prefs = self.get_user_preferences()
        if not prefs:
            return False

        profile = get_profile()
        updates = {}

        # Map USER.md fields to UserProfile structure
        if prefs.get("name"):
            updates["identity.name"] = prefs["name"]
        if prefs.get("preferred_name"):
            updates["identity.name"] = prefs["preferred_name"]  # Prefer nickname
        if prefs.get("timezone"):
            updates["identity.timezone"] = prefs["timezone"]

        # Work patterns
        if prefs.get("work_hours"):
            updates["work_patterns.active_hours"] = [prefs["work_hours"]]

        # Technical context
        if prefs.get("primary_languages"):
            updates["technical_context.stack"] = prefs["primary_languages"]
        if prefs.get("preferred_editor"):
            updates["technical_context.preferences"] = [prefs["preferred_editor"]]
        if prefs.get("os"):
            updates["technical_context.preferences"] = [prefs["os"]]
        if prefs.get("shell"):
            shells = (
                prefs["shell"] if isinstance(prefs["shell"], list) else [prefs["shell"]]
            )
            for shell in shells:
                if shell and not shell.startswith("("):
                    updates.setdefault("technical_context.preferences", []).append(
                        shell
                    )

        # Preferences
        if prefs.get("communication_style"):
            updates["preferences.communication_style"] = prefs["communication_style"]
        if prefs.get("confirm_destructive"):
            updates["preferences.confirmation_required_for"] = [
                "delete",
                "drop",
                "truncate",
            ]

        # Goals
        if prefs.get("goals"):
            updates["goals"] = prefs["goals"]

        # Apply updates
        if updates:
            updated = profile.update(updates, auto_save=True)
            logger.info(f"Synced {len(updated)} fields from USER.md to UserProfile")
            return len(updated) > 0

        return False


def get_workspace_manager() -> WorkspaceManager:
    """Get a singleton workspace manager instance."""
    return WorkspaceManager()


# CLI helper for workspace operations
def workspace_status():
    """Print workspace status."""
    wm = WorkspaceManager()

    print(f"Workspace: {wm.workspace_path}")
    print(f"Initialized: {wm.is_initialized()}")
    print(f"Bootstrap pending: {wm.has_bootstrap_pending()}")

    if wm.is_initialized():
        identity = wm.get_agent_identity()
        print(f"\nAgent: {identity['emoji']} {identity['name']}")

        print("\nBootstrap files:")
        for f in wm.BOOTSTRAP_FILES:
            exists = (wm.workspace_path / f).exists()
            status = "✓" if exists else "✗"
            print(f"  {status} {f}")


if __name__ == "__main__":
    workspace_status()
