"""
User Profile - Persistent knowledge about the human operator.

Stores structured profile data (JSON) and freeform notes (Markdown).
Enables NAVIG to learn about users across sessions and generate
personalized responses.
"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _get_memory_dir() -> Path:
    """Get the memory directory path."""
    return Path.home() / '.navig' / 'memory'


def _ensure_dir(path: Path) -> None:
    """Ensure directory exists."""
    path.mkdir(parents=True, exist_ok=True)


@dataclass
class MemoryNote:
    """A timestamped freeform note about the user."""

    timestamp: str
    category: str
    content: str
    source: str = "agent"  # agent, user, system

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'MemoryNote':
        return cls(**data)

    def to_markdown(self) -> str:
        """Format as markdown entry."""
        return f"### [{self.timestamp}] {self.category}\n_{self.source}_\n\n{self.content}\n\n---\n"


@dataclass
class UserIdentity:
    """Core identity information about the user."""
    name: Optional[str] = None
    role: Optional[str] = None
    timezone: Optional[str] = None
    location: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> 'UserIdentity':
        return cls(
            name=data.get('name'),
            role=data.get('role'),
            timezone=data.get('timezone'),
            location=data.get('location')
        )


@dataclass
class WorkPatterns:
    """Work habits and preferences."""
    active_hours: List[str] = field(default_factory=list)  # e.g., ["9-17 EST", "weekdays"]
    deploy_preferences: Dict[str, Any] = field(default_factory=dict)  # e.g., {"preferred_day": "friday", "window": "evening"}
    common_tasks: List[str] = field(default_factory=list)  # e.g., ["docker restart", "db backup"]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'WorkPatterns':
        return cls(
            active_hours=data.get('active_hours', []),
            deploy_preferences=data.get('deploy_preferences', {}),
            common_tasks=data.get('common_tasks', [])
        )


@dataclass
class TechnicalContext:
    """Technical environment and stack."""
    stack: List[str] = field(default_factory=list)  # e.g., ["Laravel", "Docker", "PostgreSQL"]
    managed_hosts: List[str] = field(default_factory=list)  # e.g., ["myserver", "production"]
    primary_projects: List[str] = field(default_factory=list)  # e.g., ["my-saas", "api-backend"]
    preferences: List[str] = field(default_factory=list)  # e.g., ["Python", "CLI tools", "Docker"]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'TechnicalContext':
        return cls(
            stack=data.get('stack', []),
            managed_hosts=data.get('managed_hosts', []),
            primary_projects=data.get('primary_projects', []),
            preferences=data.get('preferences', [])
        )


@dataclass
class UserPreferences:
    """Communication and operational preferences."""
    communication_style: Optional[str] = None  # e.g., "concise", "detailed", "casual"
    alert_thresholds: Dict[str, Any] = field(default_factory=dict)  # e.g., {"disk_warning": 80, "cpu_critical": 95}
    confirmation_required_for: List[str] = field(default_factory=list)  # e.g., ["delete", "restart_production"]

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v or isinstance(v, (list, dict))}

    @classmethod
    def from_dict(cls, data: dict) -> 'UserPreferences':
        return cls(
            communication_style=data.get('communication_style'),
            alert_thresholds=data.get('alert_thresholds', {}),
            confirmation_required_for=data.get('confirmation_required_for', [])
        )


@dataclass
class InteractionStats:
    """Usage statistics (updated automatically, no confirmation needed)."""
    total_sessions: int = 0
    total_commands: int = 0
    most_used_commands: Dict[str, int] = field(default_factory=dict)
    last_active: Optional[str] = None
    first_seen: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'InteractionStats':
        return cls(
            total_sessions=data.get('total_sessions', 0),
            total_commands=data.get('total_commands', 0),
            most_used_commands=data.get('most_used_commands', {}),
            last_active=data.get('last_active'),
            first_seen=data.get('first_seen')
        )

    def record_command(self, command: str) -> None:
        """Record a command usage."""
        self.total_commands += 1
        # Track top-level command only
        base_cmd = command.split()[0] if command else "unknown"
        self.most_used_commands[base_cmd] = self.most_used_commands.get(base_cmd, 0) + 1
        self.last_active = datetime.now().isoformat()


@dataclass
class UserProfile:
    """
    Complete user profile with structured data and freeform notes.
    
    Usage:
        profile = UserProfile.load()
        profile.identity.name = "Alex"
        profile.add_note("Prefers Friday deployments", "work_patterns")
        profile.save()
    """

    identity: UserIdentity = field(default_factory=UserIdentity)
    work_patterns: WorkPatterns = field(default_factory=WorkPatterns)
    technical_context: TechnicalContext = field(default_factory=TechnicalContext)
    preferences: UserPreferences = field(default_factory=UserPreferences)
    stats: InteractionStats = field(default_factory=InteractionStats)
    goals: List[str] = field(default_factory=list)
    notes: List[MemoryNote] = field(default_factory=list)

    # File paths
    _profile_path: Path = field(default_factory=lambda: _get_memory_dir() / 'user_profile.json')
    _notes_path: Path = field(default_factory=lambda: _get_memory_dir() / 'user_notes.md')
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'schema_version': 1,
            'identity': self.identity.to_dict(),
            'work_patterns': self.work_patterns.to_dict(),
            'technical_context': self.technical_context.to_dict(),
            'preferences': self.preferences.to_dict(),
            'stats': self.stats.to_dict(),
            'goals': self.goals,
            'notes': [n.to_dict() for n in self.notes[-50:]]  # Keep last 50 notes in JSON
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'UserProfile':
        """Create from dictionary."""
        return cls(
            identity=UserIdentity.from_dict(data.get('identity', {})),
            work_patterns=WorkPatterns.from_dict(data.get('work_patterns', {})),
            technical_context=TechnicalContext.from_dict(data.get('technical_context', {})),
            preferences=UserPreferences.from_dict(data.get('preferences', {})),
            stats=InteractionStats.from_dict(data.get('stats', {})),
            goals=data.get('goals', []),
            notes=[MemoryNote.from_dict(n) for n in data.get('notes', [])]
        )

    @classmethod
    def load(cls, profile_path: Optional[Path] = None) -> 'UserProfile':
        """Load profile from disk or create empty one."""
        path = profile_path or (_get_memory_dir() / 'user_profile.json')

        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                profile = cls.from_dict(data)
                profile._profile_path = path
                profile._notes_path = path.parent / 'user_notes.md'
                return profile
            except (json.JSONDecodeError, KeyError):
                # Corrupted file - backup and start fresh
                backup_path = path.with_suffix('.json.bak')
                shutil.copy2(path, backup_path)
                print(f"[!] Profile corrupted, backed up to {backup_path}")

        # Create new profile
        _ensure_dir(path.parent)
        profile = cls()
        profile._profile_path = path
        profile._notes_path = path.parent / 'user_notes.md'
        profile.stats.first_seen = datetime.now().isoformat()
        return profile

    def save(self) -> None:
        """Save profile to disk atomically."""
        with self._lock:
            _ensure_dir(self._profile_path.parent)

            # Write to temp file first (atomic write)
            temp_path = self._profile_path.with_suffix('.json.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2)

            # Rename (atomic on most systems)
            temp_path.replace(self._profile_path)

    def add_note(self, content: str, category: str = "general", source: str = "agent") -> MemoryNote:
        """Add a timestamped note and persist to markdown file."""
        note = MemoryNote(
            timestamp=datetime.now().isoformat(),
            category=category,
            content=content,
            source=source
        )

        self.notes.append(note)

        # Also append to markdown file
        with self._lock:
            _ensure_dir(self._notes_path.parent)
            with open(self._notes_path, 'a', encoding='utf-8') as f:
                if self._notes_path.stat().st_size == 0 if self._notes_path.exists() else True:
                    f.write("# NAVIG User Memory Notes\n\n")
                f.write(note.to_markdown())

        return note

    def update(self, updates: Dict[str, Any], auto_save: bool = True) -> List[str]:
        """
        Update profile with new data.
        
        Args:
            updates: Dictionary of updates, e.g.:
                {
                    'identity.name': 'Alex',
                    'work_patterns.active_hours': ['9-17 EST'],
                    'technical_context.stack': ['Laravel', 'Docker']
                }
            auto_save: Whether to save after updating
        
        Returns:
            List of fields that were updated
        """
        updated_fields = []

        for key, value in updates.items():
            parts = key.split('.')

            if len(parts) == 1:
                # Top-level field (e.g., 'goals')
                if hasattr(self, key):
                    setattr(self, key, value)
                    updated_fields.append(key)

            elif len(parts) == 2:
                # Nested field (e.g., 'identity.name')
                section, field = parts
                if hasattr(self, section):
                    section_obj = getattr(self, section)
                    if hasattr(section_obj, field):
                        current_value = getattr(section_obj, field)
                        # For list fields, append instead of replace
                        if isinstance(current_value, list):
                            if isinstance(value, list):
                                # Extend with new unique values
                                for v in value:
                                    if v not in current_value:
                                        current_value.append(v)
                            else:
                                # Single value - append if not present
                                if value not in current_value:
                                    current_value.append(value)
                            setattr(section_obj, field, current_value)
                        else:
                            setattr(section_obj, field, value)
                        updated_fields.append(key)

        if auto_save and updated_fields:
            self.save()

        return updated_fields

    def record_session(self) -> None:
        """Record a new session (called on agent startup)."""
        self.stats.total_sessions += 1
        self.stats.last_active = datetime.now().isoformat()
        self.save()

    def record_command(self, command: str) -> None:
        """Record a command execution."""
        self.stats.record_command(command)
        # Don't save every command - batch saves happen periodically

    def search_memory(self, query: str, limit: int = 10) -> List[str]:
        """
        Search notes and profile for relevant context.
        Simple keyword search (can be upgraded to semantic later).
        """
        query_lower = query.lower()
        results = []

        # Search notes
        for note in reversed(self.notes):  # Most recent first
            if query_lower in note.content.lower() or query_lower in note.category.lower():
                results.append(f"[{note.timestamp[:10]}] {note.category}: {note.content}")
                if len(results) >= limit:
                    break

        # Search profile fields
        profile_dict = self.to_dict()
        for section, data in profile_dict.items():
            if section in ('schema_version', 'notes'):
                continue

            if isinstance(data, dict):
                for key, value in data.items():
                    if value and query_lower in str(value).lower():
                        results.append(f"Profile.{section}.{key}: {value}")
            elif isinstance(data, list):
                for item in data:
                    if query_lower in str(item).lower():
                        results.append(f"Profile.{section}: {item}")

        return results[:limit]

    def get_context_summary(self, max_length: int = 500) -> str:
        """
        Generate a compressed context summary for AI prompts.
        Prioritizes the most relevant/recent information.
        """
        parts = []

        # Identity (always include if available)
        if self.identity.name:
            parts.append(f"Human: {self.identity.name}")
        if self.identity.timezone:
            parts.append(f"Timezone: {self.identity.timezone}")
        if self.identity.role:
            parts.append(f"Role: {self.identity.role}")

        # Technical context
        if self.technical_context.stack:
            parts.append(f"Stack: {', '.join(self.technical_context.stack[:5])}")
        if self.technical_context.managed_hosts:
            parts.append(f"Hosts: {', '.join(self.technical_context.managed_hosts[:5])}")
        if self.technical_context.primary_projects:
            parts.append(f"Projects: {', '.join(self.technical_context.primary_projects[:3])}")

        # Work patterns
        if self.work_patterns.active_hours:
            parts.append(f"Active: {', '.join(self.work_patterns.active_hours[:2])}")
        if self.work_patterns.deploy_preferences:
            prefs = self.work_patterns.deploy_preferences
            if 'preferred_day' in prefs:
                parts.append(f"Deploys: {prefs['preferred_day']}")

        # Preferences
        if self.preferences.communication_style:
            parts.append(f"Style: {self.preferences.communication_style}")

        # Goals
        if self.goals:
            parts.append(f"Goals: {', '.join(self.goals[:3])}")

        # Recent notes (last 3)
        recent_notes = [n.content for n in self.notes[-3:]]
        if recent_notes:
            parts.append(f"Recent notes: {'; '.join(recent_notes)}")

        # Stats
        if self.stats.total_sessions > 1:
            parts.append(f"Sessions: {self.stats.total_sessions}")
        if self.stats.most_used_commands:
            top_cmds = sorted(self.stats.most_used_commands.items(), key=lambda x: x[1], reverse=True)[:3]
            parts.append(f"Frequent commands: {', '.join(c[0] for c in top_cmds)}")

        summary = " | ".join(parts)

        # Truncate if needed
        if len(summary) > max_length:
            summary = summary[:max_length-3] + "..."

        return summary

    def is_empty(self) -> bool:
        """Check if profile has any meaningful data."""
        return (
            not self.identity.name and
            not self.technical_context.stack and
            not self.goals and
            not self.notes and
            self.stats.total_sessions <= 1
        )

    def clear(self, confirm: bool = False) -> bool:
        """
        Clear all profile data.
        
        Args:
            confirm: Must be True to actually clear (safety check)
        
        Returns:
            True if cleared, False if not confirmed
        """
        if not confirm:
            return False

        # Backup before clearing
        if self._profile_path.exists():
            backup_path = self._profile_path.with_suffix(f'.json.{datetime.now().strftime("%Y%m%d_%H%M%S")}.bak')
            shutil.copy2(self._profile_path, backup_path)

        # Reset to empty profile
        self.identity = UserIdentity()
        self.work_patterns = WorkPatterns()
        self.technical_context = TechnicalContext()
        self.preferences = UserPreferences()
        self.stats = InteractionStats()
        self.stats.first_seen = datetime.now().isoformat()
        self.goals = []
        self.notes = []

        self.save()

        # Clear notes file
        if self._notes_path.exists():
            self._notes_path.unlink()

        return True

    def to_human_readable(self) -> str:
        """Format profile for human display."""
        lines = ["=== NAVIG User Profile ===\n"]

        # Identity
        lines.append("IDENTITY")
        if self.identity.name:
            lines.append(f"   Name: {self.identity.name}")
        if self.identity.role:
            lines.append(f"   Role: {self.identity.role}")
        if self.identity.timezone:
            lines.append(f"   Timezone: {self.identity.timezone}")
        if self.identity.location:
            lines.append(f"   Location: {self.identity.location}")
        if not any([self.identity.name, self.identity.role, self.identity.timezone]):
            lines.append("   (no identity data)")

        # Technical
        lines.append("\nTECHNICAL CONTEXT")
        if self.technical_context.stack:
            lines.append(f"   Stack: {', '.join(self.technical_context.stack)}")
        if self.technical_context.managed_hosts:
            lines.append(f"   Hosts: {', '.join(self.technical_context.managed_hosts)}")
        if self.technical_context.primary_projects:
            lines.append(f"   Projects: {', '.join(self.technical_context.primary_projects)}")
        if not any([self.technical_context.stack, self.technical_context.managed_hosts]):
            lines.append("   (no technical data)")

        # Work patterns
        lines.append("\nWORK PATTERNS")
        if self.work_patterns.active_hours:
            lines.append(f"   Active hours: {', '.join(self.work_patterns.active_hours)}")
        if self.work_patterns.deploy_preferences:
            lines.append(f"   Deploy prefs: {json.dumps(self.work_patterns.deploy_preferences)}")
        if self.work_patterns.common_tasks:
            lines.append(f"   Common tasks: {', '.join(self.work_patterns.common_tasks)}")
        if not any([self.work_patterns.active_hours, self.work_patterns.deploy_preferences]):
            lines.append("   (no work pattern data)")

        # Preferences
        lines.append("\nPREFERENCES")
        if self.preferences.communication_style:
            lines.append(f"   Communication: {self.preferences.communication_style}")
        if self.preferences.alert_thresholds:
            lines.append(f"   Alert thresholds: {json.dumps(self.preferences.alert_thresholds)}")
        if self.preferences.confirmation_required_for:
            lines.append(f"   Confirm for: {', '.join(self.preferences.confirmation_required_for)}")
        if not self.preferences.communication_style:
            lines.append("   (no preference data)")

        # Goals
        lines.append("\nGOALS")
        if self.goals:
            for goal in self.goals:
                lines.append(f"   - {goal}")
        else:
            lines.append("   (no goals set)")

        # Stats
        lines.append("\nSTATS")
        lines.append(f"   Total sessions: {self.stats.total_sessions}")
        lines.append(f"   Total commands: {self.stats.total_commands}")
        if self.stats.first_seen:
            lines.append(f"   First seen: {self.stats.first_seen[:10]}")
        if self.stats.last_active:
            lines.append(f"   Last active: {self.stats.last_active[:10]}")
        if self.stats.most_used_commands:
            top_cmds = sorted(self.stats.most_used_commands.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append(f"   Top commands: {', '.join(f'{c[0]}({c[1]})' for c in top_cmds)}")

        # Recent notes
        lines.append("\nRECENT NOTES")
        if self.notes:
            for note in self.notes[-5:]:
                lines.append(f"   [{note.timestamp[:10]}] {note.category}: {note.content[:60]}...")
        else:
            lines.append("   (no notes)")

        return "\n".join(lines)


# Singleton instance
_profile_instance: Optional[UserProfile] = None
_profile_lock = threading.Lock()


def get_profile() -> UserProfile:
    """Get the singleton profile instance."""
    global _profile_instance

    with _profile_lock:
        if _profile_instance is None:
            _profile_instance = UserProfile.load()
        return _profile_instance


def reload_profile() -> UserProfile:
    """Force reload the profile from disk."""
    global _profile_instance

    with _profile_lock:
        _profile_instance = UserProfile.load()
        return _profile_instance
