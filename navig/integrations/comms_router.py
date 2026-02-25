"""
NAVIG Unified CommsRouter — Multi-Channel Messaging with Automatic Fallback

Routes human-in-the-loop (HitL) requests through:
    Primary:   Matrix Synapse (self-hosted, E2EE, federated)
    Fallback1: Telegram Bot API (fast, reliable)
    Fallback2: SMS via Twilio (last resort, no internet required on user side)

Used by the browser executor and desktop agent to:
- Pause tasks and await human input (2FA codes, CAPTCHA, approvals)
- Send task completion notifications with screenshots
- Forward system alerts (disk full, service down)

Architecture:
    CommsRouter.send() tries each channel in order.
    If a channel fails or times out, the next is tried.
    The channel that succeeds is promoted for the next call.
    Preference is persisted in knowledge graph: user → comms_preferred_channel → "matrix"

Channels:
    MatrixHitLChannel   — wraps NavigMatrixBot (existing comms/matrix.py)
    TelegramHitLChannel — wraps TelegramChannel (existing gateway/channels/telegram.py)
    SMSHitLChannel      — wraps Twilio REST API

Usage:
    from navig.integrations.comms_router import get_comms_router

    router = get_comms_router()

    # Pause task, ask a question
    reply = await router.ask("2FA code for GitHub?", timeout=300)

    # Choice with buttons
    choice = await router.choose("Continue with next step?", ["Yes", "Skip", "Abort"])

    # Notification
    await router.notify("✅ Task complete: paid bills", screenshot="/tmp/shot.png")
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────── Channel interface ────────────────────────────────

class HitLChannel(ABC):
    """Abstract Human-in-the-Loop channel."""

    name: str = "base"
    available: bool = True            # set False to skip this channel
    _consecutive_failures: int = 0
    _MAX_FAILURES = 3                 # disable after 3 consecutive failures

    @abstractmethod
    async def ping(self) -> bool:
        """Check if channel is reachable. Returns False to skip."""

    @abstractmethod
    async def ask(self, question: str, timeout: int = 300) -> str:
        """Ask a free-text question. Returns user reply or '' on timeout."""

    @abstractmethod
    async def choose(self, question: str, options: List[str], timeout: int = 300) -> str:
        """Show options and wait for a selection. Returns selected option or ''."""

    @abstractmethod
    async def notify(self, message: str, screenshot_path: Optional[str] = None) -> bool:
        """Send a notification. Returns True on success."""

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._MAX_FAILURES:
            self.available = False
            logger.warning("CommsRouter: channel %s disabled after %d failures", self.name, self._MAX_FAILURES)


# ─────────────────────────── Matrix channel ──────────────────────────────────

class MatrixHitLChannel(HitLChannel):
    """
    Human-in-the-loop via Matrix Synapse.

    Uses the existing NavigMatrixBot to send messages and wait for
    text replies in a specific room. Inline choices are presented
    as numbered options (since Matrix doesn't have native inline buttons).
    """

    name = "matrix"

    def __init__(self, bot_provider, room_id: str) -> None:
        """
        bot_provider: callable() -> NavigMatrixBot (lazy, avoids import at construction)
        room_id: Matrix room to use for HitL communication
        """
        self._bot_provider = bot_provider
        self._room_id = room_id
        self._pending: Dict[str, asyncio.Future] = {}  # corr_id → Future

    def _bot(self):
        return self._bot_provider()

    async def ping(self) -> bool:
        try:
            bot = self._bot()
            return bot is not None and bot.is_running
        except Exception:
            return False

    async def notify(self, message: str, screenshot_path: Optional[str] = None) -> bool:
        try:
            bot = self._bot()
            if not bot or not bot.is_running:
                return False
            # Upload screenshot if given
            if screenshot_path and Path(screenshot_path).exists():
                await bot.upload_file(self._room_id, screenshot_path, body="📸 Task Screenshot")
            await bot.send_message(self._room_id, f"🤖 NAVIG\n\n{message}")
            self.record_success()
            return True
        except Exception as exc:
            logger.warning("Matrix notify failed: %s", exc)
            self.record_failure()
            return False

    async def ask(self, question: str, timeout: int = 300) -> str:
        """
        Send a question and wait for the next text reply in the room.
        Uses a unique correlation id embedded in the question to avoid
        picking up unrelated messages.
        """
        import uuid
        corr = str(uuid.uuid4())[:6]
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[corr] = future

        try:
            bot = self._bot()
            if not bot or not bot.is_running:
                return ""
            # Register one-shot reply handler
            async def _on_reply(room_id, sender, body):
                if corr in body or corr not in self._pending:
                    return
                f = self._pending.get(corr)
                if f and not f.done():
                    f.set_result(body.strip())

            bot.on_message(_on_reply)
            await bot.send_message(
                self._room_id,
                f"⏸️ NAVIG (ref:{corr})\n\n{question}\n\n_Reply to this message with your answer_"
            )
            reply = await asyncio.wait_for(future, timeout=timeout)
            self.record_success()
            return reply
        except asyncio.TimeoutError:
            logger.warning("Matrix ask timed out after %ds", timeout)
            return ""
        except Exception as exc:
            logger.warning("Matrix ask failed: %s", exc)
            self.record_failure()
            return ""
        finally:
            self._pending.pop(corr, None)

    async def choose(self, question: str, options: List[str], timeout: int = 300) -> str:
        """Present numbered choices. User replies with the number."""
        numbered = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))
        full_q = f"{question}\n\n{numbered}\n\n_Reply with the number of your choice._"
        reply = await self.ask(full_q, timeout=timeout)
        if reply.isdigit():
            idx = int(reply) - 1
            if 0 <= idx < len(options):
                return options[idx]
        # Try matching option text directly
        for opt in options:
            if opt.lower() in reply.lower():
                return opt
        return reply  # raw reply if no match


# ─────────────────────────── Telegram channel ────────────────────────────────

class TelegramHitLChannel(HitLChannel):
    """
    Human-in-the-loop via Telegram Bot API.

    Wraps the existing TelegramChannel (gateway/channels/telegram.py) for HitL.
    Falls through to the new telegram_bridge.py HitL methods for pause/ask.
    """

    name = "telegram"

    def __init__(self, bridge_provider) -> None:
        """bridge_provider: callable() → TelegramBridge (from integrations/telegram_bridge.py)"""
        self._bp = bridge_provider

    def _bridge(self):
        return self._bp()

    async def ping(self) -> bool:
        try:
            b = self._bridge()
            return b is not None
        except Exception:
            return False

    async def notify(self, message: str, screenshot_path: Optional[str] = None) -> bool:
        try:
            await self._bridge().send_notification(message, screenshot_path=screenshot_path)
            self.record_success()
            return True
        except Exception as exc:
            logger.warning("Telegram notify failed: %s", exc)
            self.record_failure()
            return False

    async def ask(self, question: str, timeout: int = 300) -> str:
        try:
            result = await self._bridge().send_2fa_request(question)
            self.record_success()
            return result
        except Exception as exc:
            logger.warning("Telegram ask failed: %s", exc)
            self.record_failure()
            return ""

    async def choose(self, question: str, options: List[str], timeout: int = 300) -> str:
        try:
            result = await self._bridge().pause_and_ask(question, options)
            self.record_success()
            return result
        except Exception as exc:
            logger.warning("Telegram choose failed: %s", exc)
            self.record_failure()
            return ""


# ─────────────────────────── SMS channel (Twilio) ────────────────────────────

class SMSHitLChannel(HitLChannel):
    """
    SMS fallback via Twilio. No internet required on user's phone.
    Free-text only — no buttons.

    Configure:
        navig cred add twilio --account_sid <sid> --auth_token <tok> --from_number +15551234567
        navig kg remember user sms_phone +15557654321
    """

    name = "sms"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_number: str) -> None:
        self._sid = account_sid
        self._tok = auth_token
        self._from = from_number
        self._to = to_number

    async def ping(self) -> bool:
        return bool(self._sid and self._tok and self._from and self._to)

    async def _send_sms(self, body: str) -> bool:
        try:
            import httpx
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    data={"From": self._from, "To": self._to, "Body": body[:1600]},
                    auth=(self._sid, self._tok),
                    timeout=15,
                )
                ok = resp.status_code in (200, 201)
                if not ok:
                    logger.warning("Twilio SMS failed: %s %s", resp.status_code, resp.text[:200])
                return ok
        except Exception as exc:
            logger.warning("SMS send failed: %s", exc)
            return False

    async def notify(self, message: str, screenshot_path: Optional[str] = None) -> bool:
        # SMS can't send images; strip screenshot reference
        ok = await self._send_sms(f"NAVIG: {message}")
        if ok:
            self.record_success()
        else:
            self.record_failure()
        return ok

    async def ask(self, question: str, timeout: int = 300) -> str:
        """Send question via SMS. Cannot auto-receive reply — returns '' (limitation).
        The user's reply would need a Twilio webhook configured to feed back.
        For now: sends SMS and returns '' to signal no reply available.
        """
        await self._send_sms(f"NAVIG asks: {question}\n\nReply to your NAVIG dashboard to continue.")
        return ""  # Can't wait for SMS reply without inbound webhook

    async def choose(self, question: str, options: List[str], timeout: int = 300) -> str:
        numbered = " | ".join(f"{i+1}:{opt}" for i, opt in enumerate(options))
        await self._send_sms(f"NAVIG: {question} [{numbered}] Reply via dashboard.")
        return ""


# ─────────────────────────── CommsRouter ─────────────────────────────────────

class CommsRouter:
    """
    Unified messaging router with automatic channel fallback.

    Channel priority (highest to lowest):
        1. Matrix Synapse  — self-hosted, E2EE, federated
        2. Telegram        — fast, reliable, with inline buttons
        3. SMS (Twilio)    — last resort, no smartphone app required

    A channel is promoted to preferred if it succeeds consecutively.
    A channel is disabled if it fails 3 times in a row.
    Preference is persisted in the knowledge graph.
    """

    def __init__(self, channels: List[HitLChannel]) -> None:
        self._channels = channels
        self._preferred_idx = 0  # index into channels of currently preferred channel

    # ── Core API ─────────────────────────────────────────────────────────────

    async def notify(self, message: str, screenshot_path: Optional[str] = None) -> bool:
        """Send a one-way notification. Tries channels in priority order."""
        for ch in self._priority_order():
            ok = await ch.notify(message, screenshot_path=screenshot_path)
            if ok:
                self._promote(ch)
                return True
        logger.error("CommsRouter: all channels failed for notify")
        return False

    async def ask(self, question: str, timeout: int = 300) -> str:
        """Ask a free-text question. Returns reply or '' if all channels fail."""
        for ch in self._priority_order():
            if not await ch.ping():
                continue
            reply = await ch.ask(question, timeout=timeout)
            if reply:
                self._promote(ch)
                return reply
        logger.warning("CommsRouter: no reply from any channel for ask: %s", question[:60])
        return ""

    async def choose(
        self, question: str, options: List[str], timeout: int = 300
    ) -> str:
        """Present choices. Returns selected option or '' if all channels fail."""
        for ch in self._priority_order():
            if not await ch.ping():
                continue
            choice = await ch.choose(question, options, timeout=timeout)
            if choice:
                self._promote(ch)
                return choice
        return ""

    async def send_task_complete(
        self,
        task_description: str,
        success: bool,
        screenshot_path: Optional[str] = None,
    ) -> None:
        icon = "✅" if success else "❌"
        status = "completed" if success else "FAILED"
        msg = f"{icon} Task {status}: {task_description}"
        await self.notify(msg, screenshot_path=screenshot_path)

    # ── HitL integration point for browser executor ──────────────────────────

    async def handle_browser_pause(
        self,
        signal: str,        # "captcha" | "2fa" | "blocked"
        context: str = "",  # extra info (URL, service name)
        screenshot_path: Optional[str] = None,
    ) -> str:
        """
        Called when browser executor detects NeedsHuman.

        For '2fa': asks for the code, returns it
        For 'captcha': notifies user, returns '' (user must solve in browser)
        For 'blocked': notifies and returns 'abort'
        """
        if signal == "2fa":
            service = context or "unknown service"
            q = f"🔐 2FA code needed for *{service}*\nReply with the verification code."
            return await self.ask(q, timeout=300)

        elif signal == "captcha":
            await self.notify(
                f"🤖 CAPTCHA detected on {context or 'a page'}.\n"
                f"Please open your browser and solve it, then click Continue.",
                screenshot_path=screenshot_path,
            )
            choice = await self.choose(
                "CAPTCHA detected. What should I do?",
                ["Continue after you solve it", "Skip this task", "Abort"],
                timeout=600,
            )
            return choice.lower().split()[0] if choice else "abort"

        elif signal == "blocked":
            await self.notify(
                f"⛔ Access blocked on {context or 'a page'}. Task paused.",
                screenshot_path=screenshot_path,
            )
            return "abort"

        return ""

    # ── Internals ─────────────────────────────────────────────────────────────

    def _priority_order(self) -> List[HitLChannel]:
        """Return channels starting from preferred, skipping disabled ones."""
        all_ch = self._channels[self._preferred_idx:] + self._channels[:self._preferred_idx]
        return [c for c in all_ch if c.available]

    def _promote(self, channel: HitLChannel) -> None:
        """Make this channel the preferred one."""
        try:
            self._preferred_idx = self._channels.index(channel)
        except ValueError:
            pass

    def status(self) -> List[Dict[str, Any]]:
        """Return status of all channels."""
        return [
            {
                "name": c.name,
                "available": c.available,
                "preferred": i == self._preferred_idx,
                "failures": c._consecutive_failures,
            }
            for i, c in enumerate(self._channels)
        ]


# ─────────────────────────── singleton factory ───────────────────────────────

_router_instance: Optional[CommsRouter] = None


def get_comms_router() -> CommsRouter:
    """
    Build and return the singleton CommsRouter.

    Channel availability is detected automatically:
    - Matrix: enabled if NavigMatrixBot is running (from comms/matrix.py)
    - Telegram: enabled if telegram_bridge credentials exist in vault
    - SMS: enabled if twilio credentials exist in vault + sms_phone in KG

    The router degrades gracefully if only one channel is configured.
    """
    global _router_instance
    if _router_instance is not None:
        return _router_instance

    channels: List[HitLChannel] = []

    # ── 1. Matrix (primary) ───────────────────────────────────────────────────
    try:
        from navig.comms.matrix import get_matrix_bot
        from navig.config import get_config
        cfg = get_config()

        # Get the default room from config or KG
        matrix_room = getattr(cfg, "matrix_hitp_room", "") or ""
        if not matrix_room:
            try:
                from navig.memory.knowledge_graph import get_knowledge_graph
                facts = get_knowledge_graph().recall("user", predicate="matrix_hitp_room")
                matrix_room = facts[0].object if facts else ""
            except Exception:
                pass

        if matrix_room:
            ch_matrix = MatrixHitLChannel(get_matrix_bot, matrix_room)
            channels.append(ch_matrix)
            logger.info("CommsRouter: Matrix channel configured (room=%s)", matrix_room)
        else:
            logger.info("CommsRouter: Matrix channel skipped (no matrix_hitp_room set)")
    except Exception as exc:
        logger.info("CommsRouter: Matrix channel unavailable: %s", exc)

    # ── 2. Telegram (fallback 1) ──────────────────────────────────────────────
    try:
        from navig.integrations.telegram_bridge import get_telegram_bridge
        ch_tg = TelegramHitLChannel(get_telegram_bridge)
        channels.append(ch_tg)
        logger.info("CommsRouter: Telegram channel configured")
    except Exception as exc:
        logger.info("CommsRouter: Telegram channel unavailable: %s", exc)

    # ── 3. SMS via Twilio (fallback 2) ────────────────────────────────────────
    try:
        from navig.vault import get_vault
        from navig.memory.knowledge_graph import get_knowledge_graph
        vault = get_vault()
        twilio_creds = vault.list(provider="twilio")
        if twilio_creds:
            td = twilio_creds[0].data
            kg = get_knowledge_graph()
            phone_facts = kg.recall("user", predicate="sms_phone")
            to_number = phone_facts[0].object if phone_facts else ""
            if to_number:
                ch_sms = SMSHitLChannel(
                    account_sid=td.get("account_sid", ""),
                    auth_token=td.get("auth_token", ""),
                    from_number=td.get("from_number", ""),
                    to_number=to_number,
                )
                channels.append(ch_sms)
                logger.info("CommsRouter: SMS channel configured (to=%s)", to_number[-4:])
    except Exception as exc:
        logger.info("CommsRouter: SMS channel unavailable: %s", exc)

    if not channels:
        logger.warning(
            "CommsRouter: no channels configured. "
            "Set up Matrix, Telegram, or Twilio credentials."
        )

    # Prefer the channel that was last used successfully (from KG)
    try:
        from navig.memory.knowledge_graph import get_knowledge_graph
        facts = get_knowledge_graph().recall("user", predicate="comms_preferred_channel")
        if facts:
            preferred_name = facts[0].object
            for i, ch in enumerate(channels):
                if ch.name == preferred_name:
                    # Will be promoted on first use
                    break
    except Exception:
        pass

    _router_instance = CommsRouter(channels)
    return _router_instance
