"""
NAVIG Telegram Notifications

Automatic notification system for:
- Alerts and warnings
- Daily briefings
- Morning routines
- Heartbeat status
- Proactive updates
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Priority levels for notifications."""
    LOW = 1       # Can wait, batch with others
    NORMAL = 2    # Send within reasonable time
    HIGH = 3      # Send soon
    CRITICAL = 4  # Send immediately


@dataclass
class Notification:
    """A notification to send."""
    type: str  # 'alert', 'briefing', 'routine', 'heartbeat', 'reminder'
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.NORMAL
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_telegram_message(self) -> str:
        """Format for Telegram."""
        emoji_map = {
            'alert': '🚨',
            'briefing': '📊',
            'routine': '☀️',
            'heartbeat': '💓',
            'reminder': '⏰',
        }
        
        priority_prefix = {
            NotificationPriority.CRITICAL: '🔴 ',
            NotificationPriority.HIGH: '🟡 ',
            NotificationPriority.NORMAL: '',
            NotificationPriority.LOW: '',
        }
        
        emoji = emoji_map.get(self.type, '📢')
        prefix = priority_prefix.get(self.priority, '')
        
        return f"{prefix}{emoji} **{self.title}**\n\n{self.message}"


@dataclass
class ScheduledTask:
    """A scheduled notification task."""
    name: str
    time: time  # Time of day to run
    func: Callable
    enabled: bool = True
    days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon-Sun
    last_run: Optional[datetime] = None


class ChannelNotifier(ABC):
    """Abstract base for notification channels.

    Every concrete channel (Telegram, Discord, email, push, etc.) must
    implement these four methods so that ``NotificationManager`` can
    orchestrate them uniformly.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the notification channel (polling, webhooks, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the channel."""

    @abstractmethod
    async def send(self, notification: "Notification") -> None:
        """Queue or send a single notification."""

    @abstractmethod
    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ) -> None:
        """Convenience: send an alert-type notification."""


class TelegramNotifier(ChannelNotifier):
    """
    Manages automatic Telegram notifications.
    
    Features:
    - Push alerts when issues detected
    - Daily briefings (morning/evening)
    - Morning routine prompts
    - Heartbeat status updates
    - Batched low-priority notifications
    - Proactive engagement (greetings, check-ins, feature discovery)
    """
    
    def __init__(
        self,
        telegram_channel,  # TelegramChannel instance
        chat_id: int,      # Where to send notifications
    ):
        self.channel = telegram_channel
        self.chat_id = chat_id
        
        # Notification queue
        self.queue: List[Notification] = []
        self._queue_lock = asyncio.Lock()
        
        # 30-second batching window for non-critical notifications
        self._batch_window_sec = 30
        self._batch_buffer: List[Notification] = []
        self._batch_timer: Optional[asyncio.Task] = None
        
        # Scheduled tasks
        self.scheduled_tasks: List[ScheduledTask] = []
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Proactive engagement coordinator
        self._engagement = None  # Lazy-loaded
        
        # Configure default schedules
        self._setup_default_schedules()
        
    def _setup_default_schedules(self):
        """Set up default scheduled notifications."""
        # Morning briefing at 7:00 AM
        self.scheduled_tasks.append(ScheduledTask(
            name="morning_briefing",
            time=time(7, 0),
            func=self._morning_briefing,
            days=[0, 1, 2, 3, 4],  # Weekdays
        ))
        
        # Evening summary at 6:00 PM
        self.scheduled_tasks.append(ScheduledTask(
            name="evening_summary",
            time=time(18, 0),
            func=self._evening_summary,
            days=[0, 1, 2, 3, 4],  # Weekdays
        ))
        
        # Heartbeat check every 30 minutes (handled separately)
        self.scheduled_tasks.append(ScheduledTask(
            name="heartbeat_check",
            time=time(0, 0),  # Runs on interval, not specific time
            func=self._heartbeat_check,
        ))
        
        # Proactive engagement tick (runs alongside heartbeat interval)
        self.scheduled_tasks.append(ScheduledTask(
            name="engagement_tick",
            time=time(0, 0),  # Runs on interval, not specific time
            func=self._engagement_tick,
        ))
        
    async def start(self):
        """Start the notification system."""
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Telegram notifier started")
        
    async def stop(self):
        """Stop the notification system."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
                
    async def _scheduler_loop(self):
        """Main scheduler loop."""
        heartbeat_interval = 30 * 60  # 30 minutes
        engagement_interval = 15 * 60  # 15 minutes (more frequent than heartbeat)
        last_heartbeat = datetime.now()
        last_engagement = datetime.now()
        
        while self._running:
            try:
                now = datetime.now()
                
                # Check scheduled tasks
                for task in self.scheduled_tasks:
                    if not task.enabled:
                        continue
                        
                    # Special handling for heartbeat
                    if task.name == "heartbeat_check":
                        if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
                            await self._run_task(task)
                            last_heartbeat = now
                        continue
                    
                    # Special handling for engagement tick
                    if task.name == "engagement_tick":
                        if (now - last_engagement).total_seconds() >= engagement_interval:
                            await self._run_task(task)
                            last_engagement = now
                        continue
                    
                    # Time-based tasks
                    if now.weekday() not in task.days:
                        continue
                        
                    # Check if it's time to run
                    task_time = now.replace(
                        hour=task.time.hour,
                        minute=task.time.minute,
                        second=0,
                        microsecond=0
                    )
                    
                    # Run if within 1 minute window and not already run today
                    if (abs((now - task_time).total_seconds()) < 60 and
                        (task.last_run is None or task.last_run.date() != now.date())):
                        await self._run_task(task)
                        task.last_run = now
                
                # Process notification queue
                await self._process_queue()
                
                # Sleep for 30 seconds
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)
                
    async def _run_task(self, task: ScheduledTask):
        """Run a scheduled task."""
        try:
            notification = await task.func()
            if notification:
                await self.send(notification)
        except Exception as e:
            logger.error(f"Task {task.name} failed: {e}")
            
    async def _process_queue(self):
        """Process pending notifications."""
        async with self._queue_lock:
            if not self.queue:
                return
                
            # Group by priority
            critical = [n for n in self.queue if n.priority == NotificationPriority.CRITICAL]
            high = [n for n in self.queue if n.priority == NotificationPriority.HIGH]
            normal = [n for n in self.queue if n.priority == NotificationPriority.NORMAL]
            low = [n for n in self.queue if n.priority == NotificationPriority.LOW]
            
            # Send critical immediately
            for n in critical:
                await self._send_notification(n)
                self.queue.remove(n)
                
            # Send high priority
            for n in high:
                await self._send_notification(n)
                self.queue.remove(n)
                
            # Batch low priority (send if more than 3 or older than 30 min)
            if len(low) >= 3 or (low and (datetime.now() - low[0].created_at).seconds > 1800):
                await self._send_batched(low)
                for n in low:
                    self.queue.remove(n)
                    
            # Send normal priority
            for n in normal:
                await self._send_notification(n)
                self.queue.remove(n)
                
    async def _send_notification(self, notification: Notification):
        """Send a single notification (with quiet-hours gating)."""
        try:
            # Quiet-hours / DND gating
            if self._should_suppress(notification):
                logger.debug("Suppressed notification (quiet hours/DND): %s", notification.title)
                return
            message = notification.to_telegram_message()
            await self.channel.send_message(self.chat_id, message)
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            
    async def _send_batched(self, notifications: List[Notification]):
        """Send batched notifications."""
        if not notifications:
            return
            
        lines = ["📬 **Batched Updates**\n"]
        for n in notifications:
            lines.append(f"• {n.title}")
            
        message = "\n".join(lines)
        try:
            await self.channel.send_message(self.chat_id, message)
        except Exception as e:
            logger.error(f"Failed to send batched notifications: {e}")

    def _should_suppress(self, notification: Notification) -> bool:
        """Check if notification should be held (quiet hours / DND mode)."""
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker
            tracker = get_user_state_tracker()
            return tracker.should_suppress_notification(notification.priority.value)
        except Exception:
            return False
            
    async def send(self, notification: Notification):
        """Queue a notification for sending with 30s batching window."""
        if notification.priority == NotificationPriority.CRITICAL:
            # Send critical notifications immediately
            await self._send_notification(notification)
        elif notification.priority == NotificationPriority.HIGH:
            # HIGH goes to main queue (processed on next scheduler tick)
            async with self._queue_lock:
                self.queue.append(notification)
        else:
            # NORMAL + LOW enter the 30s batching window
            self._batch_buffer.append(notification)
            if self._batch_timer is None or self._batch_timer.done():
                self._batch_timer = asyncio.create_task(self._flush_batch_after_delay())
                
    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH
    ):
        """Send an alert notification."""
        await self.send(Notification(
            type='alert',
            title=title,
            message=message,
            priority=priority,
        ))

    async def _flush_batch_after_delay(self):
        """Wait batch_window_sec then flush the buffer as a single message."""
        try:
            await asyncio.sleep(self._batch_window_sec)
        except asyncio.CancelledError:
            pass
        # Drain buffer
        batch = list(self._batch_buffer)
        self._batch_buffer.clear()
        if not batch:
            return
        if len(batch) == 1:
            await self._send_notification(batch[0])
        else:
            await self._send_batched(batch)
        
    # ========================================================================
    # Scheduled Task Implementations
    # ========================================================================
    
    async def _morning_briefing(self) -> Optional[Notification]:
        """Generate morning briefing."""
        now = datetime.now()

        lines = [
            f"Good morning! It's {now.strftime('%A, %B %d')}.",
            "",
            "**Today's Focus:**",
            "• Check your task list",
            "• Review pending alerts",
            "• Plan your top 3 priorities",
            "",
            "Have a productive day! 💪",
        ]

        return Notification(
            type='routine',
            title='Morning Briefing',
            message='\n'.join(lines),
            priority=NotificationPriority.NORMAL,
        )
        
    async def _evening_summary(self) -> Optional[Notification]:
        """Generate evening summary — NAVIG lore-flavored, day/hour-aware."""
        import random
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()   # 0 = Monday, 6 = Sunday
        day_name = now.strftime('%A')

        # --- Dynamic opening line ---
        if hour < 19:
            shift_label = "evening shift begins"
        elif hour < 22:
            shift_label = "graveyard watch is close"
        else:
            shift_label = "deep cycle approaching"

        day_context = {
            0: "Week 1 of 5 complete. Momentum counts.",
            1: "Tuesday through. Keep the streak.",
            2: "Midweek. The servers don't sleep — you can.",
            3: "Thursday hold. One more push before the weekend.",
            4: "Friday wind-down. Let the daemons run overnight.",
            5: "Saturday ops. Respect the craft, even on weekends.",
            6: "Sunday. Systems quiet. Mind should be too.",
        }.get(weekday, "Another day logged in the graveyard.")

        # --- Dynamic closing line ---
        closings = [
            "The graveyard is quiet. Keep it that way. 🪦",
            "No admin visible. Systems nominal. Sleep well. 🌑",
            "Daemons running. You're allowed to rest. 🫀",
            "The watch is handed off. Go dark. 🔦",
            "Servers alive. Admin invisible. Mission holding. 🕹️",
            "Stack's green. Logs can wait until morning. 📋",
        ]
        closing = random.choice(closings)

        lines = [
            f"📊 **{day_name} Evening — {shift_label.title()}**",
            "",
            f"_{day_context}_",
            "",
            "**Shutdown Checklist:**",
            "• Review what shipped today",
            "• Lock in tomorrow's top priority",
            "• Confirm backups completed",
            "• Close what can be closed",
            "",
            closing,
        ]

        return Notification(
            type='briefing',
            title=f'{day_name} Evening Summary',
            message='\n'.join(lines),
            priority=NotificationPriority.LOW,
        )
        
    async def _heartbeat_check(self) -> Optional[Notification]:
        """Lightweight in-process health check.

        The old implementation shelled out to ``navig agent heartbeat``
        which does NOT exist as a CLI subcommand, causing empty
        "System Alert" spam every 30 minutes.

        Now we do a simple connectivity sanity check in-process.
        The full AI-driven heartbeat is handled by HeartbeatRunner
        when the gateway runs in full mode.
        """
        try:
            import socket

            # Quick sanity: can we resolve DNS? (proxy for "is network up")
            socket.getaddrinfo("dns.google", 443, socket.AF_INET, socket.SOCK_STREAM)

            # All basic checks passed — no notification needed
            return None

        except socket.gaierror:
            return Notification(
                type="heartbeat",
                title="System Alert",
                message="DNS resolution failed — network may be down.",
                priority=NotificationPriority.HIGH,
            )
        except Exception as e:
            logger.error("Heartbeat check error: %s", e)
            return None
    
    async def _engagement_tick(self) -> Optional[Notification]:
        """
        Run proactive engagement evaluation.
        
        This is the bridge between the EngagementCoordinator and the
        Telegram notification system. On each tick, the coordinator
        evaluates whether a proactive message should be sent.
        """
        try:
            coordinator = self._get_engagement_coordinator()
            result = coordinator.engagement_tick()
            
            if result is None:
                return None
            
            # Map engagement actions to notification types
            type_map = {
                'greeting': 'routine',
                'checkin': 'routine',
                'capability_promo': 'reminder',
                'contextual_tip': 'reminder',
                'evening_wrapup': 'briefing',
                'feedback_ask': 'routine',
                'idle_nudge': 'routine',
                'celebration': 'routine',
                'heartbeat_report': 'heartbeat',
            }
            
            # Map engagement priority (1-10) to notification priority
            if result.priority >= 8:
                priority = NotificationPriority.HIGH
            elif result.priority >= 5:
                priority = NotificationPriority.NORMAL
            else:
                priority = NotificationPriority.LOW
            
            return Notification(
                type=type_map.get(result.action.value, 'routine'),
                title=f"NAVIG — {result.action.value.replace('_', ' ').title()}",
                message=result.message,
                priority=priority,
                metadata=result.metadata,
            )
        except Exception as e:
            logger.error(f"Engagement tick failed: {e}")
            return None
    
    def _get_engagement_coordinator(self):
        """Lazy-load the engagement coordinator."""
        if self._engagement is None:
            from navig.agent.proactive.engagement import EngagementCoordinator
            self._engagement = EngagementCoordinator()
        return self._engagement
    
    def record_user_interaction(
        self,
        message_type: str = "chat",
        command: Optional[str] = None,
    ):
        """
        Record a user interaction for engagement tracking.
        
        Call this from message handlers when the user sends a message
        to keep the engagement system's state tracker up to date.
        """
        try:
            coordinator = self._get_engagement_coordinator()
            coordinator.state.record_interaction(
                message_type=message_type,
                command=command,
            )
        except Exception as e:
            logger.debug(f"Failed to record interaction: {e}")


class NotificationManager:
    """
    Central manager for all notification channels.
    
    Integrates with:
    - Telegram
    - Discord (future)
    - Email (future)
    - Push notifications (future)
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.telegram: Optional[TelegramNotifier] = None
        self._channels: Dict[str, ChannelNotifier] = {}
        
    def configure_telegram(self, telegram_channel, chat_id: int):
        """Configure Telegram notifications."""
        self.telegram = TelegramNotifier(telegram_channel, chat_id)
        self._channels['telegram'] = self.telegram

    def configure_matrix(
        self,
        bot,
        room_id: str,
        *,
        priority_room_id: str | None = None,
    ):
        """Configure Matrix notifications.

        Parameters
        ----------
        bot : NavigMatrixBot
            An already-started (or about-to-start) bot instance.
        room_id : str
            Default room for notifications.
        priority_room_id : str | None
            Optional dedicated room for CRITICAL / HIGH alerts.
        """
        from navig.gateway.matrix_notifier import MatrixNotifier

        notifier = MatrixNotifier(
            bot, room_id, priority_room_id=priority_room_id,
        )
        self._channels["matrix"] = notifier
        
    async def start_all(self):
        """Start all notification channels."""
        for name, channel in self._channels.items():
            try:
                await channel.start()
                logger.info(f"Started {name} notifications")
            except Exception as e:
                logger.error(f"Failed to start {name}: {e}")
                
    async def stop_all(self):
        """Stop all notification channels."""
        for name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception:
                pass
                
    async def broadcast_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH
    ):
        """Broadcast alert to all channels."""
        for channel in self._channels.values():
            try:
                await channel.send_alert(title, message, priority)
            except Exception as e:
                logger.error(f"Failed to broadcast to channel: {e}")


def get_notification_manager() -> NotificationManager:
    """Get the global notification manager."""
    return NotificationManager()
