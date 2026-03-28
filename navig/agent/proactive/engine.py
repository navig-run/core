"""
Proactive Assistance Engine

Orchestrates proactive checks (calendar, email, monitoring) and
proposes actions based on context.

Inspired by proactive assistance loop patterns.
"""

import asyncio
from datetime import datetime, timedelta

from navig import console_helper as ch
from navig.agent.proactive.providers import (
    CalendarProvider,
    EmailProvider,
    MockCalendar,
    MockEmail,
)
from navig.core.hooks import HookEvent, register_hook, trigger_hook


class ProactiveEngine:
    """
    Engine for proactive assistance.

    Responsibilities:
    1. Polling registered providers (Calendar, Email, etc.)
    2. Analyzing context (schedule, unread messages)
    3. Generating 'Proactive Events' (reminders, drafts)
    4. Running engagement evaluation ticks (greetings, check-ins, feature discovery)
    """

    def __init__(self):
        self.calendar: CalendarProvider | None = MockCalendar()
        self.email: EmailProvider | None = MockEmail()
        self.email_providers: dict = {}  # label -> EmailProvider (multi-account)
        self.running = False

        # Initialize TriggerManager
        from navig.commands.triggers import TriggerManager

        self.trigger_manager = TriggerManager()

        # State tracking
        self.last_check: datetime | None = None
        self.last_check_status: str = "never"  # never, success, error
        self.last_error: str | None = None
        self.is_checking: bool = False
        self.provider_status: dict = {"calendar": "mock", "email": "mock"}

        # Engagement coordinator (lazy-loaded)
        self._engagement = None
        self._engagement_tick_counter = 0
        # Strong references to background tasks to prevent silent GC before completion.
        self._background_tasks: set = set()

    def set_calendar_provider(self, provider: CalendarProvider):
        """Set real calendar provider (e.g., GoogleCalendar)."""
        self.calendar = provider

    def set_email_provider(self, provider: EmailProvider):
        """Set real email provider."""
        self.email = provider

    async def start(self):
        """Start the proactive loop."""
        self.running = True
        ch.info("[Proactive] Engine started. Polling every 60s...")

        # Register core hooks
        register_hook("proactive:check", self.run_checks)

        while self.running:
            await self.run_checks(None)
            await asyncio.sleep(60)

    async def stop(self):
        """Stop the proactive loop."""
        self.running = False
        ch.info("[Proactive] Engine stopped.")

    async def run_checks(self, event: HookEvent | None):
        """Run all proactive checks."""
        if self.is_checking:
            return

        self.is_checking = True
        self.last_check = datetime.now()
        self.last_error = None

        try:
            from navig.commands.triggers import TriggerEvent, TriggerType

            # Check Calendar
            if self.calendar and not isinstance(self.calendar, MockCalendar):
                self.provider_status["calendar"] = "checking"
                try:
                    now = datetime.now()
                    events = await self.calendar.list_events(
                        now, now + timedelta(hours=2)
                    )
                    self.provider_status["calendar"] = "ok"
                    if events:
                        for evt in events:
                            # 1. Fire hook (for logging/other listeners)
                            await trigger_hook(
                                "proactive:alert",
                                action="notify",
                                context={"source": "calendar", "event": evt.title},
                                messages=[
                                    f"Upcoming event: {evt.title} at {evt.start.strftime('%H:%M')}"
                                ],
                            )

                            # 2. Fire TriggerManager event (for configured automation)
                            try:
                                # TriggerEvent requires type, source, data
                                te = TriggerEvent(
                                    type=TriggerType.CALENDAR,
                                    source="proactive_engine",
                                    data={
                                        "title": evt.title,
                                        "start": evt.start.isoformat(),
                                        "end": evt.end.isoformat(),
                                        "location": evt.location,
                                        "attendees": evt.attendees or [],
                                    },
                                )
                                self.trigger_manager.process_event(te)
                            except Exception as e:
                                ch.warning(f"Error processing calendar trigger: {e}")
                except Exception as e:
                    self.provider_status["calendar"] = f"error: {str(e)}"
                    ch.warning(f"Error checking calendar: {e}")
            else:
                self.provider_status["calendar"] = "mock"

            # Check Email (multi-account)
            email_sources = {}
            if self.email_providers:
                email_sources = self.email_providers
            elif self.email and not isinstance(self.email, MockEmail):
                email_sources = {"default": self.email}

            for label, email_prov in email_sources.items():
                prov_key = f"email:{label}"
                self.provider_status[prov_key] = "checking"
                try:
                    messages = await email_prov.list_unread(limit=5)
                    self.provider_status[prov_key] = "ok"
                    if messages:
                        for msg in messages:
                            if msg.read:
                                continue

                            # 1. Fire hook
                            await trigger_hook(
                                "proactive:alert",
                                action="notify",
                                context={
                                    "source": "email",
                                    "account": label,
                                    "subject": msg.subject,
                                },
                                messages=[
                                    f"[{label}] Unread email from {msg.sender}: {msg.subject}"
                                ],
                            )

                            # 2. Fire TriggerManager event
                            try:
                                te = TriggerEvent(
                                    type=TriggerType.EMAIL,
                                    source="proactive_engine",
                                    data={
                                        "subject": msg.subject,
                                        "sender": msg.sender,
                                        "snippet": msg.snippet,
                                        "received_at": msg.received_at.isoformat(),
                                        "id": msg.id,
                                        "account": label,
                                    },
                                )
                                self.trigger_manager.process_event(te)
                            except Exception as e:
                                ch.warning(f"Error processing email trigger: {e}")
                except Exception as e:
                    self.provider_status[prov_key] = f"error: {str(e)}"
                    ch.warning(f"Error checking email ({label}): {e}")

            if not email_sources:
                self.provider_status["email"] = "mock"

            self.last_check_status = "success"

        except Exception as e:
            self.last_check_status = "error"
            self.last_error = str(e)
            ch.error(f"Proactive check failed: {e}")
        finally:
            self.is_checking = False

        # Run engagement tick (every 15th polling cycle ≈ 15 min at 60s interval)
        self._engagement_tick_counter += 1
        if self._engagement_tick_counter >= 15:
            self._engagement_tick_counter = 0
            self._run_engagement_tick()

    def _run_engagement_tick(self):
        """Run a proactive engagement evaluation."""
        try:
            coordinator = self._get_engagement_coordinator()
            result = coordinator.engagement_tick()
            if result:
                # Schedule as a tracked task so it cannot be silently GC'd.
                import asyncio

                loop = asyncio.get_running_loop()
                task = loop.create_task(
                    trigger_hook(
                        "proactive:engagement",
                        action=result.action.value,
                        context=result.metadata,
                        messages=[result.message],
                    )
                )
                self._background_tasks.add(task)

                def _on_done(t: asyncio.Task) -> None:
                    self._background_tasks.discard(t)
                    if not t.cancelled() and t.exception():
                        ch.warning(f"Engagement hook failed: {t.exception()}")

                task.add_done_callback(_on_done)
        except Exception as e:
            ch.warning(f"Engagement tick error: {e}")

    def _get_engagement_coordinator(self):
        """Lazy-load the engagement coordinator."""
        if self._engagement is None:
            from navig.agent.proactive.engagement import EngagementCoordinator

            self._engagement = EngagementCoordinator()
        return self._engagement

    def record_user_message(
        self, message_type: str = "chat", command: str | None = None
    ):
        """
        Record a user interaction for engagement tracking.
        Call from message handlers to keep the state tracker updated.
        """
        try:
            coordinator = self._get_engagement_coordinator()
            coordinator.state.record_interaction(
                message_type=message_type, command=command
            )
        except Exception:
            pass  # Non-critical

    def init_providers(self):
        """Initialize providers from configuration."""
        import os

        from navig.agent.proactive.google_calendar import GoogleCalendar
        from navig.agent.proactive.imap_email import get_email_provider
        from navig.config import get_config_manager

        cm = get_config_manager()

        # Calendar
        cal_conf = cm.global_config.get("calendar", {})
        provider_name = cal_conf.get("provider")
        if provider_name == "google":
            try:
                self.calendar = GoogleCalendar()
            except Exception as e:
                ch.warning(f"Failed to init Google Calendar: {e}")

        # Email (single provider from global config)
        email_conf = cm.global_config.get("email", {})
        email_provider_name = email_conf.get("provider")
        if email_provider_name:
            addr = email_conf.get("address") or os.environ.get("NAVIG_EMAIL_ADDRESS")
            pwd = None
            provider_key = str(email_provider_name).strip().lower()

            # AUDIT DECISION:
            # Correct: vault-first keeps secrets out of flat config.
            # Non-breaking: env and legacy config passwords still resolve as fallback.
            # Simpler alternatives would keep relying on plaintext secrets.
            try:
                if provider_key:
                    from navig.vault import get_vault

                    secret = get_vault().get_secret(
                        provider_key,
                        key="password",
                        caller="proactive.engine",
                    )
                    if secret:
                        pwd = secret.reveal()
            except Exception:
                # Vault lookup failure should not stop legacy/env fallback.
                pwd = None

            if not pwd:
                pwd = os.environ.get("NAVIG_EMAIL_PASSWORD")
            if not pwd:
                pwd = email_conf.get("password")
                if pwd:
                    ch.warning(
                        "Using legacy plaintext email password from config. "
                        "Run 'navig email setup <provider>' to migrate to vault."
                    )

            if addr and pwd:
                try:
                    self.email = get_email_provider(
                        email_provider_name,
                        addr,
                        pwd,
                        host=email_conf.get("host"),
                        port=email_conf.get("port"),
                    )
                except Exception as e:
                    ch.warning(f"Failed to init Email Provider: {e}")

        # Multi-account email from agent config
        try:
            from navig.agent.config import AgentConfig

            agent_cfg = AgentConfig.load()
            for acct in agent_cfg.ears.email_accounts:
                if not acct.enabled:
                    continue
                addr = acct.address
                pwd = acct.password
                if not addr or not pwd:
                    continue
                # Resolve env vars in password
                if pwd.startswith("${") and pwd.endswith("}"):
                    pwd = os.environ.get(pwd[2:-1], "")
                if not pwd:
                    ch.warning(f"Email password not set for {acct.label or addr}")
                    continue
                try:
                    provider = get_email_provider(
                        acct.provider,
                        addr,
                        pwd,
                        host=acct.imap_host,
                        port=acct.imap_port,
                    )
                    label = acct.label or addr
                    self.email_providers[label] = provider
                    # Set first configured account as default single provider
                    if isinstance(self.email, MockEmail):
                        self.email = provider
                except Exception as e:
                    ch.warning(f"Failed to init email for {acct.label or addr}: {e}")
        except Exception:
            pass  # Agent config not available


# Singleton instance
_engine = ProactiveEngine()
_initialized = False


def get_proactive_engine() -> ProactiveEngine:
    global _initialized
    if not _initialized:
        _engine.init_providers()
        _initialized = True
    return _engine
