"""
Telegram Channel Adapter for NAVIG Gateway

Provides integration with Telegram Bot API for:
- Receiving messages from users
- Sending responses back
- Handling commands
- Supporting inline keyboards
- Session isolation per user/group
- Mention gating for groups
"""

import asyncio
import html
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Lazy imports
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass  # optional dependency not installed; feature disabled

# Inline keyboard system
try:
    from navig.gateway.channels.telegram_keyboards import (  # noqa: F401
        CallbackHandler,
        ResponseKeyboardBuilder,
        _settings_header_text,
        build_settings_keyboard,
        get_callback_store,
    )

    HAS_KEYBOARDS = True
except ImportError:
    HAS_KEYBOARDS = False

# Session management
try:
    from navig.gateway.channels.telegram_sessions import (
        MentionGate,  # noqa: F401
        SessionManager,  # noqa: F401
        get_mention_gate,
        get_session_manager,
    )

    HAS_SESSIONS = True
except ImportError:
    HAS_SESSIONS = False

# Message templates
try:
    from navig.gateway.channels.telegram_templates import enforce_response_limits

    HAS_TEMPLATES = True
except ImportError:
    HAS_TEMPLATES = False

# Decoy responder for unauthorized users
try:
    from navig.gateway.decoy_responder import generate as generate_decoy

    HAS_DECOY = True
except ImportError:
    HAS_DECOY = False

# Cinematic pipeline renderer
try:
    from navig.gateway.channels.telegram_renderer import StatusRenderer

    HAS_RENDERER = True
except ImportError:
    HAS_RENDERER = False

# Mode classifier
try:
    from navig.gateway.channels.telegram_mode_classifier import (  # noqa: F401
        _contains_title_marker,
        _has_mid_sentence_cap,
        _has_script_mixing,
        _is_non_latin_dominant,
        _match_system_intent,
        classify_mode,
        extract_url,
        mode_to_llm_tier,
        select_tools_for_text,
    )

    HAS_CLASSIFIER = True
except ImportError:
    HAS_CLASSIFIER = False

# Voice STT/TTS pipeline
try:
    from navig.voice.stt import STT as _STT
    from navig.voice.stt import STTConfig as _STTConfig
    from navig.voice.stt import STTProvider as _STTProvider
    from navig.voice.tts import TTS as _TTS
    from navig.voice.tts import TTSConfig as _TTSConfig
    from navig.voice.tts import TTSProvider as _TTSProvider

    HAS_VOICE = True
except ImportError:
    HAS_VOICE = False

logger = logging.getLogger(__name__)


@dataclass
class TelegramMessage:
    """A message from Telegram."""

    chat_id: int
    user_id: int
    username: str
    text: str
    message_id: int
    is_group: bool = False
    reply_to_message_id: int | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "username": self.username,
            "message_id": self.message_id,
            "is_group": self.is_group,
            "reply_to": self.reply_to_message_id,
        }


class TelegramChannel:
    """
    Telegram Bot API channel adapter.

    Features:
    - Long polling for messages
    - Webhook support (optional)
    - Message sending with markdown
    - Inline keyboards
    - Command handling
    """

    _NAV_ROOT_SCREEN = "main"

    def __init__(
        self,
        bot_token: str,
        allowed_users: list[int] | None = None,
        allowed_groups: list[int] | None = None,
        on_message: Callable | None = None,
        on_approval_response: (
            Callable[[int, bool, str | None], Awaitable[tuple[bool, str]]] | None
        ) = None,
        enable_notifications: bool = True,
        require_auth: bool = True,
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
    ):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.allowed_users = set(allowed_users or [])
        self.allowed_groups = set(allowed_groups or [])
        self.on_message = on_message
        self.on_approval_response = on_approval_response
        self.enable_notifications = enable_notifications
        self.require_auth = require_auth
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret

        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._last_update_id = 0
        self._poll_task: asyncio.Task | None = None
        self._reminder_task: asyncio.Task | None = None
        self._notifier = None
        self._use_webhook = bool(webhook_url)

        # Per-user model tier preference: {user_id: "small"|"big"|"coder_big"|""}
        self._user_model_prefs: dict[int, str] = {}
        # Users who have enabled debug mode via /trace debug on
        self._debug_users: set = set()

        # Inline keyboard system
        self._kb_builder: ResponseKeyboardBuilder | None = None
        self._cb_handler: CallbackHandler | None = None
        if HAS_KEYBOARDS:
            self._kb_builder = ResponseKeyboardBuilder()
            self._cb_handler = CallbackHandler(self)

    @staticmethod
    def _is_group_chat_id(chat_id: int) -> bool:
        """Best-effort chat scope detector for Telegram IDs."""
        return chat_id < 0

    def _set_one_shot_noai(self, user_id: int) -> None:
        """Set one-shot no-AI mode for the next message only."""
        self._user_model_prefs[user_id] = "noai"

    def _set_user_tier_pref(
        self,
        chat_id: int,
        user_id: int,
        tier: str,
        *,
        is_group: bool | None = None,
        persist: bool = True,
    ) -> None:
        """Set persistent user tier preference in memory + session metadata."""
        valid = {"small", "big", "coder_big", ""}
        normalized = tier if tier in valid else ""

        if normalized:
            self._user_model_prefs[user_id] = normalized
        else:
            self._user_model_prefs.pop(user_id, None)

        if not persist or not HAS_SESSIONS:
            return

        try:
            sm = get_session_manager()
            sm.set_session_metadata(
                chat_id,
                user_id,
                "model_tier_pref",
                normalized or None,
                is_group=self._is_group_chat_id(chat_id) if is_group is None else is_group,
            )
        except Exception as e:
            logger.debug("Could not persist model tier preference: %s", e)

    def _get_user_tier_pref(
        self,
        chat_id: int,
        user_id: int,
        *,
        is_group: bool | None = None,
    ) -> str:
        """Get effective tier preference (one-shot memory first, then session)."""
        in_memory = self._user_model_prefs.get(user_id, "")
        if in_memory in {"small", "big", "coder_big", "noai"}:
            return in_memory

        if not HAS_SESSIONS:
            return ""

        try:
            sm = get_session_manager()
            persisted = sm.get_session_metadata(
                chat_id,
                user_id,
                "model_tier_pref",
                default="",
                is_group=self._is_group_chat_id(chat_id) if is_group is None else is_group,
            )
            if persisted in {"small", "big", "coder_big"}:
                self._user_model_prefs[user_id] = persisted
                return persisted
        except Exception as e:
            logger.debug("Could not read persisted model tier preference: %s", e)

        return ""

    # ── Access Control ─────────────────────────────────────────────
    def _is_user_authorized(self, user_id: int, chat_id: int, is_group: bool) -> bool:
        """Check if a user/chat is authorized to interact with the bot.

        Rules:
        - If ``require_auth`` is **False** → everyone is authorized (open mode).
        - If ``require_auth`` is **True** (default):
          - DM: user must be in ``allowed_users``. Empty list = deny all.
          - Group: chat must be in ``allowed_groups`` **or** user in ``allowed_users``.
        """
        if not self.require_auth:
            return True  # open mode — no restrictions

        if user_id in self.allowed_users:
            return True

        if is_group:
            return chat_id in self.allowed_groups

        # DM from unknown user — deny
        return False

    async def start(self):
        """Start the Telegram channel."""
        if aiohttp is None:
            logger.error("aiohttp not installed. Cannot start Telegram channel.")
            raise RuntimeError("aiohttp not installed; cannot start Telegram channel")

        # total=60 must exceed the getUpdates long-poll timeout (30s) to avoid
        # spurious TimeoutError on every polling cycle.
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, connect=10))

        # Get bot info — validate token before marking as running
        try:
            me = await self._api_call("getMe")
        except Exception as e:
            await self._session.close()
            self._session = None
            logger.error("Failed to connect to Telegram: %s", e)
            raise RuntimeError(f"Telegram connection failed: {e}") from e

        if not me:
            await self._session.close()
            self._session = None
            logger.error(
                "Telegram API rejected the bot token (getMe returned ok=false). "
                "Check TELEGRAM_BOT_TOKEN."
            )
            raise RuntimeError(
                "Telegram API rejected bot token (ok=false) — check TELEGRAM_BOT_TOKEN"
            )

        # Token is valid — mark running and complete setup
        self._running = True
        self._bot_username = me.get("username", "")
        logger.info("Telegram bot started: @%s", self._bot_username)

        # Auth status
        if self.require_auth:
            if self.allowed_users:
                logger.info("Auth ENFORCED: %d allowed users", len(self.allowed_users))
            else:
                logger.warning(
                    "Auth ENFORCED but allowed_users is EMPTY — all DMs will be blocked!"
                )
        else:
            logger.warning("Auth DISABLED (require_auth=false) — bot is open to everyone")

        # Register slash commands with Telegram
        await self._register_commands()

        # Send startup notification to allowed users
        from navig.boot_messages import get_boot_message

        boot_msg = get_boot_message()
        for user_id in self.allowed_users:
            try:
                await self.send_message(
                    user_id,
                    boot_msg,
                    parse_mode=None,
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Start notifications if enabled
        if self.enable_notifications and self.allowed_users:
            await self._start_notifier()

        # Start polling or webhook
        if self._use_webhook:
            await self._setup_webhook()
        else:
            self._poll_task = asyncio.create_task(self._poll_updates())

        self._reminder_task = asyncio.create_task(self._poll_due_reminders())

    async def _start_notifier(self):
        """Start the notification system."""
        try:
            from navig.gateway.notifications import TelegramNotifier

            # Use first allowed user as default notification target
            default_chat = list(self.allowed_users)[0] if self.allowed_users else None

            if default_chat:
                self._notifier = TelegramNotifier(self, default_chat)
                await self._notifier.start()
                logger.info("Telegram notifier started for chat %s", default_chat)
        except Exception as e:
            logger.error("Failed to start notifier: %s", e)

    async def stop(self):
        """Stop the Telegram channel."""
        self._running = False

        # Stop notifier
        if self._notifier:
            try:
                await self._notifier.stop()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Remove webhook if we set one
        if self._use_webhook:
            try:
                await self._api_call("deleteWebhook")
                logger.info("Telegram webhook removed")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._reminder_task:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._session:
            await self._session.close()

    async def _api_call(
        self, method: str, data: dict | None = None, *, _retry_count: int = 0
    ) -> dict | None:
        """Make an API call to Telegram with rate limit handling."""
        if not self._session:
            return None

        url = f"{self.base_url}/{method}"
        _MAX_RETRIES = 3

        try:
            async with self._session.post(url, json=data or {}) as resp:
                # Handle rate limiting (HTTP 429)
                if resp.status == 429:
                    if _retry_count >= _MAX_RETRIES:
                        logger.error(
                            "Telegram API rate limited after %d retries: %s",
                            _MAX_RETRIES,
                            method,
                        )
                        return None
                    # Get retry_after from response or use exponential backoff
                    retry_after = 1
                    try:
                        err_body = await resp.json()
                        retry_after = int(err_body.get("parameters", {}).get("retry_after", 1))
                    except Exception:
                        retry_after = 2**_retry_count  # exponential backoff
                    logger.warning(
                        "Telegram API rate limited on %s — retry %d/%d in %ds",
                        method,
                        _retry_count + 1,
                        _MAX_RETRIES,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    return await self._api_call(method, data, _retry_count=_retry_count + 1)

                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                else:
                    logger.error("Telegram API error: %s", result.get("description"))
                    return None
        except asyncio.TimeoutError:
            logger.error("Telegram API call timed out: %s", method)
            return None
        except Exception as e:
            logger.error("Telegram API call failed: %s", e)
            return None

    async def _poll_updates(self):
        """Long-poll for updates from Telegram."""
        while self._running:
            try:
                updates = await self._api_call(
                    "getUpdates",
                    {
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )

                if updates:
                    for update in updates:
                        self._last_update_id = update["update_id"]
                        await self._process_update(update)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Polling error: %s", e)
                await asyncio.sleep(5)

    async def _poll_due_reminders(self):
        """Deliver due reminders from RuntimeStore in the background."""
        _MAX_RETRIES = 3  # BUG-1: give up after this many failed send attempts
        _RETRY_DELAY_SEC = 60  # push remind_at forward on each retry
        poll_interval_sec = 15
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            proactive_cfg = cm.global_config.get("proactive", {}) if cm.global_config else {}
            poll_interval_sec = int(
                proactive_cfg.get("reminder_poll_interval_sec", poll_interval_sec)
            )
        except Exception:
            pass  # best-effort: config unavailable; use default reminder poll interval

        while self._running:
            try:
                from navig.store.runtime import get_runtime_store

                store = get_runtime_store()
                due_items = store.get_due_reminders()
                for reminder in due_items:
                    reminder_id = int(reminder.get("id") or 0)
                    chat_id = reminder.get("chat_id")
                    msg = str(reminder.get("message") or "").strip()
                    retry_count = int(reminder.get("retry_count") or 0)
                    remind_at_str = str(reminder.get("remind_at") or "")

                    if not chat_id or not msg:
                        # Malformed row — close it immediately
                        if reminder_id:
                            store.complete_reminder(reminder_id)
                        continue

                    # Staleness check: silently discard reminders older than 24 h
                    # (bot was offline; delivering ancient alerts would be confusing)
                    _stale = False
                    _overdue_hours = 0.0
                    try:
                        from datetime import datetime, timezone as _tz
                        _due_dt = datetime.fromisoformat(
                            remind_at_str.rstrip("Z")
                        ).replace(tzinfo=_tz.utc)
                        _overdue_hours = (
                            datetime.now(_tz.utc) - _due_dt
                        ).total_seconds() / 3600
                        _stale = _overdue_hours > 24
                    except Exception:
                        pass

                    if _stale:
                        if reminder_id:
                            store.fail_reminder(reminder_id)
                        logger.info(
                            "Reminder id=%s silently expired (%.1f h overdue)",
                            reminder_id,
                            _overdue_hours,
                        )
                        continue

                    # Add a "missed" notice when first delivering an overdue reminder
                    _header = "⏰ <b>Reminder</b>"
                    if _overdue_hours > 1 and retry_count == 0:
                        # Show the due time in server-local timezone so it matches
                        # the time the user originally entered (e.g. "23:30" not UTC)
                        try:
                            from datetime import timezone as _tz2
                            _due_dt2 = datetime.fromisoformat(
                                remind_at_str.rstrip("Z")
                            ).replace(tzinfo=_tz2.utc)
                            _due_local = _due_dt2.astimezone()
                            _due_label = _due_local.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            _due_label = remind_at_str.replace("T", " ")[:16]
                        _header = f"⏰ <b>Missed reminder</b> <i>(was due {_due_label})</i>"

                    sent = await self.send_message(
                        int(chat_id),
                        f"{_header}\n{html.escape(msg)}",
                        parse_mode="HTML",
                    )
                    if sent:
                        if reminder_id:
                            store.complete_reminder(reminder_id)
                    else:
                        # BUG-1: handle send failure with capped retry logic
                        if reminder_id:
                            if retry_count >= _MAX_RETRIES:
                                store.fail_reminder(reminder_id)
                                logger.warning(
                                    "Reminder id=%s permanently failed after %d retries "
                                    "(chat_id=%s, msg=%r)",
                                    reminder_id,
                                    retry_count,
                                    chat_id,
                                    msg[:60],
                                )
                            else:
                                store.increment_reminder_retry(reminder_id, _RETRY_DELAY_SEC)
                                logger.debug(
                                    "Reminder id=%s send failed (retry %d/%d), rescheduled +%ds",
                                    reminder_id,
                                    retry_count + 1,
                                    _MAX_RETRIES,
                                    _RETRY_DELAY_SEC,
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Reminder poller error: %s", e)

            await asyncio.sleep(poll_interval_sec)

    # ── Webhook mode ───────────────────────────────────────────────

    async def _setup_webhook(self):
        """Register the webhook URL with Telegram and delete any pending updates."""
        params: dict[str, Any] = {
            "url": self.webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        }
        if self.webhook_secret:
            params["secret_token"] = self.webhook_secret

        result = await self._api_call("setWebhook", params)
        if result is not None:
            logger.info("Telegram webhook set: %s", self.webhook_url)
        else:
            logger.error("Failed to set Telegram webhook — falling back to polling")
            self._use_webhook = False
            # Guard against duplicate poll tasks
            if self._poll_task is None or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_updates())
            else:
                logger.warning("Poll task already running — skipping duplicate")

    async def handle_webhook_update(self, update: dict, secret_header: str = "") -> bool:
        """
        Process a webhook update pushed by Telegram.

        Called by the gateway HTTP server when it receives a POST to the
        webhook route.  Returns True if the update was accepted.

        Args:
            update: Raw Telegram Update JSON dict.
            secret_header: Value of the ``X-Telegram-Bot-Api-Secret-Token``
                           header sent by Telegram (for HMAC validation).
        """
        # Validate secret token if configured
        if self.webhook_secret and secret_header != self.webhook_secret:
            logger.warning("Webhook secret mismatch — rejecting update")
            return False

        if not self._running:
            return False

        try:
            await self._process_update(update)
            return True
        except Exception as e:
            logger.error("Webhook update processing error: %s", e)
            return False

    async def _process_update(self, update: dict):
        """Process a single update from Telegram."""
        # ── Handle callback queries (inline button presses) ──
        callback_query = update.get("callback_query")
        if callback_query:
            cb_user = callback_query.get("from", {})
            cb_user_id = cb_user.get("id")
            cb_message = callback_query.get("message") or {}
            cb_chat = cb_message.get("chat", {})
            cb_message_id = cb_message.get("message_id")
            cb_is_group = cb_chat.get("type") in ("group", "supergroup")
            cb_chat_id_cq = cb_chat.get("id", 0)
            if not self._is_user_authorized(cb_user_id, cb_chat_id_cq, cb_is_group):
                logger.warning("Unauthorized callback: user_id=%s", cb_user_id)
                return
            # ── slash: monitoring refresh/nav buttons ──────────────────────
            cb_data = callback_query.get("data", "")
            if cb_data.startswith("slash:"):
                cmd_name = cb_data[6:]  # e.g. "disk", "memory"
                try:
                    await self._api_call(
                        "answerCallbackQuery",
                        {"callback_query_id": callback_query["id"]},
                    )
                except Exception:
                    pass
                handler_fn = None
                try:
                    import functools

                    from navig.gateway.channels.telegram_commands import (
                        TelegramCommandsMixin,
                        _SLASH_REGISTRY as _sr,
                    )

                    _entry = next(
                        (e for e in _sr if e.command == cmd_name and e.handler), None
                    )
                    if _entry:
                        # Try self first (in case the method was mixed in at runtime)
                        handler_fn = getattr(self, _entry.handler, None)
                        # Fall back to the mixin (TelegramChannel doesn't inherit it)
                        if handler_fn is None:
                            mixin_fn = getattr(TelegramCommandsMixin, _entry.handler, None)
                            if mixin_fn is not None:
                                handler_fn = functools.partial(mixin_fn, self)
                except Exception:
                    pass
                if handler_fn:
                    try:
                        import inspect as _insp
                        _sig = _insp.signature(handler_fn)
                        _kw = {
                            "chat_id": cb_chat_id_cq,
                            "user_id": cb_user_id,
                            "message_id": cb_message_id,
                            "metadata": {},
                        }
                        _kw = {k: v for k, v in _kw.items() if k in _sig.parameters}
                        await handler_fn(**_kw)
                    except Exception as exc:
                        logger.error("slash: callback handler %r error: %s", cmd_name, exc)
                return
            if self._cb_handler:
                try:
                    await self._cb_handler.handle(callback_query)
                except Exception as e:
                    logger.error("Callback handler error: %s", e)
            return

        message = update.get("message", {})
        if not message:
            return

        chat = message.get("chat", {})
        sender = message.get("from", {})
        text = message.get("text", "")

        chat_id = chat.get("id")
        user_id = sender.get("id")
        username = sender.get("username", str(user_id))
        is_group = chat.get("type") in ("group", "supergroup")
        message_id = message.get("message_id")
        reply_to_msg = message.get("reply_to_message", {})
        reply_to_message_id = reply_to_msg.get("message_id")

        _voice_lang = ""  # detected language from STT (empty for text messages)
        # ── Handle non-text messages (voice, sticker, photo, etc.) ──
        if not text and not message.get("caption"):
            # Check what kind of non-text content it is
            if message.get("voice") or message.get("audio"):
                content_type = "voice"
            elif message.get("sticker"):
                content_type = "sticker"
            elif message.get("photo"):
                content_type = "photo"
            elif message.get("video") or message.get("video_note"):
                content_type = "video"
            elif message.get("document"):
                content_type = "document"
            elif message.get("animation"):
                content_type = "gif"
            elif message.get("location"):
                content_type = "location"
            elif message.get("contact"):
                content_type = "contact"
            else:
                content_type = None

            if content_type:
                # ── Voice: full STT pipeline ──────────────────────────────────
                if content_type == "voice" and HAS_VOICE:
                    voice_data = message.get("voice") or message.get("audio")
                    text, _voice_lang = await self._transcribe_voice_message(
                        chat_id, is_group, voice_data
                    )
                    if not text:
                        return  # transcription failed; error already sent
                    # text is now the transcript — fall through to pipeline
                # ── Photo: vision analysis pipeline ───────────────────────
                elif content_type == "photo":
                    await self._handle_photo_vision(chat_id, user_id, is_group, message)
                    return
                else:
                    logger.debug(
                        "Non-text message (%s) from user %s — skipping",
                        content_type,
                        user_id,
                    )
                    # Only acknowledge in DMs, not in groups
                    if not is_group and chat_id:
                        ack = {
                            "voice": "can't process voice messages yet — try typing it out?",
                            "sticker": random.choice(["👀", "😄", "nice one"]),
                            "video": "video processing isn't wired up yet.",
                            "document": "can't read files through Telegram yet. try uploading via the deck.",
                            "gif": random.choice(["😄", "ha"]),
                            "location": "noted — but I can't do much with locations yet.",
                            "contact": "got it, but contact handling isn't set up.",
                        }.get(content_type, "got something I can't process yet.")
                        await self.send_message(chat_id, ack, parse_mode=None)
                    return
            # If we can't identify it and there's no text, just ignore
            if not text:
                return

        # Use caption as text for media with captions (photos, videos with text)
        if not text and message.get("caption"):
            text = message.get("caption", "")
            if message.get("photo"):
                try:
                    await self._handle_photo_vision(chat_id, user_id, is_group, message)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Captioned photo analysis failed: %s", exc)

        # Check if replying to a bot message
        is_reply_to_bot = False
        if reply_to_msg:
            reply_from = reply_to_msg.get("from", {})
            is_reply_to_bot = reply_from.get("is_bot", False)

        # ── Access control ──
        # When require_auth is True (default), only listed users get through.
        # An empty allowed_users list with require_auth = deny everyone.
        is_authorized = self._is_user_authorized(user_id, chat_id, is_group)
        if not is_authorized:
            logger.warning(
                "Unauthorized access: user_id=%s username=%s chat_id=%s text=%.60s",
                user_id,
                username,
                chat_id,
                (text or "")[:60],
            )
            if not is_group:
                # ── Decoy mode: playful non-actionable response ──
                if HAS_DECOY and text:
                    try:
                        await self.send_typing(chat_id)
                        decoy_text = generate_decoy(user_id, text)
                        await self.send_message(
                            chat_id,
                            decoy_text,
                            parse_mode=None,
                        )
                    except Exception as e:
                        logger.debug("Decoy responder error: %s", e)
            return

        # Session management
        session = None
        auto_state = None
        auto_active_in_chat = False
        auto_persona = ""
        try:
            from navig.store.runtime import get_runtime_store

            auto_state = get_runtime_store().get_ai_state(user_id)
            if auto_state and auto_state.get("session_expired"):
                state_chat_id = auto_state.get("chat_id")
                if state_chat_id is None or int(state_chat_id) == int(chat_id):
                    await self.send_message(
                        chat_id,
                        "ℹ️ Your previous AI session expired after 24h of inactivity. "
                        "Use `/agent start` to re-enable auto mode.",
                        parse_mode=None,
                    )
            if auto_state and auto_state.get("mode") == "active":
                state_chat_id = auto_state.get("chat_id")
                if state_chat_id is not None and int(state_chat_id) == int(chat_id):
                    auto_active_in_chat = True
                    auto_persona = str(auto_state.get("persona") or "")
        except Exception as e:
            logger.debug("Unable to read ai_state for user %s: %s", user_id, e)

        pre_start_last_active: str | None = None
        if HAS_SESSIONS:
            session_manager = get_session_manager()
            session = session_manager.get_session(chat_id, user_id, is_group)
            if session is not None and not is_group and text.strip().lower() == "/start":
                pre_start_last_active = str(getattr(session, "last_active", "") or "")

            # Mention gating for groups
            if is_group and hasattr(self, "_bot_username"):
                if auto_active_in_chat:
                    logger.debug(
                        "Bypassing mention gate due to active auto mode in chat %s",
                        chat_id,
                    )
                else:
                    mention_gate = get_mention_gate(self._bot_username)
                    should_respond = mention_gate.should_respond(
                        text=text,
                        user_id=user_id,
                        is_group=is_group,
                        is_reply_to_bot=is_reply_to_bot,
                        session=session,
                        reply_to_message_id=reply_to_message_id,
                    )

                    if not should_respond:
                        logger.debug("Skipping group message (no mention): %s", text[:50])
                        return

                    # Strip mention from text
                    text = mention_gate.strip_mention(text)

            # Record user message in session
            session = session_manager.add_user_message(
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                message_id=message_id,
                reply_to=reply_to_message_id,
                is_group=is_group,
                username=username,
            )

        # Build message object
        telegram_msg = TelegramMessage(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            text=text,
            message_id=message_id,
            is_group=is_group,
            reply_to_message_id=reply_to_message_id,
        )

        # Add session context to metadata
        metadata = telegram_msg.to_metadata()
        if session:
            metadata["session_key"] = session.session_key
            metadata["context_messages"] = session.get_context_messages(limit=10)
            try:
                import time as _time
                try:
                    from navig.config import get_config_manager as _get_cfg_tlang
                    _lang_max_age = float(
                        _get_cfg_tlang().get(
                            "telegram.language_cache_max_age_hours", 12
                        ) or 12
                    ) * 3600
                except Exception:
                    _lang_max_age = 12 * 3600  # fallback: 12 h
                persisted_lang = session_manager.get_session_metadata(
                    chat_id,
                    user_id,
                    "last_detected_language",
                    default="",
                    is_group=is_group,
                )
                persisted_lang_ts = session_manager.get_session_metadata(
                    chat_id,
                    user_id,
                    "last_detected_language_ts",
                    default=0.0,
                    is_group=is_group,
                )
                _lang_age = _time.time() - float(persisted_lang_ts or 0)
                # Only treat as stale when a real timestamp was stored.
                # persisted_lang_ts == 0 / None means "no timestamp recorded yet"
                # (not "set at Unix epoch"), so we allow it through.
                _lang_is_stale = bool(persisted_lang_ts) and _lang_age >= _lang_max_age
                if persisted_lang and not _lang_is_stale:
                    metadata["last_detected_language"] = str(persisted_lang).strip().lower()
                elif persisted_lang and _lang_is_stale:
                    logger.debug(
                        "Ignoring stale language hint '%s' (%.1f h old)",
                        persisted_lang, _lang_age / 3600,
                    )
            except Exception as e:
                logger.debug("Could not load persisted language metadata: %s", e)
        if auto_active_in_chat:
            metadata["auto_reply_active"] = True
            if auto_persona:
                metadata["auto_reply_persona"] = auto_persona
        if _voice_lang:
            normalized_voice_lang = str(_voice_lang).strip().lower()
            metadata["detected_language"] = normalized_voice_lang
            if session:
                try:
                    import time as _time_v
                    session_manager.set_session_metadata(
                        chat_id,
                        user_id,
                        "last_detected_language",
                        normalized_voice_lang,
                        is_group=is_group,
                        username=username,
                    )
                    session_manager.set_session_metadata(
                        chat_id,
                        user_id,
                        "last_detected_language_ts",
                        _time_v.time(),
                        is_group=is_group,
                        username=username,
                    )
                except Exception as e:
                    logger.debug("Could not persist voice language metadata: %s", e)

        # Dispatch to handler
        if self.on_message:
            try:
                # ── Parse tier override from /big /small /coder prefix ──
                tier_override = ""
                clean_text = text
                is_slash_command = text.strip().startswith("/")
                for prefix, tier in (
                    ("/big ", "big"),
                    ("/small ", "small"),
                    ("/coder ", "coder_big"),
                ):
                    if text.lower().startswith(prefix):
                        tier_override = tier
                        clean_text = text[len(prefix) :].strip()
                        break

                # Apply persistent user preference if no per-message override.
                # "noai" is one-shot: consume and clear it after first use.
                if not tier_override:
                    pref_tier = self._get_user_tier_pref(chat_id, user_id, is_group=is_group)
                    if pref_tier == "noai" and is_slash_command:
                        pref_tier = ""
                    tier_override = pref_tier
                    if pref_tier == "noai" and not is_slash_command:
                        self._user_model_prefs.pop(user_id, None)

                if tier_override:
                    metadata["tier_override"] = tier_override

                # ── Inject session tier overrides for routing ──
                # These are set by /provider_hybrid and consumed by the
                # UnifiedRouter to override provider+model per tier
                # without touching durable config.
                if session and HAS_SESSIONS:
                    try:
                        from navig.gateway.channels.telegram_sessions import (
                            get_session_manager as _get_so_mgr,
                        )

                        _so_mgr = _get_so_mgr()
                        _all_so = _so_mgr.get_all_session_overrides(session)
                        if _all_so:
                            _tier_map: dict[str, dict[str, str]] = {}
                            for _t in ("small", "big", "coder_big"):
                                _p = _all_so.get(f"tier_{_t}_provider", "")
                                _m = _all_so.get(f"tier_{_t}_model", "")
                                if _p or _m:
                                    _tier_map[_t] = {"provider": _p, "model": _m}
                            if _tier_map:
                                metadata["session_tier_overrides"] = _tier_map
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; session overrides are optional

                try:
                    if await self._handle_pending_api_key_input(
                        chat_id=chat_id,
                        user_id=user_id,
                        text=text,
                    ):
                        return
                except AttributeError:
                    pass  # best-effort: attribute absent; skip
                try:
                    if await self._handle_nl_pending_reply(
                        chat_id=chat_id,
                        user_id=user_id,
                        text=text,
                    ):
                        return
                except AttributeError:
                    pass  # best-effort: attribute absent; skip
                try:
                    if await self._handle_intake_reply(
                        chat_id=chat_id,
                        user_id=user_id,
                        text=text,
                    ):
                        return
                except AttributeError:
                    pass  # best-effort: attribute absent; skip
                if not text.strip().startswith("/"):
                    try:
                        if await self._handle_natural_language_request(
                            chat_id=chat_id,
                            user_id=user_id,
                            text=text,
                            is_group=is_group,
                            username=username,
                            metadata=metadata,
                        ):
                            return
                    except AttributeError:
                        pass  # best-effort: attribute absent; skip
                # ── Slash command routing ──
                cmd = text.strip().lower()
                if cmd in ("/models", "/model") or cmd.startswith(("/models ", "/model ")):
                    await self._handle_models_command(chat_id, user_id, text=text)
                    return
                if cmd == "/status":
                    await self._handle_status(chat_id, user_id)
                    return
                if cmd == "/start":
                    await self._handle_start(
                        chat_id,
                        username,
                        user_id=user_id,
                        prior_last_active=pre_start_last_active,
                    )
                    return
                if cmd == "/help" or cmd.startswith("/help "):
                    _help_topic = cmd[len("/help "):].strip() if cmd.startswith("/help ") else None
                    await self._handle_help(chat_id, topic=_help_topic)
                    return
                if cmd.startswith("/mode"):
                    await self._handle_mode(chat_id, text=cmd, user_id=user_id)
                    return
                if cmd == "/briefing":
                    await self._handle_briefing(chat_id, user_id, metadata)
                    return
                if cmd == "/deck":
                    await self._handle_deck(chat_id)
                    return
                if cmd == "/ping":
                    await self._handle_ping(chat_id, user_id)
                    return
                if cmd == "/voiceon":
                    await self._handle_voiceon_cmd(
                        chat_id=chat_id,
                        user_id=user_id,
                        is_group=is_group,
                    )
                    return
                if cmd == "/voiceoff":
                    await self._handle_voiceoff_cmd(
                        chat_id=chat_id,
                        user_id=user_id,
                        is_group=is_group,
                    )
                    return
                if cmd == "/settings":
                    await self._handle_settings_hub(chat_id, user_id, message_id=None)
                    return
                if cmd in ("/routing", "/router") or cmd.startswith(("/routing ", "/router ")):
                    await self._handle_models_command(chat_id, user_id, text=text)
                    return
                if cmd in ("/providers", "/provider"):
                    await self._handle_providers(chat_id, user_id)
                    return
                if cmd == "/debug":
                    await self._handle_debug(chat_id)
                    return
                if cmd == "/trace":
                    await self._handle_trace_cmd(chat_id=chat_id, user_id=user_id, text=text)
                    return

                # ── Model tier overrides (standalone /big, /small, /coder, /auto) ──
                if cmd in ("/big", "/small", "/coder", "/auto"):
                    await self._handle_tier_command(chat_id, user_id, cmd)
                    return

                # ── /restart: daemon (systemd) vs container (docker) ──
                if cmd.startswith("/restart"):
                    await self._handle_restart_cmd(
                        chat_id=chat_id,
                        user_id=user_id,
                        text=text,
                        metadata=metadata,
                    )
                    return

                if cmd.startswith("/skill"):
                    await self._handle_skill_cmd(
                        chat_id=chat_id,
                        user_id=user_id,
                        text=text,
                        metadata=metadata,
                    )
                    return

                # ── Dynamic Registry Dispatch (New Features) ──
                if cmd.startswith("/"):
                    cmd_bare = cmd.split(" ")[0][1:].split("@", 1)[0]
                    import functools
                    import inspect

                    from .telegram_commands import (
                        _SLASH_REGISTRY,
                        TelegramCommandsMixin,
                    )

                    business_chat_only = {"kick", "mute", "unmute", "search"}
                    if cmd_bare in business_chat_only and not is_group:
                        await self.send_message(
                            chat_id,
                            "This command is only available in business chats (groups/supergroups).",
                            parse_mode=None,
                        )
                        return

                    if cmd_bare in business_chat_only:
                        if not await self._is_group_admin(chat_id, user_id):
                            await self.send_message(
                                chat_id,
                                "Admin permissions are required for this command in group chats.",
                                parse_mode=None,
                            )
                            return

                    registry_entry = next(
                        (e for e in _SLASH_REGISTRY if e.command == cmd_bare), None
                    )
                    if registry_entry and registry_entry.handler:
                        handler_func = getattr(self, registry_entry.handler, None)
                        if handler_func is None:
                            mixin_handler = getattr(
                                TelegramCommandsMixin, registry_entry.handler, None
                            )
                            if mixin_handler is not None:
                                handler_func = functools.partial(mixin_handler, self)
                        # Messaging mixin fallback
                        if handler_func is None:
                            try:
                                from navig.gateway.channels.telegram_messaging_mixin import (
                                    TelegramMessagingMixin,
                                )

                                msg_handler = getattr(
                                    TelegramMessagingMixin,
                                    registry_entry.handler,
                                    None,
                                )
                                if msg_handler is not None:
                                    handler_func = functools.partial(msg_handler, self)
                            except ImportError:
                                pass
                        if handler_func:
                            sig = inspect.signature(handler_func)
                            kwargs = {}
                            if "chat_id" in sig.parameters:
                                kwargs["chat_id"] = chat_id
                            if "user_id" in sig.parameters:
                                kwargs["user_id"] = user_id
                            if "username" in sig.parameters:
                                kwargs["username"] = username
                            if "metadata" in sig.parameters:
                                kwargs["metadata"] = metadata
                            if "is_group" in sig.parameters:
                                kwargs["is_group"] = is_group
                            if "text" in sig.parameters:
                                kwargs["text"] = text
                            if "session" in sig.parameters:
                                kwargs["session"] = session

                            await handler_func(**kwargs)
                            return

                # ── Server / infra commands → navig CLI ──
                cli_result = self._match_cli_command(text.strip())
                if cli_result:
                    await self._handle_cli_command(chat_id, user_id, metadata, cli_result)
                    return

                # ── One-shot raw/no-AI route ──
                if metadata.get("tier_override") == "noai":
                    stripped_text = clean_text.strip()
                    noai_cmd = self._match_cli_command(stripped_text)
                    if noai_cmd:
                        await self._handle_cli_command(chat_id, user_id, metadata, noai_cmd)
                        return

                    if stripped_text.lower().startswith("navig "):
                        await self._handle_cli_command(
                            chat_id,
                            user_id,
                            metadata,
                            stripped_text[6:].strip(),
                        )
                        return

                    await self.send_message(
                        chat_id,
                        "⚙️ No-AI mode expects a command. Use /help for shortcuts, or send <code>navig &lt;command&gt;</code>.",
                        parse_mode="HTML",
                    )
                    return

                # ── Cinematic mode dispatch ──
                await self._dispatch_by_mode(
                    tg_msg=telegram_msg,
                    clean_text=clean_text,
                    chat_id=chat_id,
                    user_id=user_id,
                    metadata=metadata,
                    session=session,
                    session_manager=session_manager if HAS_SESSIONS else None,
                    is_group=is_group,
                )

            except Exception as e:
                import traceback

                logger.error("Message handler error: %s\n%s", e, traceback.format_exc())
                # Friendly error — no robotic entity-speak
                err_msg = random.choice(
                    [
                        f"sorry, something went wrong — {e}",
                        f"oops, hit an error: {e}",
                        f"ran into a problem. {e}",
                    ]
                )
                await self.send_message(chat_id, f"❌ {err_msg}", parse_mode=None)

        else:
            # on_message handler not configured — respond contextually instead
            # of silently dropping the message (fixes #36).
            # Slash commands that provide guidance (/help, /start) don't need AI;
            # respond with useful info so users aren't left wondering what happened.
            _cmd = (text or "").strip().split()[0].lower() if text else ""
            if _cmd == "/help":
                await self.send_message(
                    chat_id,
                    "ℹ️ Available commands:\n/start — begin setup\n/help — show this help",
                    parse_mode=None,
                )
            elif _cmd == "/start":
                await self.send_message(
                    chat_id,
                    "👋 AI is not configured yet. Complete setup first, then send a message.\n"
                    "Use /help to see available commands.",
                    parse_mode=None,
                )
            elif _cmd.startswith("/"):
                await self.send_message(
                    chat_id,
                    "❌ That command is not available yet because AI is not configured. "
                    "Use /help to see available commands or /start to begin setup.",
                    parse_mode=None,
                )
            else:
                await self.send_message(
                    chat_id,
                    "❌ AI is not configured yet. Use /help to see available commands "
                    "or /start to begin setup.",
                    parse_mode=None,
                )

    async def _keep_typing(self, chat_id: int, interval: float = 4.0, max_duration: float = 120.0):
        """Re-send 'typing' indicator every ``interval`` seconds until cancelled or timeout.

        Args:
            chat_id: Telegram chat ID.
            interval: Seconds between typing indicator refreshes.
            max_duration: Maximum seconds to keep typing (safety limit).
        """
        try:
            elapsed = 0.0
            while elapsed < max_duration:
                await self.send_typing(chat_id)
                await asyncio.sleep(interval)
                elapsed += interval
            logger.debug(
                "Typing indicator timed out after %.0fs for chat %s", max_duration, chat_id
            )
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    async def _keep_recording(
        self, chat_id: int, interval: float = 4.0, max_duration: float = 120.0
    ):
        """Re-send 'record_voice' chat action every ``interval`` seconds until cancelled or timeout.

        Used during voice-message download + transcription so the user sees a
        microphone / audio-processing indicator instead of silence or a typing
        bubble.  Cancel the returned task once transcription finishes.

        Args:
            chat_id: Telegram chat ID.
            interval: Seconds between indicator refreshes.
            max_duration: Maximum seconds to keep recording indicator (safety limit).
        """
        try:
            elapsed = 0.0
            while elapsed < max_duration:
                await self._api_call(
                    "sendChatAction", {"chat_id": chat_id, "action": "record_voice"}
                )
                await asyncio.sleep(interval)
                elapsed += interval
            logger.debug(
                "Recording indicator timed out after %.0fs for chat %s", max_duration, chat_id
            )
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    # ── Cinematic mode dispatcher ───────────────────────────────────────────

    async def _dispatch_by_mode(
        self,
        tg_msg,
        clean_text: str,
        chat_id: int,
        user_id: int,
        metadata: dict,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """Classify intent mode and route to the appropriate handler."""
        # System-monitoring intent shortcut: catch free-text queries like
        # "run disk check" or "show memory usage" and route them directly to
        # the dedicated handler, bypassing the LLM pipeline entirely.  This
        # prevents the ACT → search → empty-results → hallucination path.
        if HAS_CLASSIFIER:
            _sys_cmd = _match_system_intent(clean_text)
            if _sys_cmd is not None:
                handler = getattr(self, f"_handle_{_sys_cmd}_cmd", None)
                if handler is not None:
                    import inspect

                    sig = inspect.signature(handler)
                    kwargs: dict = {"chat_id": chat_id}
                    if "user_id" in sig.parameters:
                        kwargs["user_id"] = user_id
                    if "metadata" in sig.parameters:
                        kwargs["metadata"] = metadata
                    if "text" in sig.parameters:
                        kwargs["text"] = clean_text
                    await handler(**kwargs)
                    return
                # Fallback: CLI template via registry
                _cli = self._match_cli_command(f"/{_sys_cmd}")
                if _cli:
                    await self._handle_cli_command(chat_id, user_id, metadata, _cli)
                    return

        if HAS_CLASSIFIER:
            mode = classify_mode(clean_text)
        else:
            mode = "TALK"

        if mode == "ACT" and HAS_RENDERER:
            await self._handle_act(
                clean_text,
                chat_id,
                user_id,
                metadata,
                session,
                session_manager,
                is_group,
            )
        elif mode == "CODE":
            await self._handle_code(
                clean_text,
                chat_id,
                user_id,
                metadata,
                session,
                session_manager,
                is_group,
            )
        elif mode == "REASON":
            # When a quoted entity or non-Latin statement is detected, inject a
            # language-agnostic grounding hint so the LLM acknowledges uncertainty
            # rather than confabulating. The hint is script-neutral — the model
            # will compose its response in whatever language the user wrote.
            if HAS_CLASSIFIER and (
                _contains_title_marker(clean_text)
                or _has_mid_sentence_cap(clean_text)
                or _has_script_mixing(clean_text)
                or _is_non_latin_dominant(clean_text)
            ):
                metadata = {
                    **metadata,
                    "llm_hint": (
                        "If the user's message references a title, person, event, "
                        "or named entity that you cannot verify with certainty "
                        "from your training data, say so honestly rather than "
                        "inventing details."
                    ),
                }
            await self._handle_reason(
                clean_text,
                chat_id,
                user_id,
                metadata,
                session,
                session_manager,
                is_group,
            )
        else:
            await self._handle_talk(
                clean_text,
                chat_id,
                user_id,
                metadata,
                session,
                session_manager,
                is_group,
            )

    # ── Language persistence helper ──────────────────────────────────────

    def _persist_updated_language(
        self,
        metadata: dict,
        chat_id: int,
        user_id: int,
        session_manager,
        is_group: bool,
        username: str = "",
    ) -> None:
        """Persist agent-detected language back to the session store.

        The channel_router sets ``metadata["_updated_language"]`` when the
        conversational agent detects a language that differs from the value
        loaded from the session at the start of the turn.  We write it back
        so the next inbound message gets a fresh (non-stale) hint.
        """
        updated = (metadata.get("_updated_language") or "").strip()
        if not updated or session_manager is None:
            return
        try:
            import time as _time_u
            _uname = username or str(metadata.get("username", ""))
            session_manager.set_session_metadata(
                chat_id,
                user_id,
                "last_detected_language",
                updated,
                is_group=is_group,
                username=_uname,
            )
            session_manager.set_session_metadata(
                chat_id,
                user_id,
                "last_detected_language_ts",
                _time_u.time(),
                is_group=is_group,
                username=_uname,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not persist updated language metadata: %s", exc)

    async def _handle_talk(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        metadata: dict,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """TALK mode — direct reply, no decorations, ≤3 lines, ≤2s."""
        # Universal safety net: even short messages that slipped past the
        # classifier may reference a named entity.  Inject an honesty hint
        # so the LLM never invents details it cannot verify.
        if HAS_CLASSIFIER and (
            _contains_title_marker(text)
            or _has_mid_sentence_cap(text)
            or _has_script_mixing(text)
            or _is_non_latin_dominant(text)
        ):
            metadata = {
                **metadata,
                "llm_hint": (
                    "If the user's message references a title, person, event, "
                    "or named entity that you cannot verify with certainty "
                    "from your training data, say so honestly rather than "
                    "inventing details."
                ),
            }
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            response = await self.on_message(
                channel="telegram",
                user_id=str(user_id),
                message=text,
                metadata=metadata,
            )
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if response:
            self._persist_updated_language(metadata, chat_id, user_id, session_manager, is_group)
            self._record_assistant_msg(
                session, session_manager, chat_id, user_id, response, is_group
            )
            debug_krow = (
                [{"text": "\ud83d\udd0d Debug", "callback_data": "dbg_trace"}]
                if self._is_debug_mode(user_id)
                else None
            )
            await self._send_response(
                chat_id,
                response,
                text,
                user_id=user_id,
                is_group=is_group,
                extra_krow=debug_krow,
            )

            await self._maybe_auto_continue(
                chat_id=chat_id,
                user_id=user_id,
                metadata=metadata,
                trigger_text=response,
                session=session,
                session_manager=session_manager,
                is_group=is_group,
            )

    async def _maybe_auto_continue(
        self,
        chat_id: int,
        user_id: int,
        metadata: dict,
        trigger_text: str,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """Run a single conservative continuation turn when policy allows."""
        try:
            from navig.core.continuation import (
                apply_busy_suppression,
                classify_continuation_state,
                consume_skip,
                get_busy_suppression,
                mark_continued,
                merge_policy,
                policy_from_context,
                should_auto_continue,
            )
            from navig.spaces.next_action import build_continuation_prompt
            from navig.store.runtime import get_runtime_store

            store = get_runtime_store()
            state = store.get_ai_state(user_id)
            if not state or state.get("mode") != "active":
                return

            context = state.get("context") or {}
            policy = policy_from_context(context)
            classifier_state, classifier_reason = classify_continuation_state(trigger_text)
            context = {
                **context,
                "continuation": {
                    **(context.get("continuation") or {}),
                    "last_classifier_state": classifier_state,
                    "last_classifier_reason": classifier_reason,
                },
            }
            context = apply_busy_suppression(
                context,
                classifier_state,
                classifier_reason,
                profile=policy.profile,
            )
            busy_active, busy_reason, busy_until = get_busy_suppression(context)
            if busy_active:
                context = {
                    **context,
                    "continuation": {
                        **(context.get("continuation") or {}),
                        "last_skip_reason": f"busy_suppressed:{busy_reason}",
                        "busy_until": busy_until,
                    },
                }
            should_run, reason = should_auto_continue(trigger_text, policy, context)

            if reason == "skip_next":
                context = consume_skip(context)
                store.set_ai_state(
                    user_id=user_id,
                    chat_id=chat_id,
                    mode="active",
                    persona=state.get("persona") or "assistant",
                    context=context,
                )
                return

            if not should_run:
                context = {
                    **context,
                    "continuation": {
                        **(context.get("continuation") or {}),
                        "last_skip_reason": reason,
                    },
                }
                store.set_ai_state(
                    user_id=user_id,
                    chat_id=chat_id,
                    mode="active",
                    persona=state.get("persona") or "assistant",
                    context=context,
                )
                return

            if policy.dry_run:
                store.set_ai_state(
                    user_id=user_id,
                    chat_id=chat_id,
                    mode="active",
                    persona=state.get("persona") or "assistant",
                    context=context,
                )
                await self.send_message(
                    chat_id,
                    "[dry-run] continuation would run here.",
                    parse_mode=None,
                )
                return

            preferred_space = (context.get("continuation") or {}).get("space", "")
            followup_prompt = build_continuation_prompt(preferred_space=preferred_space)

            typing_task = asyncio.create_task(self._keep_typing(chat_id))
            try:
                next_response = await self.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=followup_prompt,
                    metadata={**metadata, "auto_continuation_turn": True},
                )
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass  # expected during task cancellation
            if not next_response:
                return

            self._persist_updated_language(metadata, chat_id, user_id, session_manager, is_group)
            self._record_assistant_msg(
                session,
                session_manager,
                chat_id,
                user_id,
                next_response,
                is_group,
            )
            await self._send_response(
                chat_id,
                f"↪️ {next_response}",
                followup_prompt,
                user_id=user_id,
                is_group=is_group,
            )

            updated_context = mark_continued(context)
            updated_context = merge_policy(updated_context, skip_next=False)
            store.set_ai_state(
                user_id=user_id,
                chat_id=chat_id,
                mode="active",
                persona=state.get("persona") or "assistant",
                context=updated_context,
            )
        except Exception as exc:
            logger.debug("Auto continuation skipped due to error: %s", exc)

    async def _handle_reason(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        metadata: dict,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """REASON mode — send placeholder, fill with numbered CoT + bold conclusion."""
        placeholder = await self.send_message(chat_id, "🧠 Reasoning...", parse_mode=None)
        placeholder_id = (placeholder or {}).get("message_id")

        typing_task = asyncio.create_task(self._keep_typing(chat_id))

        # Entity enrichment: silently fetch web context for named title/person references
        # before sending to the LLM.  Uses the same entity-signal checks as
        # select_tools_for_text() but avoids a full ACT pipeline — just prepends
        # the top-3 search snippets so the model can ground its response in facts.
        _enriched_text = text
        if HAS_CLASSIFIER and (
            _contains_title_marker(text)
            or _has_mid_sentence_cap(text)
            or _has_script_mixing(text)
            or _is_non_latin_dominant(text)  # catch pure Cyrillic/Arabic/CJK entity queries
        ):
            try:
                from navig.tools import get_pipeline_registry as _get_pipeline_registry

                _reg = _get_pipeline_registry()
                _sr = await asyncio.wait_for(
                    _reg.run_tool("search", {"query": text}), timeout=3.0
                )
                if _sr.success and isinstance(_sr.output, dict):
                    _hits = _sr.output.get("results") or _sr.output.get("items") or []
                    if _hits:
                        _ctx = ["[Web context]"]
                        for _i, _h in enumerate(_hits[:3], 1):
                            _t = _h.get("title", "")
                            _s = _h.get("snippet", "")
                            _u = _h.get("url", "")
                            _line = (
                                f"{_i}. {_t}"
                                + (f" \u2014 {_s}" if _s else "")
                                + (f" ({_u})" if _u else "")
                            )
                            _ctx.append(_line)
                        _enriched_text = "\n".join(_ctx) + "\n\nUser message: " + text
            except Exception:
                pass  # silent \u2014 never surface search errors to the user in REASON mode

        try:
            response = await self.on_message(
                channel="telegram",
                user_id=str(user_id),
                message=_enriched_text,
                metadata=metadata,
            )
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if not response:
            return

        self._persist_updated_language(metadata, chat_id, user_id, session_manager, is_group)

        # Only append model footer in debug/trace mode — keep normal replies clean
        model_name = self._resolve_model_name(metadata) if self._is_debug_mode(user_id) else ""
        footer = f"\n\n<code>· {model_name}</code>" if model_name else ""
        final_text = f"{response}{footer}"
        final_text = self._strip_internal_tags(final_text)

        self._record_assistant_msg(session, session_manager, chat_id, user_id, response, is_group)

        keyboard = None
        if self._kb_builder:
            try:
                keyboard = self._kb_builder.build(
                    ai_response=final_text,
                    user_message=text,
                    message_id=placeholder_id or 0,
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if placeholder_id:
            try:
                await self.edit_message(
                    chat_id,
                    placeholder_id,
                    final_text,
                    parse_mode=None,
                    keyboard=keyboard,
                )
                return
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        await self._send_response(chat_id, final_text, text, user_id=user_id, is_group=is_group)

    async def _handle_code(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        metadata: dict,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """CODE mode — coder tier, fenced code block, model footer."""
        metadata = dict(metadata)
        if "tier_override" not in metadata:
            metadata["tier_override"] = "coder_big"

        intro = await self.send_message(
            chat_id, "🔧 [CODER] Scaffolding solution...", parse_mode=None
        )
        intro_id = (intro or {}).get("message_id")

        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            response = await self.on_message(
                channel="telegram",
                user_id=str(user_id),
                message=text,
                metadata=metadata,
            )
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if not response:
            return

        self._persist_updated_language(metadata, chat_id, user_id, session_manager, is_group)

        if self._is_debug_mode(user_id):
            model_name = self._resolve_model_name(metadata)
            suffix = f"\n\n✅ Done · Model: {model_name}" if model_name else "\n\n✅ Done"
        else:
            suffix = "\n\n✅ Done"
        final_text = f"{response}{suffix}"

        self._record_assistant_msg(session, session_manager, chat_id, user_id, response, is_group)

        if intro_id:
            try:
                await self.edit_message(chat_id, intro_id, final_text, parse_mode=None)
                return
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        await self._send_response(chat_id, final_text, text, user_id=user_id, is_group=is_group)

    async def _handle_act(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        metadata: dict,
        session,
        session_manager,
        is_group: bool,
    ) -> None:
        """ACT mode — cinematic status pipeline with live tool execution."""

        # Send the initial sentinel message
        sentinel = await self.send_message(
            chat_id,
            "⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛ Initializing...",
            parse_mode=None,
        )
        sentinel_id = (sentinel or {}).get("message_id")
        if not sentinel_id:
            # Fallback: no message ID → degrade to REASON mode
            await self._handle_reason(
                text, chat_id, user_id, metadata, session, session_manager, is_group
            )
            return

        renderer = StatusRenderer(self, chat_id, sentinel_id)

        tool_results = []
        tool_names_run: list[str] = []
        tool_errors: list[str] = []

        # ── Step 1: classify which tools to call ──
        try:
            from navig.tools import get_pipeline_registry

            registry = get_pipeline_registry()

            tools_to_call = select_tools_for_text(text)
            url = extract_url(text)

            await renderer.update(
                "Connecting to target...",
                detail=f"{len(tools_to_call)} tool(s) queued",
                progress=1,
                icon="🔗",
            )

            total = len(tools_to_call)
            for idx, tool_name in enumerate(tools_to_call):
                # Build args per tool
                args: dict = {}
                if tool_name in ("site_check", "web_fetch", "browser_fetch"):
                    if url:
                        args["url"] = url
                    else:
                        await renderer.warn(tool_name, "no URL found in message")
                        tool_errors.append(tool_name)
                        continue
                elif tool_name == "search":
                    args["query"] = text
                elif tool_name == "code_exec_sandbox":
                    import re as _re

                    code_m = _re.search(r"`{3}[\w]*\n([\s\S]+?)\n`{3}", text) or _re.search(
                        r"`([^`]+)`", text
                    )
                    if code_m:
                        args["code"] = code_m.group(1)
                        args["language"] = "python"
                    else:
                        await renderer.warn(tool_name, "no code block found")
                        tool_errors.append(tool_name)
                        continue

                progress_val = 2 + round((idx + 1) / total * 6)

                async def _status(step, detail="", progress=0, _tn=tool_name):
                    await renderer.update(step, detail=detail, progress=progress, icon="⚙️")

                result = await registry.run_tool(tool_name, args, on_status=_status)
                tool_names_run.append(tool_name)

                if result.success:
                    tool_results.append(result)
                    await renderer.update(
                        f"{tool_name} complete",
                        detail=self._summarize_tool_result(result),
                        progress=progress_val,
                        icon="✅",
                    )
                else:
                    await renderer.warn(tool_name, result.error or "unknown error")
                    tool_errors.append(tool_name)

        except Exception as tool_exc:
            logger.exception("Tool pipeline error: %s", tool_exc)
            await renderer.warn("tool_pipeline", str(tool_exc))

        # ── Step 2: call LLM with tool context ──
        await renderer.update("Analyzing results...", progress=8, icon="🧠")

        tool_context = self._build_act_tool_context(tool_results)

        augmented_message = text
        if tool_context:
            augmented_message = (
                f"{text}\n\n"
                "[Website analysis instructions]\n"
                "Use the tool outputs to provide concrete website intelligence, not generic uptime text.\n"
                "Return concise bullets with these headings in this order:\n"
                "- Website\n"
                "- What it does\n"
                "- Key sections and features\n"
                "- Technical signals (status, latency, TLS, rendering clues)\n"
                "- Notable issues or unknowns\n"
                "If a field is missing, explicitly say 'Not detected'.\n\n"
                f"[Tool results]\n{tool_context.strip()}"
            )

        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            llm_response = await self.on_message(
                channel="telegram",
                user_id=str(user_id),
                message=augmented_message,
                metadata=metadata,
            )
        except Exception as llm_exc:
            llm_response = f"LLM call failed: {llm_exc}"
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # ── Step 3: finalize ──
        self._persist_updated_language(metadata, chat_id, user_id, session_manager, is_group)
        model_name = self._resolve_model_name(metadata)

        # Build structured conclusion
        conclusion: dict = {}
        for r in tool_results:
            if r.name == "site_check" and isinstance(r.output, dict):
                out = r.output
                conclusion["Status"] = (
                    f"HTTP {out.get('status_code')} · {'online' if out.get('online') else 'offline'}"
                )
                conclusion["Latency"] = f"{out.get('latency_ms')}ms"
                if out.get("cert_expiry"):
                    conclusion["TLS cert"] = out["cert_expiry"]
            elif r.name == "search" and isinstance(r.output, dict):
                results = r.output.get("results", [])
                if results:
                    conclusion["Top result"] = results[0].get("title", "")[:60]
                    conclusion["URL"] = results[0].get("url", "")[:60]

        # Analysis is sent as a formatted follow-up message after the card (see Step 4)

        if tool_errors:
            conclusion["Skipped"] = ", ".join(tool_errors)

        await renderer.finalize(
            conclusion=conclusion,
            title="RESULT",
            n_tools=len(tool_names_run),
            model_name=model_name,
        )

        # ── Step 4: Visual Artifacts ──
        for r in tool_results:
            if isinstance(r.output, dict) and "_screenshot" in r.output:
                caption_text = r.output.get("url", r.name)
                try:
                    await self.send_photo(
                        chat_id=chat_id,
                        photo_data=r.output["_screenshot"],
                        caption=f"📸 {caption_text}",
                    )
                except Exception as _ep:
                    logger.warning("Failed to send screenshot artifact: %s", _ep)

        if llm_response:
            # Send full analysis as a properly-formatted HTML message
            await self.send_message(
                chat_id, TelegramChannel._md_to_html(llm_response), parse_mode="HTML"
            )
            self._record_assistant_msg(
                session, session_manager, chat_id, user_id, llm_response, is_group
            )

    def _summarize_tool_result(self, result) -> str:
        """Compact, tool-specific summary for ACT step progress lines."""
        if not result.success:
            return f"⚠️ {result.error or 'unknown error'}"

        output = result.output
        if isinstance(output, dict):
            if result.name == "site_check":
                status = output.get("status_code")
                latency = output.get("latency_ms")
                redirects = output.get("redirects")
                url = str(output.get("final_url") or output.get("url") or "")
                parts = [
                    f"url: {url[:48]}" if url else "",
                    f"status: {status}" if status is not None else "",
                    f"latency: {latency}ms" if latency is not None else "",
                    f"redirects: {redirects}" if redirects is not None else "",
                ]
                return " · ".join(p for p in parts if p)[:120]

            if result.name in {"browser_fetch", "web_fetch"}:
                url = str(output.get("url") or "")
                method = output.get("method")
                chars = output.get("chars")
                status = output.get("status_code")
                parts = [
                    f"url: {url[:48]}" if url else "",
                    f"method: {method}" if method else "",
                    f"status: {status}" if status is not None else "",
                    f"chars: {chars}" if chars is not None else "",
                ]
                return " · ".join(p for p in parts if p)[:120]

            if result.name == "search":
                results = output.get("results") if isinstance(output.get("results"), list) else []
                top = ""
                if results and isinstance(results[0], dict):
                    top = str(results[0].get("title") or "")
                summary = f"results: {len(results)}"
                if top:
                    summary += f" · top: {top[:56]}"
                return summary[:120]

        return result.summary()[:120]

    def _build_act_tool_context(self, tool_results: list) -> str:
        """Build focused ACT context for LLM synthesis without noisy raw blobs."""
        lines: list[str] = []
        for result in tool_results:
            output = result.output
            if not isinstance(output, dict):
                lines.append(f"{result.name}.output={str(output)[:200]}")
                continue

            if result.name == "site_check":
                keys = [
                    "url",
                    "final_url",
                    "status_code",
                    "online",
                    "latency_ms",
                    "redirects",
                    "cert_expiry",
                ]
                for key in keys:
                    value = output.get(key)
                    if value not in (None, ""):
                        lines.append(f"site_check.{key}={value}")
                continue

            if result.name in {"browser_fetch", "web_fetch"}:
                keys = ["url", "method", "status_code", "elapsed_ms", "chars", "cached"]
                for key in keys:
                    value = output.get(key)
                    if value not in (None, ""):
                        lines.append(f"{result.name}.{key}={value}")

                content = str(output.get("content") or "").strip()
                if content:
                    compact = re.sub(r"\s+", " ", content)
                    snippet = self._trim_preview(compact, max_chars=900)
                    lines.append(f"{result.name}.content_snippet={snippet}")
                continue

            if result.name == "search":
                results = output.get("results") if isinstance(output.get("results"), list) else []
                lines.append(f"search.count={len(results)}")
                for index, item in enumerate(results[:3], start=1):
                    if not isinstance(item, dict):
                        continue
                    title = self._trim_preview(str(item.get("title") or ""), max_chars=120)
                    url = self._trim_preview(str(item.get("url") or ""), max_chars=180)
                    snippet = self._trim_preview(str(item.get("snippet") or ""), max_chars=220)
                    if title:
                        lines.append(f"search.{index}.title={title}")
                    if url:
                        lines.append(f"search.{index}.url={url}")
                    if snippet:
                        lines.append(f"search.{index}.snippet={snippet}")
                continue

            for key, value in output.items():
                if str(key).startswith("_") or value in (None, ""):
                    continue
                lines.append(f"{result.name}.{key}={self._trim_preview(str(value), max_chars=220)}")

        return "\n".join(lines)

    @staticmethod
    def _trim_preview(text: str, max_chars: int = 520) -> str:
        """Trim text on word boundary and add ellipsis when needed."""
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= max_chars:
            return cleaned

        trimmed = cleaned[:max_chars].rstrip()
        split_at = trimmed.rfind(" ")
        if split_at >= int(max_chars * 0.6):
            trimmed = trimmed[:split_at].rstrip()
        return f"{trimmed}…"

    @staticmethod
    def _md_to_html(text: str) -> str:
        """Convert lightweight LLM markdown to Telegram HTML (parse_mode='HTML').

        Handles: **bold**, *italic*, # headings, * / - / + bullet lists.
        Unrecognised patterns are left as plain text.
        """
        import html as _html

        # 1. Escape HTML special chars so we can safely inject tags
        escaped = _html.escape(str(text or ""), quote=False)

        lines_out: list[str] = []
        for line in escaped.split("\n"):
            stripped = line.lstrip()

            # ATX headings  # / ## / ###
            heading_m = re.match(r"^(#{1,3})\s+(.*)", stripped)
            if heading_m:
                lines_out.append(f"<b>{heading_m.group(2).strip()}</b>")
                continue

            # Bullet lines  * text  /  - text
            if re.match(r"^[*\-]\s+", stripped):
                lines_out.append(f"\u2022 {stripped[2:].strip()}")
                continue

            # Sub-bullet lines  + text  (LLM often uses + for nested items)
            if re.match(r"^\+\s+", stripped):
                lines_out.append(f"  \u25e6 {stripped[2:].strip()}")
                continue

            lines_out.append(line)

        result = "\n".join(lines_out)

        # Inline **bold** → <b>bold</b>  (non-greedy, no newlines inside)
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)
        # Inline *italic* (after bold consumed, so single * remaining)
        result = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", result)
        # Inline __bold__ alternative
        result = re.sub(r"__(.+?)__", r"<b>\1</b>", result)

        # Collapse 3+ blank lines → 2
        result = re.sub(r"\n{3,}", "\n\n", result)

        # Strip only leading/trailing newlines — preserve sub-bullet indentation
        return result.strip("\n")

    # ── Shared helpers ──────────────────────────────────────────────────────

    def _record_assistant_msg(
        self,
        session,
        session_manager,
        chat_id: int,
        user_id: int,
        text: str,
        is_group: bool,
    ) -> None:
        """Record assistant reply in session history (best-effort)."""
        if HAS_SESSIONS and session and session_manager:
            try:
                session_manager.add_assistant_message(
                    chat_id=chat_id,
                    user_id=user_id,
                    text=text,
                    is_group=is_group,
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def _handle_photo_vision(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool,
        message: dict,
    ) -> None:
        """Handle a photo message by routing it through the vision model.

        Resolves the best vision provider/model from session overrides → config
        → active provider → any connected provider. Downloads the largest photo
        variant from Telegram, sends it to the resolved vision model, and posts
        the description back.
        """
        import base64

        caption = message.get("caption", "") or ""

        # Resolve vision model
        session_overrides: dict = {}
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session_overrides = sm.get_all_session_overrides(chat_id, user_id, is_group=is_group)
        except Exception:  # noqa: BLE001
            pass

        try:
            from navig.providers.discovery import get_vision_api_format, resolve_vision_model

            vision = resolve_vision_model(session_overrides)
        except Exception:  # noqa: BLE001
            vision = None

        if not vision:
            await self.send_message(
                chat_id,
                "👁 No vision model available. "
                "Use /provider_vision to pick one, or connect a vision-capable provider.",
                parse_mode="HTML",
            )
            return

        provider_id, model_name, reason = vision
        api_format = get_vision_api_format(provider_id)

        # Get the largest photo variant
        photos = message.get("photo", [])
        if not photos:
            await self.send_message(chat_id, "⚠️ Could not read photo.", parse_mode=None)
            return
        best_photo = max(photos, key=lambda p: p.get("file_size", 0))
        file_id = best_photo.get("file_id")
        if not file_id:
            await self.send_message(chat_id, "⚠️ Could not read photo.", parse_mode=None)
            return

        # Signal we're processing
        try:
            await self._api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:  # noqa: BLE001
            pass

        # Download file from Telegram
        try:
            file_info = await self._api_call("getFile", {"file_id": file_id})
            if not file_info:
                await self.send_message(
                    chat_id, "⚠️ Could not retrieve photo from Telegram.", parse_mode=None
                )
                return
            file_path = file_info.get("file_path", "")
            if not file_path:
                await self.send_message(
                    chat_id, "⚠️ Could not retrieve photo from Telegram.", parse_mode=None
                )
                return

            dl_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            if not self._session:
                await self.send_message(
                    chat_id, "⚠️ Internal error: no HTTP session.", parse_mode=None
                )
                return

            async with self._session.get(dl_url) as dl_resp:
                if dl_resp.status != 200:
                    await self.send_message(chat_id, "⚠️ Failed to download photo.", parse_mode=None)
                    return
                image_bytes = await dl_resp.read()
        except Exception as exc:
            logger.warning("Photo download failed for chat %s: %s", chat_id, exc)
            await self.send_message(chat_id, "⚠️ Failed to download photo.", parse_mode=None)
            return

        ocr_text = self._extract_photo_ocr_text(image_bytes)

        # Build the vision API call based on provider format
        b64_image = base64.b64encode(image_bytes).decode()
        prompt = caption or (
            "Describe this image concisely (2-4 sentences). "
            "Focus on what's depicted, key subjects, setting, "
            "and any notable details. Be factual."
        )

        description = await self._call_vision_api(
            provider_id, model_name, api_format, b64_image, prompt
        )

        reply = self._compose_photo_analysis_reply(model_name, description, ocr_text)
        if reply:
            await self.send_message(chat_id, reply, parse_mode="HTML")
        else:
            await self.send_message(
                chat_id,
                "⚠️ Vision analysis failed — the model could not process this image.",
                parse_mode=None,
            )

    def _extract_photo_ocr_text(self, image_bytes: bytes) -> str | None:
        """Best-effort OCR text extraction for Telegram photos."""
        try:
            from navig.core.ocr import extract_ocr_text_from_image_bytes

            return extract_ocr_text_from_image_bytes(image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Photo OCR extraction failed: %s", exc)
            return None

    def _compose_photo_analysis_reply(
        self,
        model_name: str,
        description: str | None,
        ocr_text: str | None,
    ) -> str:
        """Build a safe HTML reply combining vision summary and OCR snippet."""
        parts: list[str] = []
        if description:
            parts.append(html.escape(description.strip()))

        if ocr_text:
            snippet = ocr_text.strip()
            if len(snippet) > 700:
                snippet = snippet[:700] + "…"
            parts.append(f"\n📝 <b>OCR</b>\n<code>{html.escape(snippet)}</code>")

        if not parts:
            return ""

        short_model = model_name.split("/")[-1].split(":")[-1]
        parts.append(f"\n\n<i>👁 {html.escape(short_model)}</i>")
        return "\n".join(parts)

    async def _call_vision_api(
        self,
        provider_id: str,
        model_name: str,
        api_format: str,
        b64_image: str,
        prompt: str,
    ) -> str | None:
        """Call a vision model API and return the text description.

        Supports OpenAI-compatible, Anthropic, and Google formats.
        """

        try:
            import httpx
        except ImportError:
            logger.warning("httpx not available for vision API call")
            return None

        timeout = httpx.Timeout(60.0, connect=10.0)

        try:
            if api_format == "anthropic":
                return await self._call_vision_anthropic(model_name, b64_image, prompt, timeout)
            if api_format == "google":
                return await self._call_vision_google(model_name, b64_image, prompt, timeout)
            # Default: OpenAI-compatible
            return await self._call_vision_openai(
                provider_id, model_name, b64_image, prompt, timeout
            )
        except Exception as exc:
            logger.warning("Vision API call failed (%s/%s): %s", provider_id, model_name, exc)
            return None

    async def _call_vision_openai(
        self,
        provider_id: str,
        model_name: str,
        b64_image: str,
        prompt: str,
        timeout,
    ) -> str | None:
        """OpenAI-compatible vision call (works for groq, xai, nvidia, etc.)."""
        import os as _os

        import httpx

        # Resolve API key and endpoint based on provider
        _PROVIDER_ENDPOINTS: dict[str, tuple[str, list[str]]] = {
            "openai": (
                "https://api.openai.com/v1/chat/completions",
                ["OPENAI_API_KEY"],
            ),
            "groq": (
                "https://api.groq.com/openai/v1/chat/completions",
                ["GROQ_API_KEY"],
            ),
            "xai": (
                "https://api.x.ai/v1/chat/completions",
                ["XAI_API_KEY", "GROK_KEY"],
            ),
            "nvidia": (
                "https://integrate.api.nvidia.com/v1/chat/completions",
                ["NVIDIA_API_KEY", "NIM_API_KEY"],
            ),
            "openrouter": (
                "https://openrouter.ai/api/v1/chat/completions",
                ["OPENROUTER_API_KEY"],
            ),
            "github_models": (
                "https://models.inference.ai.azure.com/chat/completions",
                ["GITHUB_TOKEN"],
            ),
            "mistral": (
                "https://api.mistral.ai/v1/chat/completions",
                ["MISTRAL_API_KEY"],
            ),
            "cerebras": (
                "https://api.cerebras.ai/v1/chat/completions",
                ["CEREBRAS_API_KEY"],
            ),
            "github_copilot": (
                "https://api.githubcopilot.com/chat/completions",
                ["GITHUB_COPILOT_TOKEN"],
            ),
        }

        endpoint, env_keys = _PROVIDER_ENDPOINTS.get(
            provider_id,
            ("https://api.openai.com/v1/chat/completions", ["OPENAI_API_KEY"]),
        )

        api_key = ""
        for k in env_keys:
            api_key = _os.environ.get(k, "")
            if api_key:
                break

        # Fallback to vault
        if not api_key:
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(provider_id)
                if manifest and manifest.vault_keys:
                    from navig.vault import get_vault

                    vault = get_vault()
                    for vk in manifest.vault_keys:
                        api_key = vault.get_secret(vk) or ""
                        if api_key:
                            break
            except Exception:  # noqa: BLE001
                pass

        if not api_key:
            logger.warning("No API key found for vision provider %s", provider_id)
            return None

        payload = {
            "model": model_name,
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}",
                                "detail": "auto",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()

        return (
            (data or {}).get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        ) or None

    async def _call_vision_anthropic(
        self,
        model_name: str,
        b64_image: str,
        prompt: str,
        timeout,
    ) -> str | None:
        """Anthropic Claude vision API call."""
        import os as _os

        import httpx

        api_key = _os.environ.get("ANTHROPIC_API_KEY") or _os.environ.get("CLAUDE_API_KEY", "")
        if not api_key:
            try:
                from navig.providers.registry import get_provider as _get_prov
                from navig.vault import get_vault

                vault = get_vault()
                _manifest = _get_prov("anthropic")
                for _vk in (
                    _manifest.vault_keys
                    if _manifest
                    else ["anthropic/api-key", "anthropic/api_key"]
                ):
                    api_key = vault.get_secret(_vk) or ""
                    if api_key:
                        break
            except Exception:  # noqa: BLE001
                pass
        if not api_key:
            return None

        payload = {
            "model": model_name,
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()

        blocks = data.get("content", [])
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return " ".join(texts).strip() or None

    async def _call_vision_google(
        self,
        model_name: str,
        b64_image: str,
        prompt: str,
        timeout,
    ) -> str | None:
        """Google Gemini vision API call."""
        import os as _os

        import httpx

        api_key = _os.environ.get("GOOGLE_API_KEY") or _os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            try:
                from navig.providers.registry import get_provider as _get_prov
                from navig.vault import get_vault

                vault = get_vault()
                _manifest = _get_prov("google")
                for _vk in (
                    _manifest.vault_keys
                    if _manifest
                    else ["google/api-key", "google/api_key", "gemini/api-key"]
                ):
                    api_key = vault.get_secret(_vk) or ""
                    if api_key:
                        break
            except Exception:  # noqa: BLE001
                pass
        if not api_key:
            return None

        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": b64_image,
                            }
                        },
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 500},
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                endpoint,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()

        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            texts = [p.get("text", "") for p in parts if "text" in p]
            return " ".join(texts).strip() or None
        return None

    async def _transcribe_voice_message(
        self,
        chat_id: int,
        is_group: bool,
        voice_data: dict | None,
    ) -> tuple[str | None, str]:
        """Download a Telegram voice message, transcribe via STT (Deepgram/Whisper),
        echo the transcript to the chat, and return (transcript, detected_lang).
        Returns (None, "") on failure — a friendly error has already been sent.
        """
        import os as _os
        import tempfile
        from pathlib import Path

        file_id = voice_data.get("file_id") if voice_data else None
        if not file_id:
            await self.send_message(chat_id, "🎙️ Couldn't read the voice message.", parse_mode=None)
            return None, ""

        # ── Resolve which STT provider to use based on available keys ────────
        # Priority: Deepgram (fastest) → Whisper API → local Whisper (offline)
        stt_provider = None
        fallback_providers: list = []

        dg_key = _os.environ.get("DEEPGRAM_KEY") or _os.environ.get("DEEPGRAM_API_KEY")
        if not dg_key:
            try:
                from navig.vault import get_vault as _gv2

                dg_key = _gv2().get_secret("deepgram/api-key") or None
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if dg_key:
            stt_provider = _STTProvider.DEEPGRAM

        oai_key = _os.environ.get("OPENAI_API_KEY")
        if not oai_key:
            try:
                from navig.vault import get_vault as _gv2

                oai_key = _gv2().get_secret("openai/api-key") or None
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if oai_key:
            if stt_provider is None:
                stt_provider = _STTProvider.WHISPER_API
            else:
                fallback_providers.append(_STTProvider.WHISPER_API)

        # Local Whisper — works offline, no API key required
        try:
            from navig.voice.stt import whisper_local_available as _wla

            _has_local_whisper = _wla()
        except Exception:
            _has_local_whisper = False
        if _has_local_whisper:
            if stt_provider is None:
                stt_provider = _STTProvider.WHISPER_LOCAL
            else:
                fallback_providers.append(_STTProvider.WHISPER_LOCAL)

        if stt_provider is None:
            await self.send_message(
                chat_id,
                "🎙️ <b>Voice transcription not configured.</b>\n\n"
                "Add any of the following to <code>~/.navig/.env</code> and restart:\n"
                "• <code>DEEPGRAM_KEY=&lt;key&gt;</code> — blazing fast, recommended\n"
                "• <code>OPENAI_API_KEY=&lt;key&gt;</code> — Whisper API fallback\n"
                "• <code>pip install openai-whisper</code> — offline, no key needed",
                parse_mode="HTML",
            )
            return None, ""

        tmp_path: str | None = None
        _recording_task: asyncio.Task | None = None
        try:
            # Signal immediately that we're processing audio — closest Bot API
            # equivalent to a read receipt for voice messages.
            await self._api_call("sendChatAction", {"chat_id": chat_id, "action": "record_voice"})
            _recording_task = asyncio.create_task(self._keep_recording(chat_id))

            # Ask Telegram for the file path
            file_info = await self._api_call("getFile", {"file_id": file_id})
            if not file_info:
                await self.send_message(
                    chat_id, "🎙️ Couldn't retrieve the voice file.", parse_mode=None
                )
                return None, ""
            file_path = file_info.get("file_path", "")
            if not file_path:
                await self.send_message(
                    chat_id, "🎙️ Couldn't retrieve the voice file.", parse_mode=None
                )
                return None, ""

            # Download the OGG/OPUS file
            dl_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            if not self._session:
                await self.send_message(
                    chat_id, "🎙️ Internal error: no HTTP session.", parse_mode=None
                )
                return None, ""

            async with self._session.get(dl_url) as dl_resp:
                if dl_resp.status != 200:
                    await self.send_message(
                        chat_id, "🎙️ Failed to download voice message.", parse_mode=None
                    )
                    return None, ""
                audio_bytes = await dl_resp.read()

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(audio_bytes)

            # Transcribe with detected provider (+ any fallbacks).
            # detect_language=True tells every provider to auto-detect the
            # spoken language rather than forcing "en", so Russian/Chinese/etc.
            # audio is transcribed as-is (not translated to English).
            stt_cfg = _STTConfig(
                provider=stt_provider,
                fallback_providers=fallback_providers,
                detect_language=True,
            )
            result = await _STT(stt_cfg).transcribe(Path(tmp_path))

            if not result.success or not result.text:
                # Map internal error strings to clean, user-friendly messages
                raw_err = result.error or ""
                if "whisper not installed" in raw_err or "No module named 'whisper'" in raw_err:
                    user_msg = (
                        "🎙️ Transcription failed: local Whisper is not installed.\n"
                        "Run `pip install openai-whisper` on the server, or add a "
                        "`DEEPGRAM_KEY` / `OPENAI_API_KEY` to `~/.navig/.env`."
                    )
                elif "API key" in raw_err or "not set" in raw_err or "not configured" in raw_err:
                    user_msg = "🎙️ Transcription failed: no STT API key configured — type your message instead."
                elif "timeout" in raw_err.lower():
                    user_msg = "🎙️ Transcription timed out — try a shorter clip or type it out."
                elif "too large" in raw_err:
                    user_msg = f"🎙️ Audio file too large — {raw_err.split(':', 1)[-1].strip()}"
                else:
                    user_msg = "🎙️ Couldn't transcribe audio — try again or type it out."
                await self.send_message(chat_id, user_msg, parse_mode=None)
                return None, ""

            transcript = result.text.strip()

            # Echo transcription — add action cards when debug trace is on
            # This also doubles as a read-receipt so the voice dot clears visually
            heard_kb = None
            _user_from_voice = getattr(voice_data, "from_user_id", None)
            # We don't have user_id here directly — determine from chat_id
            _debug_active = any(
                uid in getattr(self, "_debug_users", set()) for uid in self.allowed_users
            )
            if _debug_active:
                heard_kb = [
                    [
                        {"text": "💡 Process", "callback_data": "heard_process"},
                        {"text": "🔁 Re-transcribe", "callback_data": "heard_retry"},
                        {"text": "📝 Edit", "callback_data": "heard_edit"},
                    ],
                ]
            await self.send_message(
                chat_id,
                f"🎙️ <b>Heard:</b> <i>{html.escape(transcript)}</i>",
                parse_mode="HTML",
                keyboard=heard_kb,
            )
            detected_lang = (result.language or "") if hasattr(result, "language") else ""
            return transcript, detected_lang

        except Exception as e:
            logger.error("Voice transcription error: %s", e)
            await self.send_message(
                chat_id,
                "🎙️ Something went wrong processing your voice message — please try again.",
                parse_mode=None,
            )
            return None, ""
        finally:
            if _recording_task and not _recording_task.done():
                _recording_task.cancel()
                try:
                    await _recording_task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown
            if tmp_path and _os.path.exists(tmp_path):
                try:
                    _os.remove(tmp_path)
                except OSError:
                    pass  # best-effort cleanup

    async def _transcribe_audio_file(
        self,
        chat_id: int,
        file_id: str,
        is_voice: bool = False,
        task_view: object | None = None,
    ) -> str | None:
        """Delegate audio-file STT transcription to TelegramVoiceMixin.

        BUG-22 fix: TelegramChannel does not inherit TelegramVoiceMixin; this
        stub makes ``self.channel._transcribe_audio_file(...)`` calls from
        ``telegram_keyboards.py`` (transcribe / lang actions) work.
        """
        try:
            from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

            return await TelegramVoiceMixin._transcribe_audio_file(
                self, chat_id, file_id, is_voice=is_voice, task_view=task_view
            )
        except Exception as _e:  # noqa: BLE001
            import logging

            logging.getLogger("navig").warning("_transcribe_audio_file delegation failed: %s", _e)
            return None

    async def _maybe_send_voice(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool,
        text: str,
    ) -> None:
        """Synthesize a TTS voice reply and send it as a Telegram voice message.
        Non-fatal: the text reply has already been delivered before this is called.
        Skipped for group chats and users who have opted out via /voiceoff.
        """
        if not HAS_VOICE or is_group:
            return

        # Honour per-user voice preference
        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                session = sm.get_session(chat_id, user_id, is_group=False)
                if session is not None and not session.voice_enabled:
                    return
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        tts_text = self._prepare_for_tts(text)
        if not tts_text:
            return

        tts_result = None
        try:
            tts = _TTS(_TTSConfig(provider=_TTSProvider.GOOGLE_CLOUD))
            tts_result = await tts.synthesize(tts_text)
            if not tts_result.success:
                logger.warning("TTS synthesis failed (non-fatal): %s", tts_result.error)
                return

            audio_data: bytes | None = tts_result.audio_data
            if audio_data is None and tts_result.audio_path and tts_result.audio_path.exists():
                audio_data = tts_result.audio_path.read_bytes()

            if not audio_data:
                logger.warning("TTS returned empty audio (non-fatal)")
                return

            await self.send_voice(chat_id, audio_data)

        except Exception as e:
            logger.warning("Voice reply failed (non-fatal): %s", e)
        finally:
            try:
                if tts_result and tts_result.audio_path and tts_result.audio_path.exists():
                    tts_result.audio_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    def _prepare_for_tts(self, text: str, max_chars: int = 500) -> str:
        """Strip markdown and code blocks from text before sending to TTS."""
        import re

        # Remove fenced code blocks
        text = re.sub(r"```[\s\S]*?```", "", text)
        # Remove inline code
        text = re.sub(r"`[^`]+`", "", text)
        # Remove URLs
        text = re.sub(r"https?://\S+", "", text)
        # Strip bold/italic/underline markers
        text = re.sub(r"[*_~]{1,3}", "", text)
        # Remove markdown headers
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Truncate at word boundary
        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "…"
        return text

    async def _send_response(
        self,
        chat_id: int,
        response: str,
        original_text: str = "",
        user_id: int = 0,
        is_group: bool = False,
        extra_krow: list | None = None,
    ) -> None:
        """Send a response with template limits, optional keyboard, and voice reply."""
        # Strip internal LLM reasoning tags before any further processing
        response = self._strip_internal_tags(response)
        # Convert standard Markdown (**bold**, ## heading) to Telegram HTML
        response = TelegramChannel._md_to_html(response)
        parts = None
        if HAS_TEMPLATES:
            try:
                from navig.agent.proactive.user_state import get_user_state_tracker

                verbosity = get_user_state_tracker().get_preference("verbosity", "normal")
            except Exception:
                verbosity = "normal"
            fmt = enforce_response_limits(response, verbosity=verbosity)
            response = fmt.text
            parts = fmt.parts

        keyboard = None
        if self._kb_builder:
            try:
                keyboard = self._kb_builder.build(
                    ai_response=response,
                    user_message=original_text,
                    message_id=0,
                )
            except Exception as kb_err:
                logger.debug("Keyboard build failed: %s", kb_err)
        if extra_krow:
            if keyboard and isinstance(keyboard, dict) and "inline_keyboard" in keyboard:
                keyboard["inline_keyboard"].append(extra_krow)
            else:
                keyboard = {"inline_keyboard": [extra_krow]}

        if parts and len(parts) > 1:
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                await self._send_html_with_fallback(
                    chat_id,
                    part,
                    keyboard=keyboard if is_last else None,
                )
        elif len(response) > 4000:
            chunks = [response[i : i + 4000] for i in range(0, len(response), 4000)]
            for i, chunk in enumerate(chunks):
                is_last = i == len(chunks) - 1
                await self._send_html_with_fallback(
                    chat_id,
                    chunk,
                    keyboard=keyboard if is_last else None,
                )
        else:
            await self._send_html_with_fallback(chat_id, response, keyboard=keyboard)

        # Voice reply — non-fatal; user already has the text
        await self._maybe_send_voice(chat_id, user_id, is_group, response)

    def _is_debug_mode(self, user_id: int) -> bool:
        """Return True if user has activated /trace debug on."""
        return user_id in getattr(self, "_debug_users", set())

    def _resolve_model_name(self, metadata: dict) -> str:
        """Best-effort: resolve the active model name for footer display."""
        # 1. Gateway may have set it during routing
        model = metadata.get("resolved_model", "")
        if model:
            return model

        # 2. LLMModeRouter
        try:
            from navig.llm_router import get_llm_router

            router = get_llm_router()
            if router:
                tier = metadata.get("tier_override", "")
                mode_map = {
                    "small": "small_talk",
                    "big": "big_tasks",
                    "coder_big": "coding",
                    "": "big_tasks",
                }
                mode_name = mode_map.get(tier, "big_tasks")
                mc = router.modes.get_mode(mode_name)
                if mc:
                    return f"{mc.provider}:{mc.model}"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return ""

    @staticmethod
    def _fmt_provider(name: str) -> str:
        """Normalise provider name for display — renames legacy aliases to canonical."""
        return {
            "forge_copilot": "bridge",
            "bridge_copilot": "bridge",
            "mcp_bridge": "bridge",
        }.get(name, name)

    async def _handle_models_command(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ):
        """Delegate to the canonical model routing screen implementation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_models_command(
            self,
            chat_id,
            user_id=user_id,
            message_id=message_id,
            text=text,
        )

    async def _handle_status(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Delegate to canonical status screen handler used by menu navigation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_status(
            self,
            chat_id,
            user_id=user_id,
            message_id=message_id,
        )

    async def _handle_ping(
        self,
        chat_id: int,
        user_id: int = 0,
    ) -> None:
        """Delegate to canonical heartbeat handler (/ping)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_ping(self, chat_id, user_id=user_id)

    async def _handle_ai_command(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        message_id: int | None = None,
    ) -> None:
        """Delegate to canonical AI tier-picker handler (/ai)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_ai_command(
            self,
            chat_id,
            user_id=user_id,
            text=text,
            message_id=message_id,
        )

    async def _handle_spaces(
        self,
        chat_id: int,
        message_id: int | None = None,
    ) -> None:
        """Delegate to canonical spaces screen handler used by menu navigation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_spaces(
            self,
            chat_id,
            message_id=message_id,
        )

    async def _handle_intake(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        message_id: int | None = None,
    ) -> None:
        """Delegate to canonical intake handler used by menu navigation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_intake(
            self,
            chat_id,
            user_id,
            text=text,
            message_id=message_id,
        )

    async def _handle_settings_hub(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Delegate to canonical settings hub handler used by menu navigation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_settings_hub(
            self,
            chat_id,
            user_id,
            is_group=is_group,
            message_id=message_id,
        )

    async def _probe_bridge_grid(self) -> tuple[bool, str]:
        """Delegate bridge probe helper required by provider/model screens."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._probe_bridge_grid(self)

    def _provider_vault_validation_status(self, manifest) -> tuple[bool, bool]:
        """Delegate vault-key validation used by provider list rendering."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._provider_vault_validation_status(self, manifest)

    def _get_vault(self):
        """Delegate vault access used by provider verification helpers."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._get_vault(self)

    def _list_enabled_providers(self) -> list:
        """Delegate provider list used by the /provider hub."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._list_enabled_providers(self)

    def _verify_provider(self, manifest):
        """Delegate single-provider verification used by the /provider hub."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._verify_provider(self, manifest)

    def _get_provider_info(self, provider_id: str):
        """Delegate provider manifest lookup used by activation flows."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._get_provider_info(self, provider_id)

    def _get_navigation_store(self) -> dict[int, dict[str, Any]]:
        """Delegate navigation store helper for single-message Telegram UX."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._get_navigation_store(self)

    def _get_navigation_state(self, chat_id: int) -> dict[str, Any]:
        """Delegate navigation state helper for single-message Telegram UX."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._get_navigation_state(self, chat_id)

    def _reset_navigation_state(
        self,
        chat_id: int,
        message_id: int | None = None,
    ) -> dict[str, Any]:
        """Delegate nav reset helper used by /start."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._reset_navigation_state(
            self,
            chat_id,
            message_id=message_id,
        )

    async def _handle_providers(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Delegate to the canonical provider hub implementation in TelegramCommandsMixin."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_providers(
            self,
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            text=text,
        )

    async def _show_provider_model_picker(
        self,
        chat_id: int,
        prov_id: str,
        page: int = 0,
        selected_tier: str = "s",
        message_id: int | None = None,
        show_models: bool = False,
    ) -> None:
        """Delegate to the canonical tier-first model picker in TelegramCommandsMixin."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=page,
            selected_tier=selected_tier,
            message_id=message_id,
            show_models=show_models,
        )

    async def _show_provider_activation_confirmation(
        self,
        chat_id: int,
        prov_id: str,
        defaults: dict[str, str],
        message_id: int | None = None,
    ) -> None:
        """Delegate to provider activation confirmation screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._show_provider_activation_confirmation(
            self,
            chat_id,
            prov_id,
            defaults,
            message_id=message_id,
        )

    async def _show_models_provider_picker(
        self,
        chat_id: int,
        message_id: int | None = None,
    ) -> None:
        """Delegate to models provider picker screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._show_models_provider_picker(
            self,
            chat_id,
            message_id=message_id,
        )

    async def _show_models_tier_summary(
        self,
        chat_id: int,
        prov_id: str,
        message_id: int | None = None,
    ) -> None:
        """Delegate to models tier summary screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._show_models_tier_summary(
            self,
            chat_id,
            prov_id,
            message_id=message_id,
        )

    async def _resolve_provider_models(self, prov_id: str, manifest=None) -> list[str]:
        """Delegate provider model resolution used by activation/model pickers."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._resolve_provider_models(
            self,
            prov_id,
            manifest=manifest,
        )

    @staticmethod
    def _select_curated_tier_defaults(prov_id: str, models: list[str]) -> dict[str, str]:
        """Delegate curated tier defaults selection used by provider activation callbacks."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._select_curated_tier_defaults(prov_id, models)

    def _persist_hybrid_router_assignments(self, router_cfg) -> None:
        """Delegate hybrid router assignment persistence helper."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        TelegramCommandsMixin._persist_hybrid_router_assignments(self, router_cfg)

    def _update_llm_mode_router(self, provider_id: str, tier_models: dict[str, str]) -> None:
        """Delegate primary LLM mode-router update helper."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        TelegramCommandsMixin._update_llm_mode_router(self, provider_id, tier_models)

    async def _show_models_model_list(
        self,
        chat_id: int,
        prov_id: str,
        tier_code: str,
        page: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Delegate to paginated model list screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._show_models_model_list(
            self,
            chat_id,
            prov_id,
            tier_code,
            page=page,
            message_id=message_id,
        )

    async def _handle_audio_menu(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Delegate to audio/voice settings screen (Voice settings button)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_audio_menu(
            self,
            chat_id,
            user_id,
            is_group=is_group,
            message_id=message_id,
        )

    async def _handle_voice_menu(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Delegate to voice/TTS provider picker screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_voice_menu(
            self,
            chat_id,
            user_id,
            is_group=is_group,
            message_id=message_id,
        )

    async def _handle_providers_and_models(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
    ) -> None:
        """Delegate to combined providers+models view (Providers & Models button)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_providers_and_models(
            self,
            chat_id,
            user_id=user_id,
            is_group=is_group,
        )

    # ── Provider control surface handlers (pu_* callbacks) ───────────────────

    async def _handle_provider_hybrid(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Delegate hybrid routing screen (pu_hybrid callback)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_provider_hybrid(
            self, chat_id, user_id, message_id=message_id, text=text
        )

    async def _handle_provider_vision(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Delegate vision provider picker (pu_vision / vis_* callbacks)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_provider_vision(
            self, chat_id, user_id, message_id=message_id
        )

    async def _handle_provider_show(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Delegate routing state view (pu_show callback)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_provider_show(
            self, chat_id, user_id, message_id=message_id
        )

    async def _handle_provider_reset(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Delegate session override reset (pu_reset_session callback)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_provider_reset(
            self, chat_id, user_id, message_id=message_id
        )

    # ── Slash command handlers missing from initial delegation pass ───────────

    async def _handle_trace_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
    ) -> None:
        """Delegate /trace command (debug on|off toggle + trace view)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_trace_cmd(
            self, chat_id=chat_id, user_id=user_id, text=text
        )

    async def _handle_restart_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Delegate /restart command."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_restart_cmd(
            self, chat_id=chat_id, user_id=user_id, text=text, metadata=metadata
        )

    async def _handle_skill_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Delegate /skill command."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_skill_cmd(
            self, chat_id=chat_id, user_id=user_id, text=text, metadata=metadata
        )

    # ── Callback handlers missing from initial delegation pass ────────────────

    async def _handle_nl_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Delegate nl_yes / nl_cancel / nl_pick:* NL confirm/cancel callbacks."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_nl_callback(
            self, cb_id, cb_data, chat_id, user_id
        )

    async def _handle_status_fix_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Delegate stfix:* setup-fix callbacks from /status readiness panels."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_status_fix_callback(
            self, cb_id, cb_data, chat_id, user_id
        )

    # ── Secondary helper stubs (BUGs 23-30: called by already-delegated methods) ─

    def _runtime_state_with_context(
        self,
        user_id: int,
        chat_id: int,
        context: dict[str, Any],
    ) -> None:
        """Delegate runtime AI-state persistence for NL/intake flows."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        TelegramCommandsMixin._runtime_state_with_context(self, user_id, chat_id, context)

    def _apply_intake_to_space_docs(self, space: str, answers: dict[str, str]) -> Any:
        """Delegate intake answer → space doc writer."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._apply_intake_to_space_docs(self, space, answers)

    async def _handle_nl_command_pick(
        self,
        chat_id: int,
        user_id: int,
        command: str,
    ) -> str:
        """Delegate NL confirmed-command executor (nl_pick:*)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._handle_nl_command_pick(
            self, chat_id, user_id, command
        )

    async def _run_nl_intent(
        self,
        chat_id: int,
        user_id: int,
        intent: str,
        space: str,
        *,
        command: str = "",
        args: str = "",
        is_group: bool = False,
        username: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Delegate NL intent executor (space / intake / command / freetext)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._run_nl_intent(
            self,
            chat_id,
            user_id,
            intent,
            space,
            command=command,
            args=args,
            is_group=is_group,
            username=username,
            metadata=metadata,
        )

    def _load_recent_messages(self, user_id: int, chat_id: int = 0) -> list:
        """Delegate recent-message loader for /trace snapshot."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._load_recent_messages(self, user_id, chat_id)

    def _refresh_ai_runtime_after_router_update(self) -> None:
        """Delegate AI-runtime refresh after LLM router changes."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        TelegramCommandsMixin._refresh_ai_runtime_after_router_update(self)

    @staticmethod
    def _mark_chat_onboarding_step(step_id: str) -> None:
        """Delegate onboarding-step marker (static)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        TelegramCommandsMixin._mark_chat_onboarding_step(step_id)

    @staticmethod
    def _is_cli_command_success(response: str) -> bool:
        """Delegate CLI response success checker (static)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._is_cli_command_success(response)

    @staticmethod
    def _has_host_connectivity_confirmation(response: str) -> bool:
        """Delegate host-connectivity confirmation detector (static)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._has_host_connectivity_confirmation(response)

    # ── Core message-flow handlers (BUGs 18-20: were guarded by AttributeError) ───

    async def _handle_nl_pending_reply(
        self,
        chat_id: int,
        user_id: int,
        text: str,
    ) -> bool:
        """Delegate NL pending confirmation replies (yes / cancel / go)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._handle_nl_pending_reply(
            self, chat_id, user_id, text
        )

    async def _handle_intake_reply(
        self,
        chat_id: int,
        user_id: int,
        text: str,
    ) -> bool:
        """Delegate /intake guided-reply processing."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._handle_intake_reply(
            self, chat_id, user_id, text
        )

    async def _handle_natural_language_request(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        is_group: bool = False,
        username: str = "",
        metadata: dict | None = None,
    ) -> bool:
        """Delegate natural-language → command intent resolver."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return await TelegramCommandsMixin._handle_natural_language_request(
            self,
            chat_id,
            user_id,
            text,
            is_group=is_group,
            username=username,
            metadata=metadata,
        )

    # ── Voice toggle handlers (BUGs 16-17: hardcoded slash route, no guard) ───────

    async def _handle_voiceon_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
    ) -> None:
        """Delegate /voiceon — enable voice replies."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_voiceon_cmd(
            self, chat_id, user_id, is_group
        )

    async def _handle_voiceoff_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
    ) -> None:
        """Delegate /voiceoff — disable voice replies."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_voiceoff_cmd(
            self, chat_id, user_id, is_group
        )

    # ── Voice provider panel (BUG-21: st_goto_voice_provider keyboard button) ───

    async def _handle_provider_voice(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Delegate voice provider key-status panel (Deepgram / ElevenLabs)."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_provider_voice(
            self, chat_id, user_id, is_group, message_id
        )

    async def _handle_debug(self, chat_id: int) -> None:
        """Show daemon debug info (/debug)."""
        import os
        import sys

        lines = ["🛠 <b>Debug</b>\n"]
        lines.append(f"Python: <code>{html.escape(sys.version.split()[0])}</code>")
        try:
            import navig as _navig_pkg

            pkg_file = getattr(_navig_pkg, "__file__", "unknown")
            pkg_ver = getattr(_navig_pkg, "__version__", "unknown")
            lines.append(f"navig pkg: <code>{html.escape(str(pkg_file))}</code>")
            lines.append(f"version: <code>{html.escape(str(pkg_ver))}</code>")
        except Exception as e:
            lines.append(f"navig: ❌ <code>{html.escape(str(e))}</code>")
        try:
            from navig.platform import paths as _paths
            from navig.vault import get_vault

            _vpath = str(_paths.vault_dir())
            v = get_vault()
            items = v.list() if hasattr(v, "list") else []
            count = len(items)
            lines.append(f"vault: 🟢 <code>{count} entries</code> ({html.escape(_vpath)})")
        except Exception as e:
            try:
                from navig.platform import paths as _paths

                _vpath = str(_paths.vault_dir())
            except Exception:
                _vpath = "?"
            lines.append(
                f"vault: ❌ <code>{html.escape(str(e))}</code> — path: <code>{html.escape(_vpath)}</code>"
            )
        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                s_list = sm.list_sessions() if hasattr(sm, "list_sessions") else []
                lines.append(f"sessions: <code>{len(s_list)} loaded</code>")
            except Exception:
                lines.append("sessions: ❌")
        lines.append(f"HAS_VOICE: <code>{HAS_VOICE}</code>")
        lines.append(f"HAS_KEYBOARDS: <code>{HAS_KEYBOARDS}</code>")
        lines.append(f"HAS_SESSIONS: <code>{HAS_SESSIONS}</code>")
        pp = os.environ.get("PYTHONPATH", "(not set)")
        lines.append(f"PYTHONPATH: <code>{html.escape(pp)}</code>")
        dg = os.environ.get("DEEPGRAM_KEY") or os.environ.get("DEEPGRAM_API_KEY")
        if not dg:
            # Also check vault for deepgram key
            try:
                from navig.vault import get_vault

                _v2 = get_vault()
                if _v2 is not None:
                    _store = _v2.store()
                    for _lbl in ("deepgram", "DEEPGRAM_API_KEY", "DEEPGRAM_KEY"):
                        try:
                            _item = _store.get(_lbl)
                            if _item is not None:
                                dg = "(vault)"
                                break
                        except Exception:
                            pass  # best-effort: vault item unreadable; skip
            except Exception:
                pass  # best-effort: vault unavailable; key shown as missing
        lines.append(f"DEEPGRAM_KEY: <code>{'set' if dg else 'missing'}</code>")
        await self.send_message(chat_id, "\n".join(lines))

    async def _handle_trace(self, chat_id: int, user_id: int) -> None:
        """Show recent activity snapshot (/trace).

        Distinct from /debug: *what happened*, not system state.
        Covers: LLM bridges · recent messages · session state · daemon warnings · vault.
        """
        import json as _json
        import os as _os
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        from navig.platform.paths import msg_trace_path as _msg_trace_path

        SEP = "━━━━━━━━━━━━━━━━━━━━━━"
        now_utc = _dt.now(_tz.utc).strftime("%H:%M UTC")
        lines: list = [f"🔍 <b>Recent Trace</b> — {now_utc}", SEP]

        # ── Active Backend ─────────────────────────────────────────────────────
        # Check Bridge (VS Code MCP) first
        _bridge_active = False
        try:
            import socket as _sock

            from navig.providers.bridge_grid_reader import (
                BRIDGE_DEFAULT_PORT as _BRIDGE_PORT,
            )

            _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            _s.settimeout(0.3)
            _bridge_active = _s.connect_ex(("127.0.0.1", _BRIDGE_PORT)) == 0
            _s.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        lines.append("🔌 <b>Routing</b>")
        if _bridge_active:
            lines.append("  🟢 Bridge (VS Code) — <b>connected</b>")
        else:
            lines.append("  ⚫ Bridge — offline (using model router)")

        # Model router slot assignments
        try:
            from navig.llm_router import get_llm_router

            llm_router = get_llm_router()
            _TIER_NAMES = {
                "small_talk": ("⚡", "Small"),
                "big_tasks": ("🧠", "Big"),
                "coding": ("💻", "Code"),
            }
            if llm_router:
                for mode_name, (icon, label) in _TIER_NAMES.items():
                    mc = llm_router.modes.get_mode(mode_name)
                    if not mc:
                        continue
                    provider = getattr(mc, "provider", "?")
                    model = getattr(mc, "model", "?")
                    lines.append(
                        f"  {icon} {label} → <code>{html.escape(str(provider))}:{html.escape(str(model))}</code>"
                    )
        except Exception:
            lines.append("  <i>(model router unavailable)</i>")

        lines.append(SEP)

        # ── Gather session messages ────────────────────────────────────────────
        session_messages: list = []
        all_sessions_count = 0

        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                all_sessions_count = len(sm.sessions)
                sk = f"telegram:user:{user_id}"
                # Prefer in-memory cache; fall back to disk load
                raw_session = sm._sessions.get(sk)
                if raw_session is None and sm._get_session_file(sk).exists():
                    try:
                        raw_session = sm.get_session(chat_id, user_id)
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                if raw_session is not None:
                    session_messages = list(raw_session.messages or [])
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Memory module fallback
        if not session_messages:
            try:
                from navig.agent.memory import get_memory

                mem = get_memory()
                session_messages = (
                    mem.get_recent(user_id=str(user_id), limit=8)
                    if hasattr(mem, "get_recent")
                    else []
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # msg_trace.jsonl last resort
        if not session_messages:
            trace_file = str(_msg_trace_path())
            if _os.path.exists(trace_file):
                try:
                    with open(trace_file, encoding="utf-8") as _f:
                        for raw in _f.readlines()[-8:]:
                            try:
                                entry = _json.loads(raw)
                                role = entry.get("role") or entry.get("type", "?")
                                content = entry.get("content") or entry.get("text") or ""
                                session_messages.append({"role": role, "content": content})
                            except Exception:  # noqa: BLE001
                                pass  # best-effort; failure is non-critical
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # ── Memory snapshot ────────────────────────────────────────────────────
        lines.append(
            f"🧠 <b>Memory</b> — {len(session_messages)} msgs · {all_sessions_count} session(s)"
        )
        lines.append(SEP)

        # ── Recent messages ────────────────────────────────────────────────────
        lines.append("💬 <b>Recent</b>")
        recent = session_messages[-8:]
        if recent:
            for msg in recent:
                role = msg.get("role", "?")
                raw_content = str(msg.get("content") or "").replace("\n", " ").strip()
                preview = (
                    raw_content
                    if len(raw_content) <= 64
                    else raw_content[:64].rsplit(" ", 1)[0] + "…"
                )
                if not preview:
                    preview = "<i>(empty)</i>"
                arrow = "⬅" if role in ("user", "human") else "➡"
                actor = "👤" if role in ("user", "human") else "🤖"
                ts_raw = msg.get("timestamp") or msg.get("ts") or ""
                ts_prefix = ""
                if ts_raw:
                    try:
                        if isinstance(ts_raw, (int, float)):
                            ts_prefix = _dt.utcfromtimestamp(ts_raw).strftime("%H:%M") + " "
                        else:
                            ts_prefix = str(ts_raw)[:5] + " "
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                lines.append(f"  {ts_prefix}{arrow} {actor}: {preview}")
        else:
            lines.append("  <i>(no recent activity)</i>")

        lines.append(SEP)

        # ── Session state ──────────────────────────────────────────────────────
        tier = self._get_user_tier_pref(chat_id, user_id)
        tier_label = {
            "small": "fast",
            "big": "smart",
            "coder_big": "coder",
            "": "auto",
        }.get(tier, tier)

        active_host = "?"
        try:
            from navig.config import get_config_manager

            _gcfg = get_config_manager().global_config or {}
            active_host = _gcfg.get("active_host") or _gcfg.get("default_host") or "?"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        voice_label = "?"
        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                _sk = f"agent:default:telegram:default:dm:{user_id}"
                _s = sm.sessions.get(_sk)
                if _s is not None:
                    voice_label = "on" if _s.metadata.get("voice_enabled", False) else "off"
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(
            f"⚙️  <b>Session</b> — tier: <code>{html.escape(str(tier_label))}</code> · host: <code>{html.escape(str(active_host))}</code> · voice: <code>{html.escape(str(voice_label))}</code>"
        )
        lines.append(f"🛡  Voice pipeline: {'🟢 active' if HAS_VOICE else '⚫ inactive'}")
        lines.append(SEP)

        # ── Daemon log warnings ────────────────────────────────────────────────
        _DAEMON_LOG_CANDIDATES = [
            _os.path.expanduser("~/.navig/debug.log"),
            "/var/log/navig/daemon.log",
            "/var/log/navig-daemon.log",
        ]
        daemon_issues: list = []
        for _log_path in _DAEMON_LOG_CANDIDATES:
            if not _os.path.exists(_log_path):
                continue
            try:
                with open(_log_path, encoding="utf-8", errors="replace") as fh:
                    _tail = fh.readlines()[-50:]
                _kw = (
                    "warning",
                    "error",
                    "could not",
                    "permission denied",
                    "no such file",
                    "failed",
                    "critical",
                )
                daemon_issues = [ln.strip() for ln in _tail if any(kw in ln.lower() for kw in _kw)]
                break
            except OSError:
                pass  # best-effort cleanup

        if daemon_issues:
            lines.append("📋 <b>Daemon Warnings</b>")
            for issue in daemon_issues[-5:]:
                display = issue if len(issue) <= 100 else issue[:97] + "…"
                lines.append(f"  ⚠️  <code>{html.escape(display)}</code>")
        else:
            lines.append("📋 <b>Daemon</b> — ✅ no warnings")

        # ── Vault status ───────────────────────────────────────────────────────
        vault_ok = False
        vault_msg = "unavailable"
        try:
            from navig.vault import get_vault

            _v = get_vault()
            _items = _v.list() if hasattr(_v, "list") else []
            vault_ok = True
            vault_msg = f"{len(_items)} entries"
        except Exception as _ve:
            vault_msg = str(_ve)[:60]

        lines.append(f"🔐 <b>Vault</b> — {'✅' if vault_ok else '❌'} {vault_msg}")
        lines.append(SEP)

        trace_keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "trace_refresh"},
                {"text": "🔌 Providers", "callback_data": "trace_providers"},
                {"text": "🧠 Model", "callback_data": "trace_model"},
            ],
            [
                {"text": "❌ Close", "callback_data": "trace_close"},
            ],
        ]
        await self.send_message(chat_id, "\n".join(lines), keyboard=trace_keyboard)

    async def _handle_tier_command(self, chat_id: int, user_id: int, cmd: str) -> None:
        """Handle /big /small /coder /auto — set or clear persistent model tier."""
        tier_map = {
            "/big": ("big", "🧠 Big", "next messages will use the large smart model."),
            "/small": (
                "small",
                "⚡ Small",
                "next messages will use the fast lightweight model.",
            ),
            "/coder": (
                "coder_big",
                "💻 Coder",
                "next messages will use the coder model.",
            ),
            "/auto": ("", "🔄 Auto", "model selection is back on automatic."),
        }
        tier_key, label, note = tier_map[cmd]
        self._set_user_tier_pref(chat_id, user_id, tier_key)
        await self.send_message(
            chat_id,
            f"{label} — {note}\nSend your message normally now.",
            parse_mode=None,
        )

    async def _handle_restart(
        self,
        chat_id: int,
        user_id: int,
        metadata: dict,
        arg: str,
    ) -> None:
        """/restart [target] — systemd daemon restart or docker container restart."""
        import os as _os
        import subprocess as _sp

        DAEMON_ALIASES = {
            "daemon",
            "navig",
            "navig-daemon",
            "navig_daemon",
            "svc",
            "service",
            "",
        }
        target = (arg or "").strip().lower()

        if target in DAEMON_ALIASES:
            # Self-restart: schedule via subprocess with delay so reply goes out first
            await self.send_message(chat_id, "🔄 Restarting navig-daemon in 3s…", parse_mode=None)
            sudo_pass = _os.environ.get("SUDO_PASS", "")
            if sudo_pass:
                bash_cmd = f"sleep 3 && echo '{sudo_pass}' | sudo -S systemctl restart navig-daemon"
            else:
                bash_cmd = "sleep 3 && sudo systemctl restart navig-daemon"
            _sp.Popen(
                ["bash", "-c", bash_cmd],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                start_new_session=True,
            )
        else:
            # Docker container restart — route through CLI
            await self._handle_cli_command(chat_id, user_id, metadata, f"docker restart {arg}")

    async def _handle_settings_menu(
        self, chat_id: int, user_id: int, is_group: bool = False
    ) -> None:
        """Send the /settings inline-keyboard panel."""
        if not HAS_KEYBOARDS or not HAS_SESSIONS:
            await self.send_message(
                chat_id,
                "⚙️ Settings UI requires the keyboard + session modules.",
                parse_mode=None,
            )
            return

        sm = get_session_manager()
        session = sm.get_or_create_session(chat_id, user_id, is_group)
        keyboard_rows = build_settings_keyboard(session)
        await self.send_message(
            chat_id,
            _settings_header_text(session),
            keyboard=keyboard_rows,
        )

    async def _handle_start(
        self,
        chat_id: int,
        username: str,
        user_id: int = 0,
        prior_last_active: str | None = None,
    ):
        """Delegate to canonical /start flow with nav reset + main screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_start(
            self,
            chat_id=chat_id,
            username=username,
            user_id=user_id,
            prior_last_active=prior_last_active,
        )

    async def _handle_help(self, chat_id: int, topic: str | None = None):
        """Command reference (/help [topic]) from the slash registry."""
        from .telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_help(self, chat_id, topic=topic)

    async def _handle_help_callback(self, cb_data: str, chat_id: int, message_id: int) -> None:
        """Delegate help encyclopedia callback routing to the mixin."""
        from .telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_help_callback(self, cb_data, chat_id, message_id)

    @staticmethod
    def _build_help_home():
        """Delegate help home builder to the mixin."""
        from .telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._build_help_home()

    @staticmethod
    def _generate_help_text(deck_enabled: bool = False) -> str:
        """Delegate /help text generation to TelegramCommandsMixin."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._generate_help_text(deck_enabled=deck_enabled)

    async def renderScreen(
        self,
        chat_id: int,
        screen_name: str,
        payload: dict[str, Any] | None = None,
        message_id: int | None = None,
        user_id: int = 0,
        username: str = "",
    ) -> None:
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin.renderScreen(
            self,
            chat_id=chat_id,
            screen_name=screen_name,
            payload=payload,
            message_id=message_id,
            user_id=user_id,
            username=username,
        )

    async def navigateTo(
        self,
        chat_id: int,
        screen: str,
        *,
        user_id: int = 0,
        username: str = "",
        payload: dict[str, Any] | None = None,
        message_id: int | None = None,
    ) -> None:
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin.navigateTo(
            self,
            chat_id=chat_id,
            screen=screen,
            user_id=user_id,
            username=username,
            payload=payload,
            message_id=message_id,
        )

    async def navigateBack(
        self,
        chat_id: int,
        *,
        user_id: int = 0,
        username: str = "",
        message_id: int | None = None,
    ) -> None:
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin.navigateBack(
            self,
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            message_id=message_id,
        )

    async def _handle_mode(self, chat_id: int, mode_arg: str):
        """Set focus mode and persist to UserStateTracker."""
        valid_modes = ("work", "deep-focus", "planning", "creative", "relax", "sleep")
        if not mode_arg or mode_arg not in valid_modes:
            modes_list = ", ".join(f"<code>{m}</code>" for m in valid_modes)
            await self.send_message(
                chat_id, f"🎯 Available modes: {modes_list}\n\nUsage: <code>/mode work</code>"
            )
            return
        # Persist mode in UserStateTracker
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            tracker = get_user_state_tracker()
            tracker.set_preference("chat_mode", mode_arg)
        except Exception as e:
            logger.debug("Failed to persist mode: %s", e)
        emoji_map = {
            "work": "💼",
            "deep-focus": "🎯",
            "planning": "📋",
            "creative": "🎨",
            "relax": "☕",
            "sleep": "🌙",
        }
        emoji = emoji_map.get(mode_arg, "🎯")
        # Entity-style mode confirmations
        mode_voice = {
            "work": f"{emoji} shifting to work mode. focused.",
            "deep-focus": f"{emoji} going deep. silencing everything except critical.",
            "planning": f"{emoji} planning mode. thinking in structures.",
            "creative": f"{emoji} creative space. looser constraints.",
            "relax": f"{emoji} backing off. around if you need me.",
            "sleep": f"{emoji} entering quiet state. only emergencies get through.",
        }
        text = mode_voice.get(mode_arg, f"{emoji} mode shifted.")
        await self.send_message(chat_id, text, parse_mode=None)

    def _match_cli_command(self, text: str) -> str | None:
        """Match slash command to a navig CLI string via the registry."""
        from .telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._match_cli_command(self, text)

    async def _handle_cli_command(
        self,
        chat_id: int,
        user_id: int,
        metadata: dict,
        navig_cmd: str,
    ):
        """Execute a navig CLI command with typing indicator and send output."""
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            if self.on_message:
                # Route through channel_router as "navig <cmd>" so it hits _execute_navig_command
                response = await self.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=f"navig {navig_cmd}",
                    metadata=metadata,
                )
                if response:
                    # Truncate very long outputs for Telegram (4096 char limit)
                    if len(response) > 4000:
                        response = response[:3950] + "\n…(truncated)"
                    await self.send_message(chat_id, response, parse_mode=None)
                else:
                    await self.send_message(chat_id, "…no output.", parse_mode=None)
            else:
                await self.send_message(chat_id, "…gateway not connected.", parse_mode=None)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

    async def _handle_briefing(self, chat_id: int, user_id: int, metadata: dict):
        """Real-data system briefing — no AI, no invented teams/sprints."""
        import json as _json
        import os as _os
        import socket as _sock
        import subprocess as _sp
        from navig.platform.paths import msg_trace_path as _msg_trace_path

        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            now = _dt.now(_tz.utc)
            lines: list = [
                f"📊 <b>System Briefing</b> — {now.strftime('%H:%M UTC, %d %b')}",
                "━" * 22,
            ]

            # ── Daemon status ──
            try:
                r = _sp.run(
                    [
                        "systemctl",
                        "show",
                        "navig-daemon",
                        "--property=ActiveState,ActiveEnterTimestamp",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                state, since = "unknown", ""
                for ln in r.stdout.splitlines():
                    if ln.startswith("ActiveState="):
                        state = ln.split("=", 1)[1].strip()
                    if ln.startswith("ActiveEnterTimestamp="):
                        raw = ln.split("=", 1)[1].strip()
                        if raw and raw != "n/a":
                            since = f" — since {raw.split()[-2]}"
                icon = "🟢" if state == "active" else "🔴"
                lines.append(f"{icon} <b>Daemon:</b> {state}{since}")
            except Exception:
                lines.append("⚡ <b>Daemon:</b> status unavailable")

            # ── Bridge ──
            bridge_port = 42070
            try:
                from navig.providers.bridge_grid_reader import (
                    BRIDGE_DEFAULT_PORT,
                    get_llm_port,
                )

                bridge_port = get_llm_port() or BRIDGE_DEFAULT_PORT
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            try:
                _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                _s.settimeout(0.8)
                bridge_ok = _s.connect_ex(("127.0.0.1", bridge_port)) == 0
                _s.close()
            except Exception:
                bridge_ok = False
            lines.append(
                f"\u26a1 <b>Bridge:</b> {'online (bridge_copilot)' if bridge_ok else 'offline'}"
            )

            # ── Vault ──
            try:
                from navig.vault import get_vault

                v = get_vault()
                key_count = len(v.list()) if hasattr(v, "list") else "?"
                lines.append(f"🔑 <b>Vault:</b> {key_count} keys stored")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            # ── Sessions ──
            if HAS_SESSIONS:
                try:
                    sm = get_session_manager()
                    lines.append(f"💬 <b>Sessions:</b> {len(sm.sessions)} active")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            # ── Server uptime ──
            try:
                up = _sp.run(["uptime", "-p"], capture_output=True, text=True, timeout=2)
                lines.append(f"⏱ <b>Server:</b> {up.stdout.strip()}")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            # ── Disk ──
            try:
                df = _sp.run(
                    ["df", "-h", "/", "--output=used,avail,pcent"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                dfl = df.stdout.strip().splitlines()
                if len(dfl) >= 2:
                    parts = dfl[1].split()
                    if len(parts) >= 3:
                        lines.append(f"💾 <b>Disk:</b> {parts[0]} used, {parts[1]} free ({parts[2]})")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            lines.append("━" * 22)

            # ── Recent slash commands from trace ──
            recent: list = []
            trace_file = str(_msg_trace_path())
            if _os.path.exists(trace_file):
                try:
                    with open(trace_file, encoding="utf-8") as _tf:
                        for raw in _tf.readlines()[-20:]:
                            try:
                                e = _json.loads(raw)
                                role = e.get("role") or e.get("type", "")
                                content = str(e.get("content") or e.get("text") or "")[:60]
                                if role in ("user", "human") and content.startswith("/"):
                                    recent.append(f"  • <code>{html.escape(content)}</code>")
                            except Exception:  # noqa: BLE001
                                pass  # best-effort; failure is non-critical
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            if recent:
                lines.append("<b>Recent commands:</b>")
                lines.extend(recent[-5:])
            else:
                lines.append("<i>No recent command history.</i>")

            await self.send_message(chat_id, "\n".join(lines))
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

    async def _handle_deck(self, chat_id: int):
        """Send a WebApp button to open the Deck."""
        deck_url = self._get_deck_url()
        if deck_url:
            await self.send_message(
                chat_id,
                "…opening the deck.",
                parse_mode=None,
                keyboard=[
                    [
                        {
                            "text": "🦑 Open Deck",
                            "web_app": {"url": deck_url},
                        }
                    ]
                ],
            )
        else:
            await self.send_message(
                chat_id,
                "…deck not configured yet. set `telegram.deck_url` in config.",
                parse_mode=None,
            )

    async def _handle_skill(
        self,
        chat_id: int,
        user_id: int,
        arg: str,
        metadata: dict,
    ) -> None:
        """/skill [list | <id> | <id> <command> [args...]]"""
        parts = arg.split()

        # /skill (no args) or /skill list  →  list all skills
        if not parts or parts[0].lower() in ("list", "ls", "help"):
            await self._skill_list(chat_id)
            return

        skill_id = parts[0].lower()
        command = parts[1] if len(parts) > 1 else ""
        extra_args = parts[2:] if len(parts) > 2 else []

        # Load skill metadata for context (non-fatal if missing)
        skill_name = skill_id
        try:
            from navig.skills.loader import skills_by_id  # lazy

            index = skills_by_id()
            if skill_id in index:
                skill_name = index[skill_id].name
            elif not command:
                # Unknown skill — show help
                available = "\n".join(
                    f"  <code>{s.id}</code> — {s.name}"
                    for s in sorted(index.values(), key=lambda x: x.id)[:20]
                )
                await self.send_message(
                    chat_id,
                    f"❓ Skill <code>{html.escape(skill_id)}</code> not found.\n\nAvailable:\n{available}",
                )
                return
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # No command → show skill info via SkillRunTool (info mode)
        tool_args: dict = {
            "skill_id": skill_id,
            "command": command,
            "extra_args": extra_args,
        }

        await self.send_typing(chat_id)

        try:
            from navig.tools.skill_runner import SkillRunTool  # lazy

            collector: list[str] = []

            async def _on_status(step: str, detail: str, progress: int) -> None:
                collector.append(f"[{progress:3d}%] {step}: {detail}")

            result = await SkillRunTool().run(tool_args, on_status=_on_status)

            if result.success:
                output_text = ""
                if isinstance(result.output, dict):
                    output_text = result.output.get("output") or result.output.get("info") or ""
                else:
                    output_text = str(result.output or "")

                header = f"🧩 <b>{html.escape(skill_name)}</b>" + (f" › <code>{html.escape(command)}</code>" if command else "")
                msg = f"{header}\n\n{html.escape(output_text[:3800])}" if output_text else f"{header}\n✅ Done."
                await self.send_message(chat_id, msg)
            else:
                await self.send_message(
                    chat_id, f"❌ Skill error:\n{result.error}", parse_mode=None
                )

        except Exception as exc:  # noqa: BLE001
            await self.send_message(chat_id, f"❌ /skill crashed: {exc}", parse_mode=None)

    async def _skill_list(self, chat_id: int) -> None:
        """Send a paginated list of all available skills."""
        try:
            from navig.skills.loader import load_all_skills  # lazy

            skills = load_all_skills()
        except Exception as exc:
            await self.send_message(chat_id, f"❌ Could not load skills: {exc}", parse_mode=None)
            return

        if not skills:
            await self.send_message(
                chat_id,
                "No skills found.\n\nInstall community skill packs or check your `.navig/skills/` folder.",
                parse_mode=None,
            )
            return

        # Group by category
        by_cat: dict[str, list] = {}
        for skill in sorted(skills, key=lambda s: (s.category, s.id)):
            by_cat.setdefault(skill.category, []).append(skill)

        lines: list[str] = ["🧩 <b>Available Skills</b>\n"]
        for cat, cat_skills in sorted(by_cat.items()):
            lines.append(f"\n<b>{html.escape(cat.title())}</b>")
            for s in cat_skills:
                safety_icon = {"safe": "🟢", "elevated": "🟡", "destructive": "🔴"}.get(
                    s.safety, "⚪"
                )
                lines.append(
                    f"  {safety_icon} <code>{html.escape(str(s.id))}</code> — {html.escape(str(s.name))}"
                )

        lines.append("\n\nUsage: <code>/skill &lt;id&gt;</code> for info · <code>/skill &lt;id&gt; &lt;command&gt;</code> to run")

        await self.send_message(chat_id, "\n".join(lines))

    async def _register_commands(self):
        """Register slash commands with Telegram via registry-backed mixin."""
        from .telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._register_commands(self)

    async def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        """Check if user has admin rights in the current group chat."""
        member = await self._api_call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        if not isinstance(member, dict):
            return False
        return member.get("status") in {"administrator", "creator"}

    def _get_deck_url(self) -> str | None:
        """Resolve the Deck WebApp URL from config."""
        try:
            import os

            import yaml

            # Try project config first, then global
            for cfg_path in [
                ".navig/config.yaml",
                os.path.expanduser("~/.navig/config.yaml"),
            ]:
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f) or {}
                    url = (cfg.get("telegram", {}) or {}).get("deck_url")
                    if url:
                        return url
        except Exception as e:
            logger.debug("Could not read deck_url from config: %s", e)
        return None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
        keyboard: list[list[dict]] | None = None,
    ) -> dict | None:
        """Send a message to a chat."""
        if parse_mode == "HTML":
            text = self._auto_markdown_to_html(text)

        data = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode

        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        if keyboard:
            data["reply_markup"] = {"inline_keyboard": keyboard}

        result = await self._api_call("sendMessage", data)
        if result is None and parse_mode:
            retry_data = {k: v for k, v in data.items() if k != "parse_mode"}
            result = await self._api_call("sendMessage", retry_data)
        return result

    @staticmethod
    def _auto_markdown_to_html(text: str) -> str:
        """Convert Markdown-like text to HTML when no HTML tags are present.

        This prevents raw ``**bold**`` / ``_italic_`` markers from leaking to
        Telegram when upstream content is generated in Markdown.
        """
        src = str(text or "")
        if not src:
            return src

        # Keep explicit HTML payloads untouched.
        if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", src):
            return src

        markdownish = re.search(
            r"(\*\*|__|~~|`|^\s{0,3}#{1,6}\s|^\s*[-*+]\s|\[[^\]]+\]\([^)]+\)|(?<!\w)_[^_\n]+_(?!\w))",
            src,
            flags=re.MULTILINE,
        )
        if not markdownish:
            return src

        try:
            from navig.gateway.channels.telegram_html import md_to_html

            converted = md_to_html(src)
            return converted or src
        except Exception:
            return src

    async def send_typing(self, chat_id: int):
        """Send typing indicator."""
        await self._api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        keyboard: list | None = None,
    ) -> dict | None:
        """Edit an existing message."""
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self._api_call("editMessageText", payload)

    # -- Monitoring helpers (mirrors TelegramCommandsMixin; needed when handlers
    # are dispatched as functools.partial and self is TelegramChannel) ----------

    @staticmethod
    def _mon_bar(pct: float, width: int = 14) -> str:
        """Unicode progress bar."""
        filled = int(round(pct / 100 * width))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _mon_status_icon(pct: float, high: int = 80, med: int = 60) -> str:
        if pct >= high:
            return "🔴"
        if pct >= med:
            return "🟡"
        return "🟢"

    @staticmethod
    def _mon_header(icon: str, title: str, subtitle: str = "") -> str:
        import html as _html

        line = "━" * 26
        sub = f"\n<i>{_html.escape(subtitle)}</i>" if subtitle else ""
        return f"{line}\n{icon}  <b>{_html.escape(title)}</b>{sub}\n{line}"

    @staticmethod
    def _mon_host_ctx() -> tuple:
        """Return (host_name, server_config_dict, is_local)."""
        try:
            from navig.config import get_config_manager
            from navig.remote import is_local_host

            cfg = get_config_manager()
            host_name = cfg.get_active_host() or "localhost"
            server_config = cfg.load_server_config(host_name)
            is_local = is_local_host(server_config)
            return host_name, server_config, is_local
        except Exception:
            return "localhost", {"is_local": True}, True

    @staticmethod
    def _strip_internal_tags(text: str) -> str:
        """Remove LLM internal reasoning tags from response text."""
        import re as _re

        # Strip reasoning model chain-of-thought tags (<think>, <thinking>, etc.)
        text = _re.sub(
            r"<(think|thinking|reasoning|thought)>.*?</(think|thinking|reasoning|thought)>",
            "",
            text,
            flags=_re.DOTALL | _re.IGNORECASE,
        )

        # Strip search-quality reflection/score and raw search tags
        text = _re.sub(
            r"<searchquality(?:reflection|score)[^>]*>.*?</searchquality(?:reflection|score)[^>]*>",
            "",
            text,
            flags=_re.DOTALL | _re.IGNORECASE,
        )
        text = _re.sub(
            r"<search>.*?</search>",
            "",
            text,
            flags=_re.DOTALL | _re.IGNORECASE,
        )
        # Collapse multiple blank lines introduced by removal
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _normalize_md(text: str) -> str:
        """Convert standard Markdown to Telegram Markdown V1 compatible format.

        Delegates to ``MarkdownFormatter`` (telegram_formatter module) which
        handles headings → Unicode symbol decorations, **bold** → *bold*,
        bullet/numbered lists, blockquotes, and code-block passthrough.

        Falls back to a minimal inline conversion if the formatter module is
        unavailable, so this method is always safe to call.
        """
        try:
            from navig.gateway.channels.telegram_formatter import (
                FormatterPrefs,
                MarkdownFormatter,
            )

            return MarkdownFormatter().convert(text, FormatterPrefs())
        except Exception:
            # Minimal fallback: just convert **bold** → *bold* and headings
            import re as _re

            text = _re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=_re.DOTALL)
            text = _re.sub(r"^#{1,6}[ \t]+(.+)$", r"*\1*", text, flags=_re.MULTILINE)
            return text

    async def _send_html_with_fallback(
        self,
        chat_id: int,
        text: str,
        keyboard: dict | None = None,
    ) -> None:
        """Send *text* with HTML formatting.

        ``send_message`` already contains parse-mode fallback logic (HTML -> plain),
        so this helper intentionally performs a single call to avoid duplicate retries.
        """
        await self.send_message(chat_id, text, parse_mode="HTML", keyboard=keyboard)

    async def _send_md_with_fallback(
        self,
        chat_id: int,
        text: str,
        keyboard: dict | None = None,
    ) -> None:
        """Deprecated: use ``_send_html_with_fallback`` instead."""
        await self._send_html_with_fallback(chat_id, text, keyboard=keyboard)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message."""
        result = await self._api_call(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )
        return result is not None

    async def send_voice(
        self,
        chat_id: int,
        audio_data: bytes,
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a voice message using multipart/form-data (sendVoice Bot API)."""
        if not self._session or not aiohttp:
            return None
        url = f"{self.base_url}/sendVoice"
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field(
                "voice",
                audio_data,
                filename="voice.ogg",
                content_type="audio/ogg",
            )
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))
            async with self._session.post(url, data=form) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.warning("sendVoice API error: %s", result.get("description"))
                return None
        except Exception as e:
            logger.warning("send_voice failed: %s", e)
            return None

    async def send_photo(
        self,
        chat_id: int,
        photo_data: bytes,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a photo message to a chat."""
        if not self._session or not aiohttp:
            return None
        url = f"{self.base_url}/sendPhoto"
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field(
                "photo",
                photo_data,
                filename="image.jpeg",
                content_type="image/jpeg",
            )
            if caption:
                form.add_field("caption", caption)
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))

            async with self._session.post(url, data=form) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.warning("sendPhoto API error: %s", result.get("description"))
                return None
        except Exception as e:
            logger.warning("send_photo failed: %s", e)
            return None

    async def send_document(
        self,
        chat_id: int,
        document_data: bytes,
        filename: str = "file",
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a document/file to a chat (sendDocument Bot API)."""
        if not self._session or not aiohttp:
            return None
        url = f"{self.base_url}/sendDocument"
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field(
                "document",
                document_data,
                filename=filename,
                content_type="application/octet-stream",
            )
            if caption:
                form.add_field("caption", caption)
            if parse_mode:
                form.add_field("parse_mode", parse_mode)
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))
            async with self._session.post(url, data=form) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.warning("sendDocument API error: %s", result.get("description"))
                return None
        except Exception as e:
            logger.warning("send_document failed: %s", e)
            return None

    async def send_video(
        self,
        chat_id: int,
        video_data: bytes,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a video to a chat (sendVideo Bot API)."""
        if not self._session or not aiohttp:
            return None
        url = f"{self.base_url}/sendVideo"
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("video", video_data, filename="video.mp4", content_type="video/mp4")
            if caption:
                form.add_field("caption", caption)
            if parse_mode:
                form.add_field("parse_mode", parse_mode)
            if duration is not None:
                form.add_field("duration", str(duration))
            if width is not None:
                form.add_field("width", str(width))
            if height is not None:
                form.add_field("height", str(height))
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))
            async with self._session.post(url, data=form) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.warning("sendVideo API error: %s", result.get("description"))
                return None
        except Exception as e:
            logger.warning("send_video failed: %s", e)
            return None

    async def send_animation(
        self,
        chat_id: int,
        animation_data: bytes,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send an animation (GIF or H.264/MPEG-4 AVC) to a chat (sendAnimation Bot API)."""
        if not self._session or not aiohttp:
            return None
        url = f"{self.base_url}/sendAnimation"
        try:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field(
                "animation",
                animation_data,
                filename="animation.gif",
                content_type="image/gif",
            )
            if caption:
                form.add_field("caption", caption)
            if parse_mode:
                form.add_field("parse_mode", parse_mode)
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))
            async with self._session.post(url, data=form) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.warning("sendAnimation API error: %s", result.get("description"))
                return None
        except Exception as e:
            logger.warning("send_animation failed: %s", e)
            return None

    async def send_sticker(
        self,
        chat_id: int,
        sticker: str,
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a sticker to a chat.  *sticker* is a file_id or URL (sendSticker Bot API)."""
        data: dict = {"chat_id": chat_id, "sticker": sticker}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        return await self._api_call("sendSticker", data)

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = True,
        poll_type: str = "regular",
        allows_multiple_answers: bool = False,
        correct_option_id: int | None = None,
        explanation: str | None = None,
        explanation_parse_mode: str | None = "HTML",
        is_closed: bool = False,
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a native Telegram poll (sendPoll Bot API)."""
        import json as _json

        data: dict = {
            "chat_id": chat_id,
            "question": question,
            "options": _json.dumps(options),
            "is_anonymous": is_anonymous,
            "type": poll_type,
            "allows_multiple_answers": allows_multiple_answers,
            "is_closed": is_closed,
        }
        if correct_option_id is not None:
            data["correct_option_id"] = correct_option_id
        if explanation:
            data["explanation"] = explanation
            if explanation_parse_mode:
                data["explanation_parse_mode"] = explanation_parse_mode
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        return await self._api_call("sendPoll", data)

    async def pin_message(
        self,
        chat_id: int,
        message_id: int,
        disable_notification: bool = False,
    ) -> bool:
        """Pin a message in a chat (pinChatMessage Bot API).  Returns True on success."""
        result = await self._api_call(
            "pinChatMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
        )
        return result is not None

    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str = "👍",
        is_big: bool = False,
    ) -> bool:
        """Set a reaction emoji on a message (setMessageReaction Bot API).

        Available from Bot API 7.0.  Falls back gracefully on older API versions.
        Returns True if the reaction was accepted.
        """
        result = await self._api_call(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
                "is_big": is_big,
            },
        )
        return result is not None


def create_telegram_channel(gateway, config: dict[str, Any]) -> TelegramChannel | None:
    """
    Create a Telegram channel from config.

    Config structure:
    {
        "bot_token": "123456:ABC-DEF...",
        "allowed_users": [12345, 67890],
        "allowed_groups": [-123456789]
    }
    """
    bot_token = config.get("bot_token")
    if not bot_token:
        logger.error("Telegram bot_token not configured")
        return None

    async def handle_message(channel, user_id, message, metadata):
        """Route message through gateway."""
        return await gateway.router.route_message(
            channel=channel, user_id=user_id, message=message, metadata=metadata
        )

    async def handle_approval_response(
        user_id: int,
        approved: bool,
        request_id: str | None = None,
    ) -> tuple[bool, str]:
        manager = getattr(gateway, "approval_manager", None)
        if not manager:
            return False, "⚠️ Approval system unavailable"

        resolved_id = (request_id or "").strip()
        if not resolved_id:
            pending: list[dict[str, Any]] = []
            try:
                if hasattr(manager, "get_pending"):
                    pending = (
                        manager.get_pending(
                            channel="telegram",
                            user_id=str(user_id),
                        )
                        or []
                    )
                elif hasattr(manager, "list_pending"):
                    for req in manager.list_pending() or []:
                        if getattr(req, "channel", "") != "telegram":
                            continue
                        if str(getattr(req, "user_id", "")) != str(user_id):
                            continue
                        pending.append({"id": getattr(req, "id", "")})
            except Exception:
                pending = []

            if len(pending) == 1:
                resolved_id = str(pending[0].get("id", "")).strip()
            elif len(pending) > 1:
                return False, "⚠️ Multiple pending approvals; specify a request ID."
            else:
                return False, "⚠️ No pending approval found."

        # AUDIT DECISION:
        # Is this the correct implementation? Yes — request ownership and channel are
        # checked before responding to the approval manager.
        # Does it break any existing callers? No — callback behavior is additive and
        # falls back gracefully when approval manager is unavailable.
        # Is there a simpler alternative? Yes, but skipping ownership checks weakens security.
        if hasattr(manager, "get_request"):
            request = manager.get_request(resolved_id)
            if request:
                request_user = str(request.get("user_id", ""))
                request_channel = str(request.get("channel", ""))
                if request_user and request_user != str(user_id):
                    return False, "⚠️ That approval request belongs to a different user."
                if request_channel and request_channel != "telegram":
                    return False, "⚠️ Approval request channel mismatch."

        try:
            success = await manager.respond(request_id=resolved_id, approved=approved)
        except TypeError:
            # Compatibility for older test doubles that use positional args.
            success = await manager.respond(resolved_id, approved)

        if not success:
            return False, "⚠️ Approval request expired or not found."

        verdict = "✅ Approved" if approved else "❌ Denied"
        return True, f"{verdict} ({resolved_id})"

    return TelegramChannel(
        bot_token=bot_token,
        allowed_users=config.get("allowed_users"),
        allowed_groups=config.get("allowed_groups"),
        on_message=handle_message,
        on_approval_response=handle_approval_response,
        require_auth=config.get("require_auth", True),
    )
