"""
Context Layer for NAVIG Agent

Provides markdown-based context files for agent personality and user profiles,
inspired by SOUL.md, USER.md, and context layer patterns.

Context Files:
- SOUL.md: Agent personality, behavioral guidelines, expertise areas
- USER.md: User profile, preferences, infrastructure context
- MEMORY.md: Long-term knowledge (planned)
- Daily logs: Recent interactions (planned)

Usage:
    from navig.agent.context import ContextLayer

    ctx = ContextLayer()

    # Load all context for system prompt
    system_additions = ctx.get_system_prompt_context()

    # Update user profile
    ctx.update_user_profile({"name": "John", "timezone": "UTC"})
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# =============================================================================
# Paths
# =============================================================================


def get_navig_dir() -> Path:
    """Get NAVIG config directory."""
    return Path.home() / ".navig"


def get_context_dir() -> Path:
    """Get user context directory (same as workspace for consistency)."""
    return get_navig_dir() / "workspace"


def get_bundled_templates_dir() -> Path:
    """Get bundled context templates."""
    return Path(__file__).parent


# =============================================================================
# Markdown Frontmatter Parser
# =============================================================================


def parse_markdown_with_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """
    Parse markdown with YAML frontmatter.

    Returns:
        Tuple of (frontmatter_dict, body_content)
    """
    if not content.startswith("---"):
        return {}, content

    # Find end of frontmatter
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_end = end_match.end() + 3
    frontmatter_text = content[3 : frontmatter_end - 4]
    body = content[frontmatter_end:]

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        frontmatter = {}

    return frontmatter, body.strip()


def format_markdown_with_frontmatter(frontmatter: Dict[str, Any], body: str) -> str:
    """Format markdown with YAML frontmatter."""
    if not frontmatter:
        return body

    fm_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{fm_text}---\n{body}"


# =============================================================================
# Context Files
# =============================================================================


class ContextFile:
    """A single context file (SOUL.md, USER.md, etc.)."""

    def __init__(self, path: Path):
        self.path = path
        self._frontmatter: Dict[str, Any] = {}
        self._body: str = ""
        self._loaded = False

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def name(self) -> str:
        return self.path.stem

    def load(self) -> None:
        """Load context file from disk."""
        if not self.exists:
            self._frontmatter = {}
            self._body = ""
            self._loaded = True
            return

        content = self.path.read_text(encoding="utf-8")
        self._frontmatter, self._body = parse_markdown_with_frontmatter(content)
        self._loaded = True

    def save(self) -> None:
        """Save context file to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = format_markdown_with_frontmatter(self._frontmatter, self._body)
        self.path.write_text(content, encoding="utf-8")

    @property
    def frontmatter(self) -> Dict[str, Any]:
        if not self._loaded:
            self.load()
        return self._frontmatter

    @property
    def body(self) -> str:
        if not self._loaded:
            self.load()
        return self._body

    @property
    def content(self) -> str:
        """Get full content including frontmatter."""
        if not self._loaded:
            self.load()
        return format_markdown_with_frontmatter(self._frontmatter, self._body)

    def get_summary(self) -> Optional[str]:
        """Get summary from frontmatter."""
        return self.frontmatter.get("summary")

    def is_editable(self) -> bool:
        """Check if file is user-editable."""
        return self.frontmatter.get("editable", True)

    def update_body(self, new_body: str, save: bool = True) -> None:
        """Update the body content."""
        if not self._loaded:
            self.load()
        self._body = new_body
        if save:
            self.save()

    def update_frontmatter(self, updates: Dict[str, Any], save: bool = True) -> None:
        """Update frontmatter fields."""
        if not self._loaded:
            self.load()
        self._frontmatter.update(updates)
        if save:
            self.save()


# =============================================================================
# Context Layer
# =============================================================================


class ContextLayer:
    """
    Manager for agent context files.

    Provides access to SOUL.md (personality), USER.md (profile),
    and future context sources.
    """

    def __init__(self, context_dir: Optional[Path] = None):
        self.context_dir = context_dir or get_context_dir()
        self.templates_dir = get_bundled_templates_dir()

        # Context files
        self._soul: Optional[ContextFile] = None
        self._user: Optional[ContextFile] = None

    def ensure_context_dir(self) -> None:
        """Create context directory if it doesn't exist."""
        self.context_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # SOUL.md - Agent Personality
    # =========================================================================

    @property
    def soul(self) -> ContextFile:
        """Get SOUL.md context file."""
        if self._soul is None:
            self._soul = ContextFile(self.context_dir / "SOUL.md")
        return self._soul

    def ensure_soul(self) -> ContextFile:
        """Ensure SOUL.md exists, copying from template if needed."""
        if not self.soul.exists:
            self.ensure_context_dir()
            template = self.templates_dir / "SOUL.md"
            if template.exists():
                shutil.copy(template, self.soul.path)
            else:
                # Create minimal default
                self.soul._frontmatter = {
                    "summary": "Agent personality and guidelines",
                    "scope": "global",
                    "editable": True,
                }
                self.soul._body = "# SOUL.md\n\nDefine your agent's personality here."
                self.soul._loaded = True
                self.soul.save()
        return self.soul

    def get_soul_context(self) -> str:
        """Get SOUL.md content for system prompt."""
        if self.soul.exists:
            return self.soul.body
        return ""

    # =========================================================================
    # USER.md - User Profile
    # =========================================================================

    @property
    def user(self) -> ContextFile:
        """Get USER.md context file."""
        if self._user is None:
            self._user = ContextFile(self.context_dir / "USER.md")
        return self._user

    def ensure_user(self) -> ContextFile:
        """Ensure USER.md exists, copying from template if needed."""
        if not self.user.exists:
            self.ensure_context_dir()
            template = self.templates_dir / "USER.template.md"
            if template.exists():
                shutil.copy(template, self.user.path)
                # Rename to USER.md
                (self.context_dir / "USER.template.md").rename(self.user.path)
            else:
                # Create minimal default
                self.user._frontmatter = {
                    "summary": "User profile and preferences",
                    "scope": "user",
                    "editable": True,
                    "auto_update": True,
                }
                self.user._body = "# USER.md\n\nUser information will be added here."
                self.user._loaded = True
                self.user.save()
        return self.user

    def get_user_context(self) -> str:
        """Get USER.md content for system prompt."""
        if self.user.exists:
            return self.user.body
        return ""

    def get_user_preferences(self) -> Dict[str, Any]:
        """
        Get parsed user preferences from USER.md.

        Delegates to WorkspaceManager for robust parsing of preferences
        including timezone, work hours, communication style, etc.

        Returns:
            Dict with parsed user preferences
        """
        try:
            from navig.workspace import WorkspaceManager

            wm = WorkspaceManager(workspace_path=self.context_dir)
            return wm.get_user_preferences()
        except ImportError:
            return {}
        except Exception:
            return {}

    def update_user_profile(self, updates: Dict[str, str]) -> None:
        """
        Update user profile with new information.

        Intelligently updates the USER.md content based on the updates dict.
        """
        self.ensure_user()
        body = self.user.body

        for key, value in updates.items():
            # Try to find and update existing field
            pattern = rf"(\*\*{re.escape(key)}:\*\*\s*).*"
            if re.search(pattern, body, re.IGNORECASE):
                body = re.sub(pattern, rf"\1{value}", body, flags=re.IGNORECASE)
            else:
                # Append to Notes section
                if "## Notes" in body:
                    body = body.replace("## Notes", f"## Notes\n\n- **{key}:** {value}")

        self.user.update_body(body)

    # =========================================================================
    # Combined Context
    # =========================================================================

    def get_system_prompt_context(self) -> str:
        """
        Get combined context for system prompt.

        Returns a formatted string with SOUL and USER context.
        """
        parts = []

        # SOUL context (personality)
        soul_ctx = self.get_soul_context()
        if soul_ctx:
            parts.append(f"<agent_personality>\n{soul_ctx}\n</agent_personality>")

        # USER context (profile)
        user_ctx = self.get_user_context()
        if user_ctx:
            parts.append(f"<user_profile>\n{user_ctx}\n</user_profile>")

        return "\n\n".join(parts)

    def get_context_summary(self) -> Dict[str, Any]:
        """Get summary of available context files."""
        return {
            "soul": {
                "exists": self.soul.exists,
                "path": str(self.soul.path),
                "summary": self.soul.get_summary() if self.soul.exists else None,
            },
            "user": {
                "exists": self.user.exists,
                "path": str(self.user.path),
                "summary": self.user.get_summary() if self.user.exists else None,
            },
            "context_dir": str(self.context_dir),
        }

    def initialize(self) -> None:
        """Initialize context layer with default files."""
        self.ensure_soul()
        self.ensure_user()


# =============================================================================
# Module-level convenience functions
# =============================================================================

_default_context: Optional[ContextLayer] = None


def get_context_layer() -> ContextLayer:
    """Get the default context layer instance."""
    global _default_context
    if _default_context is None:
        _default_context = ContextLayer()
    return _default_context


def get_agent_context() -> str:
    """Get combined context for agent system prompt."""
    return get_context_layer().get_system_prompt_context()


def initialize_context() -> None:
    """Initialize context files with defaults."""
    get_context_layer().initialize()
