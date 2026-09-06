"""
Proactive Engagement Coordinator

The brain of NAVIG's proactive engagement system. Decides WHEN and WHAT
proactive actions to take based on:
- Operator state (active, idle, away, deep work, etc.)
- Time of day / time since last interaction
- Cooldown timers (prevent spamming)
- Soul mood (personality-aware delivery)
- HEARTBEAT.md-driven task list

Architecture heartbeat-runner + cron-service:
- HEARTBEAT.md as an LLM-readable task list that the heartbeat
  feeds into the agent prompt on each tick — the LLM decides what to do.
- NAVIG adapts this: the EngagementCoordinator runs on the existing
  HeartbeatRunner interval, evaluates UserStateTracker + cooldowns, and
  enqueues SystemEvents or TelegramNotifier messages.

Integration:
- HeartbeatRunner calls engagement_tick() on each heartbeat cycle
- TelegramNotifier has new schedule entries for proactive messages
- NervousSystem events for PROACTIVE_GREETING, PROACTIVE_CHECKIN, etc.
- ProactiveEngine polling loop integrates calendar/email triggers
"""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from navig.agent.proactive.user_state import (
    OperatorState,
    TimeOfDay,
    UserStateTracker,
    get_user_state_tracker,
)
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class EngagementAction(Enum):
    """Types of proactive engagement actions."""

    GREETING = "greeting"  # Morning/return greeting
    CHECKIN = "checkin"  # "How's it going?" periodic
    CAPABILITY_PROMO = "capability_promo"  # Feature discovery nudge
    CONTEXTUAL_TIP = "contextual_tip"  # Tip based on recent usage
    EVENING_WRAPUP = "evening_wrapup"  # End-of-day summary offer
    FEEDBACK_ASK = "feedback_ask"  # Self-improvement dialogue
    IDLE_NUDGE = "idle_nudge"  # Gentle reminder after long idle
    CELEBRATION = "celebration"  # Milestone / streak congrats
    HEARTBEAT_REPORT = "heartbeat_report"  # System health summary


@dataclass
class EngagementConfig:
    """Configuration for proactive engagement behavior."""

    enabled: bool = True

    # Cooldown periods (hours) — minimum time between each action type
    greeting_cooldown_hours: float = 12.0
    checkin_cooldown_hours: float = 4.0
    capability_promo_cooldown_hours: float = 24.0
    contextual_tip_cooldown_hours: float = 8.0
    feedback_ask_cooldown_hours: float = 72.0
    idle_nudge_cooldown_hours: float = 6.0
    wrapup_cooldown_hours: float = 6.0
    celebration_cooldown_hours: float = 24.0

    # Time windows
    greeting_hours: tuple[int, int] = (7, 10)  # 7AM-10AM
    wrapup_hours: tuple[int, int] = (17, 20)  # 5PM-8PM
    quiet_hours: tuple[int, int] = (23, 7)  # 11PM-7AM: no proactive

    # Probability tuning (0.0 = never, 1.0 = always when eligible)
    checkin_probability: float = 0.3
    capability_promo_probability: float = 0.4
    contextual_tip_probability: float = 0.5
    feedback_ask_probability: float = 0.15
    idle_nudge_probability: float = 0.2

    # Engagement limits
    max_proactive_per_day: int = 5
    min_interactions_before_promo: int = 10  # Need baseline before promoting
    min_days_before_feedback: int = 3  # Need relationship before asking


@dataclass
class EngagementResult:
    """Result of an engagement evaluation."""

    action: EngagementAction
    message: str
    priority: int = 5  # 1-10, higher = more important
    metadata: dict[str, Any] = field(default_factory=dict)
    suppress: bool = False  # If True, was generated but suppressed


class EngagementCoordinator:
    """
    Coordinates proactive engagement decisions.

    Runs on each heartbeat tick and evaluates whether a proactive
    action should be taken. Uses probabilistic scheduling with
    cooldown enforcement to feel natural, not robotic.

    Key design principle: the proactive system should
    feel like a thoughtful colleague who notices things and offers
    help — not a notification firehose.
    """

    def __init__(
        self,
        state_tracker: UserStateTracker | None = None,
        config: EngagementConfig | None = None,
    ):
        self.state = state_tracker or UserStateTracker()
        self.config = config or EngagementConfig()

        # Track daily sends to enforce max_proactive_per_day
        self._daily_sends: list[float] = []
        self._daily_reset_date: str | None = None

        # Callbacks for delivering proactive messages
        self._delivery_callbacks: list[Callable[[EngagementResult], None]] = []

        # Capability promoter (lazy-loaded to avoid circular imports)
        self._capability_promoter = None

    def register_delivery_callback(self, callback: Callable[[EngagementResult], None]):
        """Register a callback that will be called when a proactive message is ready."""
        self._delivery_callbacks.append(callback)

    def engagement_tick(self) -> EngagementResult | None:
        """
        Main evaluation loop — called on each heartbeat tick.

        Evaluates all potential proactive actions, picks the highest
        priority eligible one, and returns it (or None if nothing to do).

        This is the equivalent of heartbeat-runner tick that
        feeds HEARTBEAT.md tasks into the agent prompt.
        """
        if not self.config.enabled:
            return None

        # Enforce quiet hours
        if self._is_quiet_hours():
            return None

        # Enforce daily limit
        self._prune_daily_sends()
        if len(self._daily_sends) >= self.config.max_proactive_per_day:
            logger.debug("Daily proactive limit reached, skipping tick")
            return None

        # Get current state
        operator_state = self.state.get_operator_state()
        time_of_day = self.state.get_time_of_day()

        # Don't interrupt deep work
        if operator_state == OperatorState.DEEP_WORK:
            logger.debug("Operator in deep work, skipping proactive")
            return None

        # Evaluate all candidate actions
        candidates: list[EngagementResult] = []

        # 1. Greeting (highest priority for returning users)
        greeting = self._evaluate_greeting(operator_state, time_of_day)
        if greeting:
            candidates.append(greeting)

        # 2. Evening wrap-up
        wrapup = self._evaluate_wrapup(operator_state, time_of_day)
        if wrapup:
            candidates.append(wrapup)

        # 3. Check-in
        checkin = self._evaluate_checkin(operator_state)
        if checkin:
            candidates.append(checkin)

        # 4. Capability promotion
        promo = self._evaluate_capability_promo(operator_state)
        if promo:
            candidates.append(promo)

        # 5. Contextual tip
        tip = self._evaluate_contextual_tip(operator_state)
        if tip:
            candidates.append(tip)

        # 6. Idle nudge
        nudge = self._evaluate_idle_nudge(operator_state)
        if nudge:
            candidates.append(nudge)

        # 7. Feedback ask (lowest frequency)
        feedback = self._evaluate_feedback_ask(operator_state)
        if feedback:
            candidates.append(feedback)

        # Pick highest priority non-suppressed candidate
        candidates = [c for c in candidates if not c.suppress]
        if not candidates:
            return None

        candidates.sort(key=lambda c: c.priority, reverse=True)
        winner = candidates[0]

        # Record and deliver
        self._record_send(winner)
        self._deliver(winner)

        logger.info(
            "Proactive engagement: %s (priority=%s, state=%s)",
            winner.action.value,
            winner.priority,
            operator_state.value,
        )
        return winner

    # ─── Action Evaluators ──────────────────────────────────────────

    def _evaluate_greeting(self, state: OperatorState, tod: TimeOfDay) -> EngagementResult | None:
        """
        Evaluate whether to send a greeting.

        Triggers:
        - First interaction after long gap (JUST_ARRIVED state)
        - Morning hours + no greeting today
        """
        hours_since = self.state.hours_since("greeting")

        # Always greet a returning user (with cooldown)
        if state == OperatorState.JUST_ARRIVED:
            if hours_since is None or hours_since >= self.config.greeting_cooldown_hours:
                msg = self._build_greeting(tod, returning=True)
                return EngagementResult(
                    action=EngagementAction.GREETING,
                    message=msg,
                    priority=9,
                    metadata={"trigger": "just_arrived", "tod": tod.value},
                )

        # Morning greeting
        if tod in (TimeOfDay.EARLY_MORNING, TimeOfDay.MORNING):
            if hours_since is None or hours_since >= self.config.greeting_cooldown_hours:
                if state in (OperatorState.ACTIVE, OperatorState.IDLE):
                    msg = self._build_greeting(tod, returning=False)
                    return EngagementResult(
                        action=EngagementAction.GREETING,
                        message=msg,
                        priority=8,
                        metadata={"trigger": "morning", "tod": tod.value},
                    )
        return None

    def _evaluate_wrapup(self, state: OperatorState, tod: TimeOfDay) -> EngagementResult | None:
        """Evaluate whether to offer an evening wrap-up."""
        hour = datetime.now().hour
        start, end = self.config.wrapup_hours

        if start <= hour < end and state in (
            OperatorState.ACTIVE,
            OperatorState.WINDING_DOWN,
        ):
            hours_since = self.state.hours_since("wrapup")
            if hours_since is None or hours_since >= self.config.wrapup_cooldown_hours:
                return EngagementResult(
                    action=EngagementAction.EVENING_WRAPUP,
                    message=self._build_wrapup(),
                    priority=6,
                    metadata={"trigger": "evening_hours"},
                )
        return None

    def _evaluate_checkin(self, state: OperatorState) -> EngagementResult | None:
        """Evaluate whether to do a periodic check-in."""
        hours_since = self.state.hours_since("checkin")
        if hours_since is None or hours_since >= self.config.checkin_cooldown_hours:
            if state == OperatorState.ACTIVE:
                if random.random() < self.config.checkin_probability:
                    return EngagementResult(
                        action=EngagementAction.CHECKIN,
                        message=self._build_checkin(),
                        priority=4,
                        metadata={"trigger": "periodic"},
                    )
        return None

    def _evaluate_capability_promo(self, state: OperatorState) -> EngagementResult | None:
        """Evaluate whether to promote an underused feature."""
        hours_since = self.state.hours_since("capability_promo")
        if hours_since is None or hours_since >= self.config.capability_promo_cooldown_hours:
            if self.state.stats.total_messages >= self.config.min_interactions_before_promo:
                if state in (OperatorState.ACTIVE, OperatorState.IDLE):
                    if random.random() < self.config.capability_promo_probability:
                        promo = self._get_capability_promoter()
                        msg, feature = promo.get_promotion(self.state)
                        if msg:
                            return EngagementResult(
                                action=EngagementAction.CAPABILITY_PROMO,
                                message=msg,
                                priority=5,
                                metadata={"feature": feature},
                            )
        return None

    def _evaluate_contextual_tip(self, state: OperatorState) -> EngagementResult | None:
        """Evaluate whether to offer a contextual usage tip."""
        hours_since = self.state.hours_since("capability_promo")  # same cooldown
        if hours_since is None or hours_since >= self.config.contextual_tip_cooldown_hours:
            if state == OperatorState.ACTIVE:
                frequent = self.state.get_frequent_commands(top_n=3)
                if frequent and random.random() < self.config.contextual_tip_probability:
                    cmd, count = frequent[0]
                    msg = self._build_contextual_tip(cmd, count)
                    if msg:
                        return EngagementResult(
                            action=EngagementAction.CONTEXTUAL_TIP,
                            message=msg,
                            priority=3,
                            metadata={"command": cmd, "count": count},
                        )
        return None

    def _evaluate_idle_nudge(self, state: OperatorState) -> EngagementResult | None:
        """Evaluate whether to gently nudge an idle user."""
        if state == OperatorState.IDLE:
            hours_idle = self.state.hours_since("last_interaction")
            hours_since_nudge = self.state.hours_since("idle_nudge")

            if hours_idle and hours_idle >= 0.5:  # 30+ min idle
                if (
                    hours_since_nudge is None
                    or hours_since_nudge >= self.config.idle_nudge_cooldown_hours
                ):
                    if random.random() < self.config.idle_nudge_probability:
                        return EngagementResult(
                            action=EngagementAction.IDLE_NUDGE,
                            message=self._build_idle_nudge(),
                            priority=2,
                            # The nudge ASKS something ("want me to run a check?"),
                            # so it has to carry the command that answers it.
                            # Delivery turns this into an inline button — see
                            # gateway/notifications._engagement_tick.
                            #
                            # Without it the message is a question nobody can
                            # answer: it ships as a plain push Notification with no
                            # pending state anywhere, so a reply of "yes" reaches
                            # the ordinary chat handler, which knows of no
                            # outstanding question and answers "OK, how can I
                            # help?". Measured on the operator's bot.
                            metadata={"idle_hours": hours_idle, "suggested_command": "status"},
                        )
        return None

    def _evaluate_feedback_ask(self, state: OperatorState) -> EngagementResult | None:
        """Evaluate whether to ask for self-improvement feedback."""
        hours_since = self.state.hours_since("feedback_ask")
        if hours_since is None or hours_since >= self.config.feedback_ask_cooldown_hours:
            # Need minimum relationship before asking
            first_seen = self.state.stats.first_seen
            if first_seen:
                days_known = (time.time() - first_seen) / 86400
                if days_known >= self.config.min_days_before_feedback:
                    if state == OperatorState.ACTIVE:
                        if random.random() < self.config.feedback_ask_probability:
                            return EngagementResult(
                                action=EngagementAction.FEEDBACK_ASK,
                                message=self._build_feedback_ask(),
                                priority=2,
                                metadata={"days_known": days_known},
                            )
        return None

    # ─── Message Builders ───────────────────────────────────────────

    #: Which locale pool each part of the day draws from. The English text now
    #: lives in navig/locales/*.json — it was hardcoded here, which is why an
    #: operator with user.language=Russian was greeted in English.
    _GREETING_POOLS = {
        TimeOfDay.EARLY_MORNING: "engagement.greeting.early_morning",
        TimeOfDay.MORNING: "engagement.greeting.morning",
        TimeOfDay.AFTERNOON: "engagement.greeting.afternoon",
        TimeOfDay.EVENING: "engagement.greeting.evening",
        TimeOfDay.NIGHT: "engagement.greeting.night",
        TimeOfDay.LATE_NIGHT: "engagement.greeting.late_night",
    }

    def _build_greeting(self, tod: TimeOfDay, returning: bool) -> str:
        """Build a greeting message, in the operator's global language."""
        from navig.core import i18n

        if returning:
            return i18n.pick("engagement.greeting.returning")
        key = self._GREETING_POOLS.get(tod, "engagement.greeting.default")
        return i18n.pick(key) or i18n.pick("engagement.greeting.default")

    def _build_wrapup(self) -> str:
        """Build an evening wrap-up message."""
        from navig.core import i18n

        # `{total}` is substituted AFTER the variant is chosen: only one of the
        # three mentions it, and pre-formatting the pool would break the others.
        chosen = i18n.pick("engagement.wrapup")
        try:
            return chosen.format(total=self.state.stats.total_commands)
        except (KeyError, IndexError, ValueError):
            return chosen

    def _build_checkin(self) -> str:
        """Build a periodic check-in message."""
        from navig.core import i18n

        return i18n.pick("engagement.checkin")

    #: Commands that have a usage tip. The text lives in the locale files.
    _TIP_COMMANDS = ("db", "run", "file", "host", "docker", "backup")

    def _build_contextual_tip(self, command: str, count: int) -> str | None:
        """Build a contextual usage tip, in the operator's global language."""
        if command not in self._TIP_COMMANDS:
            return None

        from navig.core import i18n

        key = f"engagement.tip.{command}"
        text = i18n.t(key, count=count)
        # `t` echoes the key back when nothing resolves — that means the locale
        # has no entry for this command, not that the tip is the string
        # "engagement.tip.db".
        return None if text == key else text

    def _build_idle_nudge(self) -> str:
        """Build a gentle idle nudge."""
        from navig.core import i18n

        return i18n.pick("engagement.idle_nudge")

    def _build_feedback_ask(self) -> str:
        """Build a self-improvement feedback request."""
        from navig.core import i18n

        return i18n.pick("engagement.feedback_ask")

    # ─── Internal Helpers ───────────────────────────────────────────

    def _is_quiet_hours(self) -> bool:
        """Check if we're in quiet hours (no proactive messages)."""
        hour = datetime.now().hour
        start, end = self.config.quiet_hours
        if start > end:  # Wraps midnight (e.g., 23-7)
            return hour >= start or hour < end
        return start <= hour < end

    def _prune_daily_sends(self):
        """Prune daily send tracker, resetting at midnight."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_sends = []
            self._daily_reset_date = today

    def _record_send(self, result: EngagementResult):
        """Record that a proactive message was sent."""
        self._daily_sends.append(time.time())

        # Map action to event type for state tracker
        event_map = {
            EngagementAction.GREETING: "greeting",
            EngagementAction.CHECKIN: "checkin",
            EngagementAction.EVENING_WRAPUP: "wrapup",
            EngagementAction.CAPABILITY_PROMO: "capability_promo",
            EngagementAction.CONTEXTUAL_TIP: "capability_promo",
            EngagementAction.FEEDBACK_ASK: "feedback_ask",
            EngagementAction.IDLE_NUDGE: "idle_nudge",
            EngagementAction.CELEBRATION: "checkin",
        }
        event_type = event_map.get(result.action, "checkin")
        self.state.record_proactive_event(event_type)

    def _deliver(self, result: EngagementResult):
        """Deliver a proactive message through registered callbacks."""
        for callback in self._delivery_callbacks:
            try:
                callback(result)
            except Exception as e:
                logger.warning("Engagement delivery callback failed: %s", e)

    def _get_capability_promoter(self):
        """Lazy-load the capability promoter to avoid circular imports."""
        if self._capability_promoter is None:
            from navig.agent.proactive.capability_promo import CapabilityPromoter

            self._capability_promoter = CapabilityPromoter()
        return self._capability_promoter


_COORDINATORS: dict[str, EngagementCoordinator] = {}


def get_engagement_coordinator(user_id: str = "default") -> EngagementCoordinator:
    """Get (or create) a process-level EngagementCoordinator for a user key."""
    coordinator = _COORDINATORS.get(user_id)
    if coordinator is None:
        coordinator = EngagementCoordinator(state_tracker=get_user_state_tracker())
        _COORDINATORS[user_id] = coordinator
    return coordinator
