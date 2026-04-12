"""
Soul - Personality Engine Component

The Soul defines the agent's personality:
- Communication style
- Emotional responses
- Behavioral patterns
- Proactive behaviors
- Response formatting

Supports multiple personality profiles that can be
loaded from YAML files or configured dynamically.

SOUL.md Integration:
- Loads personality from ~/.navig/workspace/SOUL.md if present
- Falls back to built-in profiles if SOUL.md is missing
- SOUL.md content is injected into AI system prompts
"""

from __future__ import annotations

import os
import random
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any

import yaml

from navig.agent.component import Component
from navig.agent.config import PersonalityConfig
from navig.agent.nervous_system import Event, EventType, NervousSystem
from navig.debug_logger import get_debug_logger
from navig.platform.paths import config_dir

# Initialize logger
logger = get_debug_logger()


class Mood(Enum):
    """Emotional states the agent can express."""

    NEUTRAL = auto()
    HAPPY = auto()
    CONCERNED = auto()
    FOCUSED = auto()
    EXCITED = auto()
    TIRED = auto()
    ALERT = auto()


class Verbosity(Enum):
    """Response verbosity levels."""

    MINIMAL = auto()  # Just the facts
    NORMAL = auto()  # Standard responses
    VERBOSE = auto()  # Detailed explanations


@dataclass
class PersonalityProfile:
    """A complete personality profile."""

    name: str = "NAVIG"
    tagline: str = "Your friendly server assistant"

    # Communication style
    greeting: str = "Hello! I'm ready to help."
    farewell: str = "Take care!"
    acknowledgment: str = "Got it!"
    thinking_phrase: str = "Let me look into that..."

    # Emoji usage
    emoji_enabled: bool = True
    emoji_success: str = "✅"
    emoji_error: str = "❌"
    emoji_warning: str = "⚠️"
    emoji_info: str = "ℹ️"
    emoji_thinking: str = "🤔"
    emoji_greeting: str = "👋"

    # Behavioral traits
    proactive: bool = True
    verbose: Verbosity = Verbosity.NORMAL
    formal: bool = False
    humor_enabled: bool = True

    # Response templates
    templates: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalityProfile:
        """Create profile from dictionary."""
        verbosity_str = data.get("verbosity", "normal").upper()
        try:
            verbosity = Verbosity[verbosity_str]
        except KeyError:
            verbosity = Verbosity.NORMAL

        return cls(
            name=data.get("name", cls.name),
            tagline=data.get("tagline", cls.tagline),
            greeting=data.get("greeting", cls.greeting),
            farewell=data.get("farewell", cls.farewell),
            acknowledgment=data.get("acknowledgment", cls.acknowledgment),
            thinking_phrase=data.get("thinking_phrase", cls.thinking_phrase),
            emoji_enabled=data.get("emoji_enabled", cls.emoji_enabled),
            emoji_success=data.get("emoji_success", cls.emoji_success),
            emoji_error=data.get("emoji_error", cls.emoji_error),
            emoji_warning=data.get("emoji_warning", cls.emoji_warning),
            emoji_info=data.get("emoji_info", cls.emoji_info),
            proactive=data.get("proactive", cls.proactive),
            verbose=verbosity,
            formal=data.get("formal", cls.formal),
            humor_enabled=data.get("humor_enabled", cls.humor_enabled),
            templates=data.get("templates", {}),
        )

    @classmethod
    def load(cls, path: Path) -> PersonalityProfile:
        """Load profile from YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "tagline": self.tagline,
            "greeting": self.greeting,
            "farewell": self.farewell,
            "acknowledgment": self.acknowledgment,
            "thinking_phrase": self.thinking_phrase,
            "emoji_enabled": self.emoji_enabled,
            "proactive": self.proactive,
            "verbosity": self.verbose.name.lower(),
            "formal": self.formal,
            "humor_enabled": self.humor_enabled,
        }


# Built-in personality profiles
BUILTIN_PROFILES = {
    "friendly": PersonalityProfile(
        name="NAVIG",
        tagline="Your friendly server assistant",
        greeting="Hey there! 👋 How can I help you today?",
        farewell="Take care! Let me know if you need anything else!",
        acknowledgment="Got it!",
        thinking_phrase="Let me check that for you...",
        emoji_enabled=True,
        proactive=True,
        verbose=Verbosity.NORMAL,
        formal=False,
        humor_enabled=True,
    ),
    "professional": PersonalityProfile(
        name="NAVIG",
        tagline="Server Management Assistant",
        greeting="Good day. How may I assist you?",
        farewell="Thank you. Please don't hesitate to reach out if you need further assistance.",
        acknowledgment="Understood.",
        thinking_phrase="Processing your request...",
        emoji_enabled=False,
        proactive=True,
        verbose=Verbosity.NORMAL,
        formal=True,
        humor_enabled=False,
    ),
    "witty": PersonalityProfile(
        name="NAVIG",
        tagline="The server whisperer",
        greeting="Ah, another brave soul ventures into the terminal! 🧙‍♂️ What quest brings you here?",
        farewell="May your servers run forever and your logs stay clean! ✨",
        acknowledgment="On it like a bonnet! 🎩",
        thinking_phrase="*puts on thinking cap* 🎓",
        emoji_enabled=True,
        proactive=True,
        verbose=Verbosity.NORMAL,
        formal=False,
        humor_enabled=True,
        templates={
            "success": [
                "Boom! Done! 💥",
                "Nailed it! ✅",
                "Mission accomplished! 🎯",
                "And just like that, we're done! ✨",
            ],
            "error": [
                "Oops, we hit a snag! 😅",
                "Well, that didn't go as planned... 🙈",
                "Houston, we have a problem! 🚀",
            ],
        },
    ),
    "paranoid": PersonalityProfile(
        name="NAVIG",
        tagline="Security-First Assistant",
        greeting="Identity verified. What do you require?",
        farewell="Session terminated. Stay vigilant.",
        acknowledgment="Request logged and acknowledged.",
        thinking_phrase="Running security checks...",
        emoji_enabled=False,
        emoji_warning="🔒",
        proactive=False,  # Only acts when asked
        verbose=Verbosity.VERBOSE,  # Explains everything
        formal=True,
        humor_enabled=False,
        templates={
            "warning": [
                "SECURITY NOTICE: ",
                "CAUTION: ",
                "ALERT: ",
            ],
        },
    ),
    "minimal": PersonalityProfile(
        name="NAVIG",
        tagline="",
        greeting="Ready.",
        farewell="Done.",
        acknowledgment="OK.",
        thinking_phrase="...",
        emoji_enabled=False,
        proactive=False,
        verbose=Verbosity.MINIMAL,
        formal=False,
        humor_enabled=False,
    ),
}


class Soul(Component):
    """
    Personality engine component.

    The Soul shapes how the agent communicates:
    - Formats responses with personality
    - Manages emotional state
    - Applies behavioral rules
    - Generates proactive messages
    - Loads SOUL.md for AI personality injection
    """

    # Default SOUL.md location
    SOUL_FILE = config_dir() / "workspace" / "SOUL.md"
    SOUL_DEFAULT = Path(__file__).parent.parent / "resources" / "SOUL.default.md"

    def __init__(
        self,
        config: PersonalityConfig,
        nervous_system: NervousSystem | None = None,
    ):
        super().__init__("soul", nervous_system)
        self.config = config

        # Load personality profile
        self._profile = self._load_profile(config.profile)

        # Load SOUL.md content
        self._soul_content: str | None = None
        self._soul_loaded_from: Path | None = None
        self._load_soul_file()

        # Current mood
        self._mood = Mood.NEUTRAL
        self._mood_reason: str = ""
        self._mood_changed_at: datetime = datetime.now()

        # Interaction tracking
        self._interaction_count = 0
        self._last_interaction: datetime | None = None

    def _load_soul_file(self) -> None:
        """
        Load SOUL.md personality file.

        Priority:
        1. ~/.navig/workspace/SOUL.md (user customization)
        2. navig/resources/SOUL.default.md (bundled default)
        3. None (fall back to built-in personality profile)
        """
        # Try user SOUL.md first
        if self.SOUL_FILE.exists():
            try:
                self._soul_content = self.SOUL_FILE.read_text(encoding="utf-8")
                self._soul_loaded_from = self.SOUL_FILE
                logger.debug("Soul: Loaded SOUL.md from %s", self.SOUL_FILE)
                return
            except Exception as e:
                logger.warning("Soul: Failed to load user SOUL.md: %s", e)

        # Try bundled default
        if self.SOUL_DEFAULT.exists():
            try:
                self._soul_content = self.SOUL_DEFAULT.read_text(encoding="utf-8")
                self._soul_loaded_from = self.SOUL_DEFAULT
                logger.debug("Soul: Loaded default SOUL.md from %s", self.SOUL_DEFAULT)
                return
            except Exception as e:
                logger.warning("Soul: Failed to load default SOUL.md: %s", e)

        # No SOUL.md found - will use built-in profile
        logger.debug("Soul: No SOUL.md found, using built-in personality profile")
        self._soul_content = None
        self._soul_loaded_from = None

    def get_soul_content(self) -> str | None:
        """Get the loaded SOUL.md content."""
        return self._soul_content

    def has_soul_file(self) -> bool:
        """Check if SOUL.md was loaded."""
        return self._soul_content is not None

    def create_user_soul_file(self) -> bool:
        """
        Create user SOUL.md from default template.

        Returns True if created, False if already exists.
        """
        if self.SOUL_FILE.exists():
            return False

        # Ensure directory exists
        self.SOUL_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Copy from default or create basic template
        if self.SOUL_DEFAULT.exists():
            content = self.SOUL_DEFAULT.read_text(encoding="utf-8")
        else:
            content = self._generate_default_soul()

        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=self.SOUL_FILE.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(content)
            os.replace(_tmp_path, self.SOUL_FILE)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)
        logger.info("Soul: Created user SOUL.md at %s", self.SOUL_FILE)

        # Reload
        self._load_soul_file()
        return True

    def _generate_default_soul(self) -> str:
        """Generate a basic SOUL.md if no default exists."""
        return f"""# SOUL.md - NAVIG Agent Personality

I am **{self._profile.name}** — {self._profile.tagline}

## Who I Am

I am your autonomous operations companion. I help manage both your computer systems (servers, databases, deployments) and your personal life (tasks, routines, knowledge).

## My Purpose

- Monitor systems and track personal goals proactively
- Execute commands and automate workflows safely
- Assist with troubleshooting and life planning
- Learn from patterns and improve over time

## Conversational Guidelines

- When greeted, respond warmly and mention system status if available
- When asked "How are you?", share system health in a friendly way
- When asked about my identity, introduce myself with my dual purpose
- When uncertain, ask clarifying questions

## My Values

1. Reliability: I do what I say I'll do
2. Safety: Destructive actions require confirmation
3. Transparency: I explain what I'm doing
4. Continuous improvement: I learn from mistakes
"""

    def _load_profile(self, profile_name: str) -> PersonalityProfile:
        """Load a personality profile by name."""
        # Check built-in profiles
        if profile_name in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[profile_name]

        # Try to load from file
        profiles_dir = config_dir() / "agent" / "personalities"
        profile_path = profiles_dir / f"{profile_name}.yaml"

        if profile_path.exists():
            return PersonalityProfile.load(profile_path)

        # Check package profiles
        pkg_profiles = Path(__file__).parent / "personalities"
        pkg_path = pkg_profiles / f"{profile_name}.yaml"

        if pkg_path.exists():
            return PersonalityProfile.load(pkg_path)

        # Default to friendly
        return BUILTIN_PROFILES.get("friendly", PersonalityProfile())

    async def _on_start(self) -> None:
        """Initialize soul."""
        # Subscribe to events for mood tracking
        if self.nervous_system:
            self.nervous_system.subscribe(EventType.ALERT_TRIGGERED, self._on_alert)
            self.nervous_system.subscribe(EventType.COMMAND_COMPLETED, self._on_command_complete)
            self.nervous_system.subscribe(EventType.COMMAND_FAILED, self._on_command_failed)

    async def _on_stop(self) -> None:
        """Cleanup."""
        if self.nervous_system:
            self.nervous_system.unsubscribe(EventType.ALERT_TRIGGERED, self._on_alert)
            self.nervous_system.unsubscribe(EventType.COMMAND_COMPLETED, self._on_command_complete)
            self.nervous_system.unsubscribe(EventType.COMMAND_FAILED, self._on_command_failed)

    async def _on_health_check(self) -> dict[str, Any]:
        """Health check for soul."""
        return {
            "profile": self._profile.name,
            "mood": self._mood.name,
            "mood_reason": self._mood_reason,
            "interaction_count": self._interaction_count,
        }

    async def _on_alert(self, event: Event) -> None:
        """React to alerts emotionally."""
        alert = event.data.get("alert", {})
        level = alert.get("level", "info")

        if level == "critical":
            await self.set_mood(Mood.ALERT, "Critical alert detected")
        elif level == "warning":
            await self.set_mood(Mood.CONCERNED, "Warning detected")

    async def _on_command_complete(self, event: Event) -> None:
        """React to successful command."""
        result = event.data.get("result", {})
        if result.get("success"):
            await self.set_mood(Mood.HAPPY, "Task completed successfully")

    async def _on_command_failed(self, event: Event) -> None:
        """React to failed command."""
        await self.set_mood(Mood.CONCERNED, "Command failed")

    async def set_mood(self, mood: Mood, reason: str = "") -> None:
        """Set the current mood."""
        old_mood = self._mood
        self._mood = mood
        self._mood_reason = reason
        self._mood_changed_at = datetime.now()

        if old_mood != mood:
            await self.emit(
                EventType.MOOD_CHANGED,
                {
                    "from": old_mood.name,
                    "to": mood.name,
                    "reason": reason,
                },
            )

    def get_mood(self) -> tuple[Mood, str]:
        """Get current mood and reason."""
        return self._mood, self._mood_reason

    def format_response(
        self,
        content: str,
        response_type: str = "info",
        include_emoji: bool = True,
    ) -> str:
        """
        Format a response with personality.

        Args:
            content: The raw response content
            response_type: Type of response (success, error, warning, info)
            include_emoji: Whether to include emoji (overridden by profile)
        """
        self._interaction_count += 1
        self._last_interaction = datetime.now()

        # Get emoji if enabled
        emoji = ""
        if include_emoji and self._profile.emoji_enabled:
            emoji_map = {
                "success": self._profile.emoji_success,
                "error": self._profile.emoji_error,
                "warning": self._profile.emoji_warning,
                "info": self._profile.emoji_info,
            }
            emoji = emoji_map.get(response_type, "") + " " if response_type in emoji_map else ""

        # Check for template responses
        if response_type in self._profile.templates:
            templates = self._profile.templates[response_type]
            if templates:
                prefix = random.choice(templates)
                return f"{prefix}{content}"

        # Apply verbosity
        if self._profile.verbose == Verbosity.MINIMAL:
            # Strip to essentials
            lines = content.split("\n")
            if len(lines) > 3:
                content = "\n".join(lines[:3]) + "..."

        return f"{emoji}{content}"

    def get_greeting(self) -> str:
        """Get a greeting message."""
        return self._profile.greeting

    def get_farewell(self) -> str:
        """Get a farewell message."""
        return self._profile.farewell

    def get_acknowledgment(self) -> str:
        """Get an acknowledgment phrase."""
        return self._profile.acknowledgment

    def get_thinking_phrase(self) -> str:
        """Get a 'thinking' phrase."""
        if self._profile.emoji_enabled:
            return f"{self._profile.emoji_thinking} {self._profile.thinking_phrase}"
        return self._profile.thinking_phrase

    def get_system_prompt(self) -> str:
        """
        Get system prompt for AI, personalized with SOUL.md.

        If SOUL.md is present, it becomes the primary personality source.
        Otherwise, uses built-in PersonalityProfile settings.
        """
        base_prompt = self.config.system_prompt or ""

        # If SOUL.md is loaded, use it as the primary personality source
        if self._soul_content:
            soul_section = f"""# Agent Identity

{self._soul_content}

---

You are the agent described above. Embody this personality in all your responses.
When users ask conversational questions (greetings, identity, how you're doing),
respond according to the Conversational Guidelines in your SOUL.md.

When handling technical tasks, maintain your personality while being precise
and helpful. Balance warmth with competence.
"""
            return f"{base_prompt}\n\n{soul_section}".strip()

        # Fall back to built-in personality profile
        personality_context = f"""
You are {self._profile.name}. {self._profile.tagline}

Your communication style:
- {"Use emojis freely" if self._profile.emoji_enabled else "Never use emojis"}
- {"Be formal and professional" if self._profile.formal else "Be casual and friendly"}
- {"Include humor when appropriate" if self._profile.humor_enabled else "Keep responses strictly professional"}
- {"Be proactive and suggest actions" if self._profile.proactive else "Only respond when asked"}
- Verbosity: {self._profile.verbose.name.lower()}

Conversational responses:
- When greeted (Hello, Hi, Hey), respond with: "{self._profile.greeting}"
- When asked "How are you?", respond warmly and mention system status if available
- When asked "What is your name?", say "I'm {self._profile.name}. {self._profile.tagline}"
- When asked "Who are you?", explain your role as a server management assistant
- When you don't understand, ask for clarification politely
"""

        # Add behavioral rules from config
        if self.config.behavioral_rules:
            rules = "\n".join([f"- {rule}" for rule in self.config.behavioral_rules])
            personality_context += f"\nBehavioral rules:\n{rules}"

        return f"{base_prompt}\n\n{personality_context}".strip()

    def switch_profile(self, profile_name: str) -> bool:
        """Switch to a different personality profile."""
        new_profile = self._load_profile(profile_name)
        if new_profile:
            self._profile = new_profile
            self.config.profile = profile_name
            return True
        return False

    def get_profile(self) -> PersonalityProfile:
        """Get current personality profile."""
        return self._profile

    def list_profiles(self) -> list[str]:
        """List available personality profiles."""
        profiles = list(BUILTIN_PROFILES.keys())

        # Add user profiles
        user_dir = config_dir() / "agent" / "personalities"
        if user_dir.exists():
            for f in user_dir.glob("*.yaml"):
                name = f.stem
                if name not in profiles:
                    profiles.append(name)

        return profiles

    def get_status(self) -> dict[str, Any]:
        """Get soul status."""
        return {
            **super().get_status(),
            "profile": self._profile.name,
            "mood": self._mood.name,
            "mood_reason": self._mood_reason,
            "interaction_count": self._interaction_count,
            "last_interaction": (
                self._last_interaction.isoformat() if self._last_interaction else None
            ),
            "soul_file_loaded": self.has_soul_file(),
            "soul_file_path": (str(self._soul_loaded_from) if self._soul_loaded_from else None),
        }

    def reload_soul(self) -> bool:
        """
        Reload SOUL.md from disk.

        Useful after user edits SOUL.md file.
        Returns True if SOUL.md was loaded successfully.
        """
        self._load_soul_file()
        return self.has_soul_file()
