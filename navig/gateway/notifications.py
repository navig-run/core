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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Priority levels for notifications."""

    LOW = 1  # Can wait, batch with others
    NORMAL = 2  # Send within reasonable time
    HIGH = 3  # Send soon
    CRITICAL = 4  # Send immediately


@dataclass
class Notification:
    """A notification to send."""

    type: str  # 'alert', 'briefing', 'routine', 'heartbeat', 'reminder'
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.NORMAL
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Inline keyboard rows sent alongside the message (Telegram inline_keyboard format).
    keyboard: list[list[dict]] | None = field(default=None)
    # When True, ``message`` is already fully formatted — skip the title/emoji wrapper.
    raw_message: bool = False
    # Delivery bookkeeping for the queue drain (see TelegramNotifier._settle).
    # `compare=False` so requeueing never changes how a notification compares —
    # equality is about WHAT is being said, not how many times we have tried.
    delivery_attempts: int = field(default=0, compare=False, repr=False)

    def to_telegram_message(self) -> str:
        """Format for Telegram (parse_mode=HTML).

        The title is HTML-escaped and bolded; the body is rendered through the
        shared markdown→HTML converter so it is BOTH safe and rich:

        * **Safe** — special characters like ``<``/``&`` (routine in tracebacks,
          error dumps, and webhook payloads) can no longer trip Telegram's HTML
          parser. Previously they did, and the transport silently retried with the
          parse mode stripped, so the message arrived with the ``<b>`` tags showing
          literally and every bit of formatting gone.
        * **Rich** — markdown in the body (code blocks, links, expandable quotes,
          bold) now renders, consistently with the main reply path.

        ``raw_message=True`` bypasses this entirely — the caller owns the HTML.
        """
        if self.raw_message:
            # Message already carries its own header/HTML — emit as-is.
            return self.message

        from navig.gateway.channels.telegram_html import html_escape, md_to_html

        emoji_map = {
            "alert": "🚨",
            "briefing": "📊",
            "routine": "☀️",
            "heartbeat": "💓",
            "reminder": "⏰",
        }

        priority_prefix = {
            NotificationPriority.CRITICAL: "🔴 ",
            NotificationPriority.HIGH: "🟡 ",
            NotificationPriority.NORMAL: "",
            NotificationPriority.LOW: "",
        }

        emoji = emoji_map.get(self.type, "📢")
        prefix = priority_prefix.get(self.priority, "")

        header = (
            f"{prefix}{emoji} <b>{html_escape(self.title)}</b>"
            if self.title
            else f"{prefix}{emoji}".rstrip()
        )
        body = md_to_html(self.message) if self.message else ""
        return f"{header}\n\n{body}" if body else header


@dataclass
class ScheduledTask:
    """A scheduled notification task."""

    name: str
    time: time  # Time of day to run
    func: Callable
    enabled: bool = True
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon-Sun
    last_run: datetime | None = None


class ChannelNotifier(ABC):
    """Abstract base for notification channels.

    Every concrete channel (Telegram, Discord, email, push, etc.) must
    implement these four methods so that ``NotificationManager`` can
    orchestrate them uniformly.
    """

    # Delivery attempts a queued/batched notification gets before it is dropped
    # LOUDLY. Defined ONCE here because every channel needs the same budget and
    # both implementations independently drained their buffer before the send —
    # a private copy per channel is how the two drifted apart in the first place.
    _MAX_DELIVERY_ATTEMPTS = 3

    @abstractmethod
    async def start(self) -> None:
        """Start the notification channel (polling, webhooks, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the channel."""

    @abstractmethod
    async def send(self, notification: "Notification") -> bool:
        """Queue or send a single notification.

        Returns True if the notification was delivered (immediate send) or
        accepted for async delivery (queued/batched), False if the channel
        rejected an immediate send. Callers that don't care may ignore it.
        """

    @abstractmethod
    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ) -> bool:
        """Convenience: send an alert-type notification. Returns like send()."""


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

    # `_MAX_DELIVERY_ATTEMPTS` is inherited from ChannelNotifier. The drain runs
    # on the proactive scheduler tick (30s by default), so it is roughly a
    # one-minute window for a transient rejection to clear.

    # Grace for the final buffered flush during stop() before it is abandoned.
    _SHUTDOWN_DRAIN_SEC = 5.0

    def __init__(
        self,
        telegram_channel,  # TelegramChannel instance
        chat_id: int,  # Where to send notifications
    ):
        self.channel = telegram_channel
        self.chat_id = chat_id

        # Notification queue
        self.queue: list[Notification] = []
        self._queue_lock = asyncio.Lock()

        # 30-second batching window for non-critical notifications
        self._batch_window_sec = 30
        self._batch_buffer: list[Notification] = []
        self._batch_timer: asyncio.Task | None = None

        # Scheduled tasks
        self.scheduled_tasks: list[ScheduledTask] = []
        self._scheduler_task: asyncio.Task | None = None
        self._running = False

        # Proactive engagement coordinator
        self._engagement = None  # Lazy-loaded

        # Configure default schedules
        self._setup_default_schedules()

    def _setup_default_schedules(self):
        """Set up default scheduled notifications."""
        # Morning briefing at 7:00 AM
        self.scheduled_tasks.append(
            ScheduledTask(
                name="morning_briefing",
                time=time(7, 0),
                func=self._morning_briefing,
                days=[0, 1, 2, 3, 4],  # Weekdays
            )
        )

        # Evening summary at 6:00 PM
        self.scheduled_tasks.append(
            ScheduledTask(
                name="evening_summary",
                time=time(18, 0),
                func=self._evening_summary,
                days=[0, 1, 2, 3, 4],  # Weekdays
            )
        )

        # Heartbeat check every 30 minutes (handled separately)
        self.scheduled_tasks.append(
            ScheduledTask(
                name="heartbeat_check",
                time=time(0, 0),  # Runs on interval, not specific time
                func=self._heartbeat_check,
            )
        )

        # Proactive engagement tick (runs alongside heartbeat interval)
        self.scheduled_tasks.append(
            ScheduledTask(
                name="engagement_tick",
                time=time(0, 0),  # Runs on interval, not specific time
                func=self._engagement_tick,
            )
        )

    async def start(self):
        """Start the notification system.

        Idempotent. A second ``start()`` while the loop is alive would overwrite
        ``_scheduler_task`` and ORPHAN the running one — two loops draining one
        queue, and only the newer reachable by ``stop()``. The channel restart path
        now reuses this object across restarts, so re-entry is reachable.
        """
        if self._running and self._scheduler_task and not self._scheduler_task.done():
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="telegram-notifier-scheduler"
        )
        logger.info("Telegram notifier started")

    async def stop(self):
        """Stop the notification system.

        Both tasks are reaped. ``_batch_timer`` was never cancelled here, so a
        pending 30s flush outlived stop() and woke up to send on a channel that
        was already tearing down — and with delivery retries it can now arm a
        successor, which would chain past shutdown. Cancelling it also reaches
        the flush's own shutdown branch: it delivers what is buffered and then
        declines to schedule anything further.

        Bounded, because that last-gasp flush is a network call: a wedged send
        must not wedge shutdown.
        """
        self._running = False
        for task in (self._scheduler_task, self._batch_timer):
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=self._SHUTDOWN_DRAIN_SEC)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass  # cancelled or too slow to matter; expected during shutdown
        self._batch_timer = None

    async def _scheduler_loop(self):
        """Main scheduler loop."""
        from navig.config import get_config_manager
        from navig.store.runtime import get_runtime_store

        cm = get_config_manager()
        proactive_cfg = cm.global_config.get("proactive", {}) if cm.global_config else {}

        heartbeat_interval = int(proactive_cfg.get("heartbeat_interval_sec", 30 * 60))
        engagement_interval = int(proactive_cfg.get("engagement_tick_interval_sec", 15 * 60))
        scheduler_loop_interval = int(proactive_cfg.get("scheduler_loop_interval_sec", 30))

        store = get_runtime_store()
        now = datetime.now()

        heartbeat_cache = store.cache_get("sched:heartbeat_check:last_ts")
        engagement_cache = store.cache_get("sched:engagement_tick:last_ts")

        try:
            last_heartbeat = (
                datetime.fromisoformat(str(heartbeat_cache)) if heartbeat_cache else now
            )
        except Exception:
            last_heartbeat = now

        try:
            last_engagement = (
                datetime.fromisoformat(str(engagement_cache)) if engagement_cache else now
            )
        except Exception:
            last_engagement = now

        for task in self.scheduled_tasks:
            if task.name in ("heartbeat_check", "engagement_tick"):
                continue
            last_run_date = store.get_task_last_run(task.name)
            if last_run_date:
                try:
                    task.last_run = datetime.fromisoformat(last_run_date)
                except Exception:
                    task.last_run = None

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
                            store.cache_set(
                                "sched:heartbeat_check:last_ts",
                                now.isoformat(),
                                ttl_seconds=48 * 3600,
                            )
                        continue

                    # Special handling for engagement tick
                    if task.name == "engagement_tick":
                        if (now - last_engagement).total_seconds() >= engagement_interval:
                            await self._run_task(task)
                            last_engagement = now
                            store.cache_set(
                                "sched:engagement_tick:last_ts",
                                now.isoformat(),
                                ttl_seconds=48 * 3600,
                            )
                        continue

                    # Time-based tasks
                    if now.weekday() not in task.days:
                        continue

                    # Check if it's time to run
                    task_time = now.replace(
                        hour=task.time.hour,
                        minute=task.time.minute,
                        second=0,
                        microsecond=0,
                    )

                    # Run if within 1 minute window and not already run today
                    if abs((now - task_time).total_seconds()) < 60 and (
                        task.last_run is None or task.last_run.date() != now.date()
                    ):
                        await self._run_task(task)
                        task.last_run = now
                        store.set_task_last_run(task.name, now.strftime("%Y-%m-%d"))

                # Process notification queue
                await self._process_queue()

                await asyncio.sleep(scheduler_loop_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler error: %s", e)
                await asyncio.sleep(60)

    async def _run_task(self, task: ScheduledTask):
        """Run a scheduled task.

        ``send()`` returns True for anything queued or batched ("accepted"), so a
        False here means a CRITICAL notification was genuinely rejected — worth
        naming the task that produced it, since `last_run` is stamped either way
        and the task will not run again today.
        """
        try:
            notification = await task.func()
            if notification and not await self.send(notification):
                logger.error(
                    "Scheduled task %s produced a notification that was NOT delivered: %r",
                    task.name,
                    notification.title,
                )
        except Exception as e:
            logger.error("Task %s failed: %s", task.name, e)

    def _settle(self, notification: Notification, delivered: bool) -> None:
        """Drop *notification* from the queue, or keep it for another attempt.

        The queue is the delivery promise: ``send()`` returns True for a queued
        notification meaning "accepted for delivery", not "sent". Removing a row
        whose send was REJECTED turns that promise into a silent drop — and the
        rejections that matter here are the routine ones (a Telegram 429 when a
        burst of alerts goes out at once, a network blip), not permanent faults.
        Retrying on the next tick clears exactly those.

        Bounded, because a notification that can never be delivered (a malformed
        payload Telegram keeps refusing) must not wedge the queue forever — after
        ``_MAX_DELIVERY_ATTEMPTS`` it is dropped LOUDLY, which is the one outcome
        the old code produced for every failure on the first try.
        """
        if delivered:
            notification.delivery_attempts = 0
            self._discard(notification)
            return

        notification.delivery_attempts += 1
        if notification.delivery_attempts >= self._MAX_DELIVERY_ATTEMPTS:
            logger.error(
                "Notification DROPPED after %d failed delivery attempts: %r [%s, %s]",
                notification.delivery_attempts,
                notification.title,
                notification.type,
                notification.priority.name,
            )
            self._discard(notification)
        else:
            logger.warning(
                "Notification delivery failed (attempt %d/%d), requeued: %r [%s]",
                notification.delivery_attempts,
                self._MAX_DELIVERY_ATTEMPTS,
                notification.title,
                notification.type,
            )

    def _discard(self, notification: Notification) -> None:
        """Remove *notification* from the queue by identity.

        ``list.remove`` matches on equality, and two alerts raised in the same
        tick can compare equal (same type/title/message/priority) — which would
        drop the wrong row and leave this one queued forever. Identity is what we
        actually mean.
        """
        for i, queued in enumerate(self.queue):
            if queued is notification:
                del self.queue[i]
                return

    async def _process_queue(self):
        """Process pending notifications.

        Every branch settles each notification against its REAL delivery result:
        delivered (or intentionally suppressed) drops it, a rejection requeues it
        until the attempt budget runs out. ``_send_notification`` has always
        returned that result — the drain used to throw it away and remove the row
        regardless, so a single rejected HIGH alert was gone for good.
        """
        async with self._queue_lock:
            if not self.queue:
                return

            # Group by priority
            critical = [n for n in self.queue if n.priority == NotificationPriority.CRITICAL]
            high = [n for n in self.queue if n.priority == NotificationPriority.HIGH]
            low = [n for n in self.queue if n.priority == NotificationPriority.LOW]

            # Send critical immediately
            for n in critical:
                self._settle(n, await self._send_notification(n))

            # Send high priority
            for n in high:
                self._settle(n, await self._send_notification(n))

            # Batch low priority (send if more than 3 or older than 30 min).
            # `.total_seconds()` — NOT `.seconds`, which is the sub-day component
            # of the delta, so a LOW notification sitting for 24h+30s reported 30
            # seconds old and kept failing the age check it had long since passed.
            if low and (
                len(low) >= 3
                or (datetime.now() - low[0].created_at).total_seconds() > 1800
            ):
                delivered = await self._send_batched(low)
                for n in low:
                    self._settle(n, delivered)

            # NORMAL notifications are batched by send(); no direct queue branch.

    async def _send_notification(self, notification: Notification) -> bool:
        """Send a single notification (with quiet-hours gating).

        Returns True if delivered (or intentionally suppressed), False if the
        send was rejected or errored — so callers don't report a phantom success.
        """
        try:
            # Quiet-hours / DND gating
            if self._should_suppress(notification):
                logger.debug("Suppressed notification (quiet hours/DND): %s", notification.title)
                return True  # intentionally not sent — not a delivery failure
            message = notification.to_telegram_message()
            sent = await self.channel.send_message(
                self.chat_id,
                message,
                keyboard=notification.keyboard or None,
            )
            # send_message returns None on a REJECTED send WITHOUT raising (rate-limit,
            # API error, timeout) — the `except` below never sees it. A proactive alert
            # that silently fails to deliver is exactly the "healed at 3am, told nobody"
            # trap; at minimum surface it so it's diagnosable instead of looking sent.
            if sent is None:
                logger.warning(
                    "Notification NOT delivered — Telegram rejected the send: %r [%s]",
                    notification.title,
                    getattr(notification, "type", "?"),
                )
                return False
            return True
        except Exception as e:
            logger.error("Failed to send notification: %s", e)
            return False

    async def _send_batched(self, notifications: list[Notification]) -> bool:
        """Send batched notifications. Returns True when the batch was delivered.

        The result is the queue drain's settle signal — an empty batch is
        vacuously delivered, a rejected one keeps every row for another attempt.
        """
        if not notifications:
            return True

        from navig.gateway.channels.telegram_html import html_escape

        lines = ["📬 <b>Batched Updates</b>\n"]
        for n in notifications:
            # Escape titles — this message carries an HTML tag, so it is sent with
            # parse_mode=HTML; a raw "<"/"&" in a title would break the whole batch.
            lines.append(f"• {html_escape(n.title)}")

        message = "\n".join(lines)
        try:
            sent = await self.channel.send_message(self.chat_id, message)
            # None = Telegram rejected the send without raising (see _send_notification).
            if sent is None:
                logger.warning(
                    "Batched notifications NOT delivered — Telegram rejected the send (%d items)",
                    len(notifications),
                )
                return False
            return True
        except Exception as e:
            logger.error("Failed to send batched notifications: %s", e)
            return False

    def _should_suppress(self, notification: Notification) -> bool:
        """Check if notification should be held (quiet hours / DND mode)."""
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            tracker = get_user_state_tracker()
            return tracker.should_suppress_notification(notification.priority.value)
        except Exception:
            return False

    async def send(self, notification: Notification) -> bool:
        """Queue a notification for sending with 30s batching window.

        CRITICAL is sent immediately and returns its real delivery result; HIGH
        (queued) and NORMAL/LOW (batched) are delivered asynchronously, so they
        return True to mean "accepted for delivery" (a later reject is logged).
        """
        if notification.priority == NotificationPriority.CRITICAL:
            # Send critical notifications immediately — return the real result so
            # a rejected must-deliver alert isn't reported as sent.
            return await self._send_notification(notification)
        elif notification.priority == NotificationPriority.HIGH:
            # HIGH goes to main queue (processed on next scheduler tick)
            async with self._queue_lock:
                self.queue.append(notification)
            return True
        else:
            # NORMAL + LOW enter the 30s batching window
            self._batch_buffer.append(notification)
            if self._batch_timer is None or self._batch_timer.done():
                self._batch_timer = asyncio.create_task(self._flush_batch_after_delay())
            return True

    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ) -> bool:
        """Send an alert notification. Returns like send()."""
        return await self.send(
            Notification(
                type="alert",
                title=title,
                message=message,
                priority=priority,
            )
        )

    async def _flush_batch_after_delay(self):
        """Wait batch_window_sec then flush the buffer as a single message.

        NORMAL/LOW notifications never enter ``self.queue`` — they live in
        ``_batch_buffer`` and this is their only delivery path, so a rejected
        flush used to lose the whole batch with one log line. They are put back
        for another window instead, on the same bounded budget as the queue.
        """
        cancelled = False
        try:
            await asyncio.sleep(self._batch_window_sec)
        except asyncio.CancelledError:
            # Task cancelled; expected during shutdown. Still flush what is
            # buffered, but do NOT arm another timer — that would outlive stop().
            cancelled = True
        # Drain buffer
        batch = list(self._batch_buffer)
        self._batch_buffer.clear()
        if not batch:
            return
        if len(batch) == 1:
            delivered = await self._send_notification(batch[0])
        else:
            delivered = await self._send_batched(batch)
        if delivered:
            for n in batch:
                n.delivery_attempts = 0
            return
        self._requeue_batch(batch, rearm=not cancelled)

    def _requeue_batch(self, batch: list[Notification], *, rearm: bool = True) -> None:
        """Return a rejected batch to the buffer and re-arm the flush timer."""
        retry: list[Notification] = []
        for n in batch:
            n.delivery_attempts += 1
            if n.delivery_attempts >= self._MAX_DELIVERY_ATTEMPTS:
                logger.error(
                    "Notification DROPPED after %d failed delivery attempts: %r [%s, %s]",
                    n.delivery_attempts,
                    n.title,
                    n.type,
                    n.priority.name,
                )
            else:
                retry.append(n)
        if not retry:
            return
        logger.warning(
            "Batched delivery failed — %d notification(s) requeued for the next window",
            len(retry),
        )
        # Preserve arrival order: the retries are older than anything buffered
        # while the flush was in flight.
        self._batch_buffer[:0] = retry
        if not rearm:
            return
        # Arm UNCONDITIONALLY: `self._batch_timer` is the flush task currently
        # running this code, so it is neither None nor done() — the usual
        # "already armed?" check would decline to schedule and strand the retries
        # until some unrelated notification happened to arm a timer.
        self._batch_timer = asyncio.create_task(self._flush_batch_after_delay())

    # ========================================================================
    # Scheduled Task Implementations
    # ========================================================================

    async def _morning_briefing(self) -> Notification | None:
        """Generate morning briefing, surfacing last night's anchor if set."""
        now = datetime.now()

        # Pull yesterday's priority anchor logged via eve:plan_tomorrow
        anchor: str = ""
        try:
            from navig.agent.proactive.eve_log import get_yesterday
            anchor = (get_yesterday().get("priority") or "").strip()
        except Exception:
            pass

        lines = [
            f"☀️ <b>Good morning</b>  ·  <i>{now.strftime('%A, %B %d')}</i>",
            "",
        ]

        if anchor:
            from navig.gateway.channels.telegram_html import html_escape

            lines += [
                "📌 <b>Your anchor for today:</b>",
                f"<i>{html_escape(anchor)}</i>",  # user eve_log value → escape for HTML
                "",
            ]

        lines += [
            "<b>First moves:</b>",
            "• Check your task list",
            "• Review overnight alerts",
            "• Lock in your top 3",
            "",
            "<i>Ship something worthy. 🚀</i>",
        ]

        keyboard: list[list[dict]] | None = None
        if anchor:
            keyboard = [
                [
                    {"text": "✅ Still my anchor", "callback_data": "morn:anchor_ok"},
                    {"text": "🔄 Set a new one", "callback_data": "eve:plan_tomorrow"},
                ]
            ]

        return Notification(
            type="routine",
            title="Morning Briefing",
            message="\n".join(lines),
            priority=NotificationPriority.NORMAL,
            keyboard=keyboard,
            raw_message=True,
        )

    async def _evening_summary(self) -> Notification | None:
        """Evening summary — single-header, inline action buttons, no duplicate text."""
        import random

        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()  # 0 = Monday, 6 = Sunday
        day_name = now.strftime("%A")

        shift_label = (
            "Evening Shift"
            if hour < 19
            else "Graveyard Watch"
            if hour < 22
            else "Deep Cycle"
        )

        day_context: dict[int, str] = {
            0: "Week 1 of 5 complete. Momentum counts.",
            1: "Tuesday through. Keep the streak.",
            2: "Midweek. Servers don't sleep — you can.",
            3: "Thursday hold. One more push.",
            4: "Friday wind-down. Let the daemons run.",
            5: "Saturday ops. Respect the craft.",
            6: "Sunday. Systems quiet. Mind should be too.",
        }
        context_line = day_context.get(weekday, "Another day logged in the graveyard.")

        closings = [
            "Stack's green. Logs can wait until morning. 📋",
            "No admin visible. Systems nominal. 🌑",
            "Daemons running. You're allowed to rest. 🫀",
            "The watch is handed off. Go dark. 🔦",
            "Servers alive. Admin invisible. Mission holding. 🕹️",
            "The graveyard is quiet. Keep it that way. 🪦",
        ]
        closing = random.choice(closings)

        # Pull already-logged items so the briefing reflects reality.
        already_shipped: str = ""
        already_priority: str = ""
        try:
            from navig.agent.proactive.eve_log import get_today
            today = get_today()
            already_shipped = (today.get("shipped") or "").strip()
            already_priority = (today.get("priority") or "").strip()
        except Exception:
            pass

        from navig.gateway.channels.telegram_html import html_escape

        checklist_lines = ["<b>Close out the day:</b>"]
        if already_shipped:
            # user eve_log values → escape for HTML (this message is sent HTML-parsed)
            checklist_lines.append(f"✅ <i>Shipped: {html_escape(already_shipped)}</i>")
        else:
            checklist_lines.append("• Review what shipped")
        if already_priority:
            checklist_lines.append(f"📌 <i>Anchor: {html_escape(already_priority)}</i>")
        else:
            checklist_lines.append("• Lock in tomorrow's top priority")
        checklist_lines += [
            "• Confirm backups ran",
            "• Close what can be closed",
        ]

        message = "\n".join([
            f"🌙 <b>{day_name} Evening</b>  ·  <i>{shift_label}</i>",
            "",
            f"<i>{context_line}</i>",
            "",
            *checklist_lines,
            "",
            f"<i>{closing}</i>",
        ])

        # Keyboard: only show log/plan buttons when not yet captured
        row1 = []
        if not already_shipped:
            row1.append({"text": "✅ Log what shipped", "callback_data": "eve:log_shipped"})
        if not already_priority:
            row1.append({"text": "🎯 Set tomorrow", "callback_data": "eve:plan_tomorrow"})

        row2 = [
            {"text": "💾 Backup check", "callback_data": "eve:backup_check"},
            {"text": "🌑 Go dark", "callback_data": "eve:dnd_on"},
        ]

        keyboard = [r for r in [row1, row2] if r]

        return Notification(
            type="briefing",
            title=f"{day_name} Evening",
            message=message,
            priority=NotificationPriority.LOW,
            keyboard=keyboard,
            raw_message=True,
        )

    async def _heartbeat_check(self) -> Notification | None:
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

    async def _engagement_tick(self) -> Notification | None:
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
                "greeting": "routine",
                "checkin": "routine",
                "capability_promo": "reminder",
                "contextual_tip": "reminder",
                "evening_wrapup": "briefing",
                "feedback_ask": "routine",
                "idle_nudge": "routine",
                "celebration": "routine",
                "heartbeat_report": "heartbeat",
            }

            # Map engagement priority (1-10) to notification priority
            if result.priority >= 8:
                priority = NotificationPriority.HIGH
            elif result.priority >= 5:
                priority = NotificationPriority.NORMAL
            else:
                priority = NotificationPriority.LOW

            # The card's HEADER follows the global language too. It used to be
            # built by title-casing the action name, which is always English —
            # so a Russian body arrived under "NAVIG — Greeting".
            from navig.core import i18n

            action = result.action.value
            title_key = f"engagement.title.{action}"
            title = i18n.t(title_key)
            if title == title_key:  # no locale entry — fall back to the old shape
                title = f"NAVIG — {action.replace('_', ' ').title()}"

            # A nudge that ASKS something must ship the way to answer it.
            # These go out as one-way push Notifications: nothing anywhere records
            # a pending question, so a plain "yes" lands in the ordinary chat
            # handler, which has no idea anything was asked and answers "OK, how
            # can I help?" — measured on the operator's bot. It is also why 50
            # "Remediate health issues" approvals expired unanswered.
            #
            # `Notification.keyboard` already exists and the `slash:` callback
            # prefix already dispatches a bot command, so answering is a button
            # rather than a new intent-parsing layer. Only nudges that declare a
            # `suggested_command` get one; the rest stay plain statements.
            keyboard = None
            suggested = (result.metadata or {}).get("suggested_command")
            if suggested:
                # i18n.t takes **fields for formatting, not a default — a missing
                # key comes back AS the key, which is the fallback shape used for
                # the title above.
                label_key = "engagement.nudge_yes"
                label = i18n.t(label_key)
                if label == label_key:
                    label = "Yes, go ahead"
                keyboard = [[{
                    "text": label,
                    "callback_data": f"slash:{suggested}",
                }]]

            return Notification(
                type=type_map.get(action, "routine"),
                title=title,
                message=result.message,
                priority=priority,
                metadata=result.metadata,
                keyboard=keyboard,
            )
        except Exception as e:
            logger.error("Engagement tick failed: %s", e)
            return None

    def _get_engagement_coordinator(self):
        """Lazy-load the engagement coordinator."""
        if self._engagement is None:
            from navig.agent.proactive.engagement import get_engagement_coordinator

            self._engagement = get_engagement_coordinator()
        return self._engagement

    def record_user_interaction(
        self,
        message_type: str = "chat",
        command: str | None = None,
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
            logger.debug("Failed to record interaction: %s", e)


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
        self.telegram: TelegramNotifier | None = None
        self._channels: dict[str, ChannelNotifier] = {}

    def get_channel(self, name: str) -> "ChannelNotifier | None":
        """Public accessor for a configured channel (used by the notify router)."""
        return self._channels.get(name)

    def configure_telegram(self, telegram_channel, chat_id: int):
        """Configure Telegram notifications."""
        self.telegram = TelegramNotifier(telegram_channel, chat_id)
        self._channels["telegram"] = self.telegram

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
            bot,
            room_id,
            priority_room_id=priority_room_id,
        )
        self._channels["matrix"] = notifier

    async def start_all(self):
        """Start all notification channels."""
        for name, channel in self._channels.items():
            try:
                await channel.start()
                logger.info("Started %s notifications", name)
            except Exception as e:
                logger.error("Failed to start %s: %s", name, e)

    async def stop_all(self):
        """Stop all notification channels."""
        for _name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def broadcast_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ):
        """Broadcast alert to all channels."""
        for channel in self._channels.values():
            try:
                await channel.send_alert(title, message, priority)
            except Exception as e:
                logger.error("Failed to broadcast to channel: %s", e)


def get_notification_manager() -> NotificationManager:
    """Get the global notification manager."""
    return NotificationManager()
