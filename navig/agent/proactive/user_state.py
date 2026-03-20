"""
Proactive Engagement System — User State Tracker

Tracks operator state signals to enable context-aware proactive interactions:
- Activity patterns (last message, response times, active hours)
- Interaction sentiment (message tone, request complexity)
- Usage patterns (frequent commands, underused features, feature discovery)
- Temporal context (time of day, day of week, work patterns)

Inspired by OpenClaw's session-scoped context awareness and HEARTBEAT.md
operator health monitoring, adapted for NAVIG's persistent daemon model.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class OperatorState(Enum):
    """Current operator state inference."""
    UNKNOWN = "unknown"
    ACTIVE = "active"              # Interacting right now
    IDLE = "idle"                  # No recent activity (<2h)
    AWAY = "away"                  # No activity for hours
    DEEP_WORK = "deep_work"        # Long session, minimal chat
    JUST_ARRIVED = "just_arrived"  # First message after long gap
    WINDING_DOWN = "winding_down"  # Late hours, decreasing activity


class TimeOfDay(Enum):
    """Time-of-day classification."""
    EARLY_MORNING = "early_morning"  # 05:00-08:00
    MORNING = "morning"              # 08:00-12:00
    AFTERNOON = "afternoon"          # 12:00-17:00
    EVENING = "evening"              # 17:00-21:00
    NIGHT = "night"                  # 21:00-00:00
    LATE_NIGHT = "late_night"        # 00:00-05:00


@dataclass
class UserPreferences:
    """Per-user preferences persisted across sessions."""
    chat_mode: str = "work"           # work, deep-focus, planning, creative, relax, sleep
    verbosity: str = "normal"         # brief, normal, detailed
    voice_enabled: bool = False       # Whether voice responses are on
    quiet_hours_start: int = 23       # 24h format  (hour to START quiet)
    quiet_hours_end: int = 7          # 24h format  (hour to END quiet)
    autonomy_level: str = "balanced"  # cautious, balanced, autonomous
    notifications_enabled: bool = True

    # Valid values for each field
    _VALID_MODES = ("work", "deep-focus", "planning", "creative", "relax", "sleep")
    _VALID_VERBOSITY = ("brief", "normal", "detailed")
    _VALID_AUTONOMY = ("cautious", "balanced", "autonomous")


@dataclass
class InteractionRecord:
    """A single interaction event."""
    timestamp: float
    message_type: str  # 'command', 'chat', 'greeting', 'question'
    command: Optional[str] = None
    response_time_ms: Optional[float] = None
    sentiment: Optional[str] = None  # 'positive', 'neutral', 'frustrated'


@dataclass
class UsageStats:
    """Aggregated usage statistics."""
    total_messages: int = 0
    total_commands: int = 0
    command_counts: Dict[str, int] = field(default_factory=dict)
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    active_days: int = 0
    avg_session_length_min: float = 0.0
    peak_hours: List[int] = field(default_factory=list)
    features_used: Dict[str, int] = field(default_factory=dict)
    last_greeting: Optional[float] = None
    last_checkin: Optional[float] = None
    last_capability_promo: Optional[float] = None
    last_feedback_ask: Optional[float] = None
    feedback_responses: List[Dict[str, Any]] = field(default_factory=list)


class UserStateTracker:
    """
    Tracks and infers operator state for proactive engagement decisions.
    
    This is the sensory layer — it observes interaction patterns and maintains
    a live model of the user's current state, without initiating any actions.
    The EngagementCoordinator reads this state to decide what proactive
    actions to take.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or Path.home() / ".navig" / "engagement"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # In-memory state
        self._interactions: List[InteractionRecord] = []
        self._max_interactions = 500
        self.stats = UsageStats()
        self.preferences = UserPreferences()

        # Active hours configuration (24h format)
        self.active_hours_start = 8   # 8 AM
        self.active_hours_end = 23    # 11 PM
        self.timezone_offset = 0      # Hours from UTC

        # Load persisted state
        self._load_state()

    def record_interaction(
        self,
        message_type: str = "chat",
        command: Optional[str] = None,
        response_time_ms: Optional[float] = None,
        sentiment: Optional[str] = None,
    ):
        """Record a user interaction event."""
        now = time.time()

        record = InteractionRecord(
            timestamp=now,
            message_type=message_type,
            command=command,
            response_time_ms=response_time_ms,
            sentiment=sentiment,
        )
        self._interactions.append(record)

        # Trim old interactions
        if len(self._interactions) > self._max_interactions:
            self._interactions = self._interactions[-self._max_interactions:]

        # Update stats
        self.stats.total_messages += 1
        if not self.stats.first_seen:
            self.stats.first_seen = now
        self.stats.last_seen = now

        if message_type == "command" and command:
            self.stats.total_commands += 1
            self.stats.command_counts[command] = self.stats.command_counts.get(command, 0) + 1

            # Track feature usage (group by command category)
            feature = command.split()[0] if command else "unknown"
            self.stats.features_used[feature] = self.stats.features_used.get(feature, 0) + 1

        # Track peak hours
        hour = datetime.fromtimestamp(now).hour
        if hour not in self.stats.peak_hours:
            # Keep top 5 peak hours based on interaction count
            hour_counts: Dict[int, int] = {}
            for rec in self._interactions:
                h = datetime.fromtimestamp(rec.timestamp).hour
                hour_counts[h] = hour_counts.get(h, 0) + 1
            self.stats.peak_hours = sorted(
                hour_counts.keys(), key=lambda h: hour_counts[h], reverse=True
            )[:5]

        # Auto-save periodically (every 50 interactions)
        if self.stats.total_messages % 50 == 0:
            self._save_state()

    def record_proactive_event(self, event_type: str):
        """Record when a proactive event was sent (to prevent spamming)."""
        now = time.time()
        if event_type == "greeting":
            self.stats.last_greeting = now
        elif event_type == "checkin":
            self.stats.last_checkin = now
        elif event_type == "capability_promo":
            self.stats.last_capability_promo = now
        elif event_type == "feedback_ask":
            self.stats.last_feedback_ask = now
        self._save_state()

    def record_feedback(self, feedback: str, rating: Optional[int] = None):
        """Record user feedback from self-improvement dialogue."""
        self.stats.feedback_responses.append({
            "timestamp": time.time(),
            "feedback": feedback,
            "rating": rating,
        })
        # Keep last 20 feedback entries
        if len(self.stats.feedback_responses) > 20:
            self.stats.feedback_responses = self.stats.feedback_responses[-20:]
        self._save_state()

    def get_operator_state(self) -> OperatorState:
        """Infer current operator state from interaction patterns."""
        now = time.time()

        if not self.stats.last_seen:
            return OperatorState.UNKNOWN

        gap_seconds = now - self.stats.last_seen
        gap_minutes = gap_seconds / 60
        gap_hours = gap_seconds / 3600

        # Just arrived: first message after 2+ hour gap
        if gap_hours >= 2:
            return OperatorState.JUST_ARRIVED

        # Away: no activity for 1-2 hours
        if gap_hours >= 1:
            return OperatorState.AWAY

        # Check for deep work pattern: long session, few messages
        recent = [r for r in self._interactions if now - r.timestamp < 3600]
        if len(recent) >= 3:
            # Active for 1+ hour but low message rate = deep work
            time_span = now - recent[0].timestamp
            if time_span > 3600 and len(recent) < 10:
                return OperatorState.DEEP_WORK

        # Winding down: late hours + decreasing activity
        hour = datetime.fromtimestamp(now).hour
        if hour >= 21 or hour < 5:
            if gap_minutes > 15:
                return OperatorState.WINDING_DOWN

        # Idle: no activity for 15+ minutes
        if gap_minutes >= 15:
            return OperatorState.IDLE

        return OperatorState.ACTIVE

    def get_time_of_day(self) -> TimeOfDay:
        """Get current time-of-day classification."""
        hour = datetime.now().hour
        if 5 <= hour < 8:
            return TimeOfDay.EARLY_MORNING
        elif 8 <= hour < 12:
            return TimeOfDay.MORNING
        elif 12 <= hour < 17:
            return TimeOfDay.AFTERNOON
        elif 17 <= hour < 21:
            return TimeOfDay.EVENING
        elif 21 <= hour < 24:
            return TimeOfDay.NIGHT
        else:
            return TimeOfDay.LATE_NIGHT

    def is_within_active_hours(self) -> bool:
        """Check if current time is within configured active hours."""
        hour = datetime.now().hour
        return self.active_hours_start <= hour < self.active_hours_end

    def hours_since(self, event_type: str) -> Optional[float]:
        """Get hours since a specific proactive event type."""
        now = time.time()
        ts = None
        if event_type == "greeting":
            ts = self.stats.last_greeting
        elif event_type == "checkin":
            ts = self.stats.last_checkin
        elif event_type == "capability_promo":
            ts = self.stats.last_capability_promo
        elif event_type == "feedback_ask":
            ts = self.stats.last_feedback_ask
        elif event_type == "last_interaction":
            ts = self.stats.last_seen

        if ts is None:
            return None
        return (now - ts) / 3600

    def get_underused_features(self, known_features: List[str]) -> List[str]:
        """Identify features the user hasn't tried or rarely uses."""
        used = set(self.stats.features_used.keys())
        never_used = [f for f in known_features if f not in used]

        # Also find features used < 3 times
        rarely_used = [
            f for f, count in self.stats.features_used.items()
            if count < 3 and f in known_features
        ]

        return never_used + rarely_used

    def get_frequent_commands(self, top_n: int = 5) -> List[tuple]:
        """Get most frequently used commands."""
        sorted_cmds = sorted(
            self.stats.command_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_cmds[:top_n]

    # ── Preference management ──────────────────────────────────

    def set_preference(self, key: str, value: Any) -> bool:
        """Set a user preference. Returns True if value was valid and set."""
        if key == "chat_mode":
            if value not in UserPreferences._VALID_MODES:
                return False
            self.preferences.chat_mode = value
        elif key == "verbosity":
            if value not in UserPreferences._VALID_VERBOSITY:
                return False
            self.preferences.verbosity = value
        elif key == "voice_enabled":
            self.preferences.voice_enabled = bool(value)
        elif key == "quiet_hours_start":
            self.preferences.quiet_hours_start = int(value) % 24
        elif key == "quiet_hours_end":
            self.preferences.quiet_hours_end = int(value) % 24
        elif key == "autonomy_level":
            if value not in UserPreferences._VALID_AUTONOMY:
                return False
            self.preferences.autonomy_level = value
        elif key == "notifications_enabled":
            self.preferences.notifications_enabled = bool(value)
        else:
            return False
        self._save_state()
        return True

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a user preference by key."""
        return getattr(self.preferences, key, default)

    def is_quiet_hours(self) -> bool:
        """Check if current time falls within quiet hours."""
        hour = datetime.now().hour
        start = self.preferences.quiet_hours_start
        end = self.preferences.quiet_hours_end
        if start <= end:
            return start <= hour < end
        else:
            # Wraps midnight: e.g. 23..07
            return hour >= start or hour < end

    def is_do_not_disturb(self) -> bool:
        """True when mode is deep-focus or sleep, or notifications disabled."""
        if not self.preferences.notifications_enabled:
            return True
        return self.preferences.chat_mode in ("deep-focus", "sleep")

    def should_suppress_notification(self, priority: int = 2) -> bool:
        """
        Whether a notification with this priority level should be suppressed.
        Priority: 1=LOW, 2=NORMAL, 3=HIGH, 4=CRITICAL
        Returns True if notification should be held/suppressed.
        """
        # CRITICAL always goes through
        if priority >= 4:
            return False
        # DND blocks everything except CRITICAL
        if self.is_do_not_disturb():
            return True
        # Quiet hours block LOW and NORMAL
        if self.is_quiet_hours() and priority <= 2:
            return True
        return False

    def _load_state(self):
        """Load persisted state from disk."""
        state_file = self.state_dir / "user_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                stats = data.get("stats", {})
                self.stats = UsageStats(
                    total_messages=stats.get("total_messages", 0),
                    total_commands=stats.get("total_commands", 0),
                    command_counts=stats.get("command_counts", {}),
                    first_seen=stats.get("first_seen"),
                    last_seen=stats.get("last_seen"),
                    active_days=stats.get("active_days", 0),
                    avg_session_length_min=stats.get("avg_session_length_min", 0.0),
                    peak_hours=stats.get("peak_hours", []),
                    features_used=stats.get("features_used", {}),
                    last_greeting=stats.get("last_greeting"),
                    last_checkin=stats.get("last_checkin"),
                    last_capability_promo=stats.get("last_capability_promo"),
                    last_feedback_ask=stats.get("last_feedback_ask"),
                    feedback_responses=stats.get("feedback_responses", []),
                )
                # Load active hours config
                config = data.get("config", {})
                self.active_hours_start = config.get("active_hours_start", 8)
                self.active_hours_end = config.get("active_hours_end", 23)

                # Load preferences
                prefs = data.get("preferences", {})
                if prefs:
                    self.preferences = UserPreferences(
                        chat_mode=prefs.get("chat_mode", "work"),
                        verbosity=prefs.get("verbosity", "normal"),
                        voice_enabled=prefs.get("voice_enabled", False),
                        quiet_hours_start=prefs.get("quiet_hours_start", 23),
                        quiet_hours_end=prefs.get("quiet_hours_end", 7),
                        autonomy_level=prefs.get("autonomy_level", "balanced"),
                        notifications_enabled=prefs.get("notifications_enabled", True),
                    )
            except Exception as e:
                logger.warning(f"Failed to load user state: {e}")

    def _save_state(self):
        """Persist state to disk."""
        state_file = self.state_dir / "user_state.json"
        try:
            data = {
                "stats": {
                    "total_messages": self.stats.total_messages,
                    "total_commands": self.stats.total_commands,
                    "command_counts": self.stats.command_counts,
                    "first_seen": self.stats.first_seen,
                    "last_seen": self.stats.last_seen,
                    "active_days": self.stats.active_days,
                    "avg_session_length_min": self.stats.avg_session_length_min,
                    "peak_hours": self.stats.peak_hours,
                    "features_used": self.stats.features_used,
                    "last_greeting": self.stats.last_greeting,
                    "last_checkin": self.stats.last_checkin,
                    "last_capability_promo": self.stats.last_capability_promo,
                    "last_feedback_ask": self.stats.last_feedback_ask,
                    "feedback_responses": self.stats.feedback_responses,
                },
                "config": {
                    "active_hours_start": self.active_hours_start,
                    "active_hours_end": self.active_hours_end,
                },
                "preferences": {
                    "chat_mode": self.preferences.chat_mode,
                    "verbosity": self.preferences.verbosity,
                    "voice_enabled": self.preferences.voice_enabled,
                    "quiet_hours_start": self.preferences.quiet_hours_start,
                    "quiet_hours_end": self.preferences.quiet_hours_end,
                    "autonomy_level": self.preferences.autonomy_level,
                    "notifications_enabled": self.preferences.notifications_enabled,
                },
                "updated_at": datetime.now().isoformat(),
            }
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save user state: {e}")


# ── Singleton accessor ────────────────────────────────────────

_tracker_instance: Optional[UserStateTracker] = None


def get_user_state_tracker() -> UserStateTracker:
    """Get the global UserStateTracker singleton."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = UserStateTracker()
    return _tracker_instance
