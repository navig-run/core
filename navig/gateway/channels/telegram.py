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
import logging
import random
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
                is_group=self._is_group_chat_id(chat_id)
                if is_group is None
                else is_group,
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
                is_group=self._is_group_chat_id(chat_id)
                if is_group is None
                else is_group,
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
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60, connect=10)
        )

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
                logger.info(
                    "Auth ENFORCED: %d allowed users", len(self.allowed_users)
                )
            else:
                logger.warning(
                    "Auth ENFORCED but allowed_users is EMPTY — all DMs will be blocked!"
                )
        else:
            logger.warning(
                "Auth DISABLED (require_auth=false) — bot is open to everyone"
            )

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

    async def _api_call(self, method: str, data: dict | None = None) -> dict | None:
        """Make an API call to Telegram."""
        if not self._session:
            return None

        url = f"{self.base_url}/{method}"

        try:
            async with self._session.post(url, json=data or {}) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                else:
                    logger.error("Telegram API error: %s", result.get('description'))
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
        _MAX_RETRIES = 3       # BUG-1: give up after this many failed send attempts
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
            pass

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

                    if not chat_id or not msg:
                        # Malformed row — close it immediately
                        if reminder_id:
                            store.complete_reminder(reminder_id)
                        continue

                    sent = await self.send_message(
                        int(chat_id),
                        f"⏰ *Reminder*\n{msg}",
                        parse_mode="Markdown",
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
                                store.increment_reminder_retry(
                                    reminder_id, _RETRY_DELAY_SEC
                                )
                                logger.debug(
                                    "Reminder id=%s send failed (retry %d/%d), "
                                    "rescheduled +%ds",
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
            self._poll_task = asyncio.create_task(self._poll_updates())

    async def handle_webhook_update(
        self, update: dict, secret_header: str = ""
    ) -> bool:
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
        if callback_query and self._cb_handler:
            cb_user = callback_query.get("from", {})
            cb_user_id = cb_user.get("id")
            cb_chat = (callback_query.get("message") or {}).get("chat", {})
            cb_is_group = cb_chat.get("type") in ("group", "supergroup")
            if not self._is_user_authorized(
                cb_user_id, cb_chat.get("id", 0), cb_is_group
            ):
                logger.warning("Unauthorized callback: user_id=%s", cb_user_id)
                return
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
                            "photo": "can't see images yet, but working on it.",
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

        if HAS_SESSIONS:
            session_manager = get_session_manager()
            session = session_manager.get_session(chat_id, user_id, is_group)

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
                persisted_lang = session_manager.get_session_metadata(
                    chat_id,
                    user_id,
                    "last_detected_language",
                    default="",
                    is_group=is_group,
                )
                if persisted_lang:
                    metadata["last_detected_language"] = str(persisted_lang).strip().lower()
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
                    session_manager.set_session_metadata(
                        chat_id,
                        user_id,
                        "last_detected_language",
                        normalized_voice_lang,
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
                    pref_tier = self._get_user_tier_pref(
                        chat_id, user_id, is_group=is_group
                    )
                    if pref_tier == "noai" and is_slash_command:
                        pref_tier = ""
                    tier_override = pref_tier
                    if pref_tier == "noai" and not is_slash_command:
                        self._user_model_prefs.pop(user_id, None)

                if tier_override:
                    metadata["tier_override"] = tier_override

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
                if cmd in ("/models", "/model"):
                    await self._handle_models_command(chat_id, user_id)
                    return
                if cmd == "/status":
                    await self._handle_status(chat_id, user_id)
                    return
                if cmd == "/start":
                    await self._handle_start(chat_id, username, user_id=user_id)
                    return
                if cmd == "/help":
                    await self._handle_help(chat_id)
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
                if cmd in ("/routing", "/router"):
                    await self._handle_models_command(chat_id, user_id)
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
                    await self._handle_cli_command(
                        chat_id, user_id, metadata, cli_result
                    )
                    return

                # ── One-shot raw/no-AI route ──
                if metadata.get("tier_override") == "noai":
                    stripped_text = clean_text.strip()
                    noai_cmd = self._match_cli_command(stripped_text)
                    if noai_cmd:
                        await self._handle_cli_command(
                            chat_id, user_id, metadata, noai_cmd
                        )
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
                        "⚙️ No-AI mode expects a command. Use `/help` for shortcuts, or send `navig <command>`.",
                        parse_mode="Markdown",
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

    async def _keep_typing(self, chat_id: int, interval: float = 4.0):
        """Re-send 'typing' indicator every ``interval`` seconds until cancelled."""
        try:
            while True:
                await self.send_typing(chat_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    async def _keep_recording(self, chat_id: int, interval: float = 4.0):
        """Re-send 'record_voice' chat action every ``interval`` seconds until cancelled.

        Used during voice-message download + transcription so the user sees a
        microphone / audio-processing indicator instead of silence or a typing
        bubble.  Cancel the returned task once transcription finishes.
        """
        try:
            while True:
                await self._api_call(
                    "sendChatAction", {"chat_id": chat_id, "action": "record_voice"}
                )
                await asyncio.sleep(interval)
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
        placeholder = await self.send_message(
            chat_id, "🧠 Reasoning...", parse_mode=None
        )
        placeholder_id = (placeholder or {}).get("message_id")

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

        # Only append model footer in debug/trace mode — keep normal replies clean
        model_name = (
            self._resolve_model_name(metadata) if self._is_debug_mode(user_id) else ""
        )
        footer = f"\n\n`· {model_name}`" if model_name else ""
        final_text = f"{response}{footer}"
        final_text = self._strip_internal_tags(final_text)

        self._record_assistant_msg(
            session, session_manager, chat_id, user_id, response, is_group
        )

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
                    chat_id, placeholder_id, final_text, keyboard=keyboard
                )
                return
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        await self._send_response(
            chat_id, final_text, text, user_id=user_id, is_group=is_group
        )

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

        if self._is_debug_mode(user_id):
            model_name = self._resolve_model_name(metadata)
            suffix = (
                f"\n\n✅ Done · Model: {model_name}" if model_name else "\n\n✅ Done"
            )
        else:
            suffix = "\n\n✅ Done"
        final_text = f"{response}{suffix}"

        self._record_assistant_msg(
            session, session_manager, chat_id, user_id, response, is_group
        )

        if intro_id:
            try:
                await self.edit_message(chat_id, intro_id, final_text)
                return
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        await self._send_response(
            chat_id, final_text, text, user_id=user_id, is_group=is_group
        )

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
                if tool_name in ("site_check", "web_fetch"):
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

                    code_m = _re.search(
                        r"`{3}[\w]*\n([\s\S]+?)\n`{3}", text
                    ) or _re.search(r"`([^`]+)`", text)
                    if code_m:
                        args["code"] = code_m.group(1)
                        args["language"] = "python"
                    else:
                        await renderer.warn(tool_name, "no code block found")
                        tool_errors.append(tool_name)
                        continue

                progress_val = 2 + round((idx + 1) / total * 6)

                async def _status(step, detail="", progress=0, _tn=tool_name):
                    await renderer.update(
                        step, detail=detail, progress=progress, icon="⚙️"
                    )

                result = await registry.run_tool(tool_name, args, on_status=_status)
                tool_names_run.append(tool_name)

                if result.success:
                    tool_results.append(result)
                    await renderer.update(
                        f"{tool_name} complete",
                        detail=result.summary()[:80],
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

        tool_context = ""
        for r in tool_results:
            if isinstance(r.output, dict):
                for k, v in r.output.items():
                    if k.startswith("_"):
                        continue
                    # WebFetchTool natively trims output to 5000 chars, safe to include.
                    tool_context += f"{r.name}.{k}={v}\n"
            else:
                tool_context += f"{r.name}: {r.output}\n"

        augmented_message = text
        if tool_context:
            augmented_message = f"{text}\n\n[Tool results]\n{tool_context.strip()}"

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

        if llm_response:
            conclusion["Analysis"] = llm_response[:200]

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
            self._record_assistant_msg(
                session, session_manager, chat_id, user_id, llm_response, is_group
            )

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
            await self.send_message(
                chat_id, "🎙️ Couldn't read the voice message.", parse_mode=None
            )
            return None, ""

        # ── Resolve which STT provider to use based on available keys ────────
        # Priority: Deepgram (fastest) → Whisper API → local Whisper (offline)
        stt_provider = None
        fallback_providers: list = []

        dg_key = _os.environ.get("DEEPGRAM_KEY") or _os.environ.get("DEEPGRAM_API_KEY")
        if not dg_key:
            try:
                from navig.vault import get_vault_v2 as _gv2

                dg_key = _gv2().get_secret("deepgram/api-key") or None
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if dg_key:
            stt_provider = _STTProvider.DEEPGRAM

        oai_key = _os.environ.get("OPENAI_API_KEY")
        if not oai_key:
            try:
                from navig.vault import get_vault_v2 as _gv2

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
                "🎙️ *Voice transcription not configured.*\n\n"
                "Add any of the following to `~/.navig/.env` and restart:\n"
                "• `DEEPGRAM_KEY=<key>` — blazing fast, recommended\n"
                "• `OPENAI_API_KEY=<key>` — Whisper API fallback\n"
                "• `pip install openai-whisper` — offline, no key needed",
                parse_mode="Markdown",
            )
            return None, ""

        tmp_path: str | None = None
        _recording_task: asyncio.Task | None = None
        try:
            # Signal immediately that we're processing audio — closest Bot API
            # equivalent to a read receipt for voice messages.
            await self._api_call(
                "sendChatAction", {"chat_id": chat_id, "action": "record_voice"}
            )
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
                if (
                    "whisper not installed" in raw_err
                    or "No module named 'whisper'" in raw_err
                ):
                    user_msg = (
                        "🎙️ Transcription failed: local Whisper is not installed.\n"
                        "Run `pip install openai-whisper` on the server, or add a "
                        "`DEEPGRAM_KEY` / `OPENAI_API_KEY` to `~/.navig/.env`."
                    )
                elif (
                    "API key" in raw_err
                    or "not set" in raw_err
                    or "not configured" in raw_err
                ):
                    user_msg = "🎙️ Transcription failed: no STT API key configured — type your message instead."
                elif "timeout" in raw_err.lower():
                    user_msg = (
                        "🎙️ Transcription timed out — try a shorter clip or type it out."
                    )
                elif "too large" in raw_err:
                    user_msg = (
                        f"🎙️ Audio file too large — {raw_err.split(':', 1)[-1].strip()}"
                    )
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
                uid in getattr(self, "_debug_users", set())
                for uid in self.allowed_users
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
                f"🎙️ *Heard:* _{transcript}_",
                parse_mode="Markdown",
                keyboard=heard_kb,
            )
            detected_lang = (
                (result.language or "") if hasattr(result, "language") else ""
            )
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
            if (
                audio_data is None
                and tts_result.audio_path
                and tts_result.audio_path.exists()
            ):
                audio_data = tts_result.audio_path.read_bytes()

            if not audio_data:
                logger.warning("TTS returned empty audio (non-fatal)")
                return

            await self.send_voice(chat_id, audio_data)

        except Exception as e:
            logger.warning("Voice reply failed (non-fatal): %s", e)
        finally:
            try:
                if (
                    tts_result
                    and tts_result.audio_path
                    and tts_result.audio_path.exists()
                ):
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
        parts = None
        if HAS_TEMPLATES:
            try:
                from navig.agent.proactive.user_state import get_user_state_tracker

                verbosity = get_user_state_tracker().get_preference(
                    "verbosity", "normal"
                )
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
            if (
                keyboard
                and isinstance(keyboard, dict)
                and "inline_keyboard" in keyboard
            ):
                keyboard["inline_keyboard"].append(extra_krow)
            else:
                keyboard = {"inline_keyboard": [extra_krow]}

        if parts and len(parts) > 1:
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                await self.send_message(
                    chat_id, part, keyboard=keyboard if is_last else None
                )
        elif len(response) > 4000:
            chunks = [response[i : i + 4000] for i in range(0, len(response), 4000)]
            for i, chunk in enumerate(chunks):
                is_last = i == len(chunks) - 1
                await self.send_message(
                    chat_id, chunk, keyboard=keyboard if is_last else None
                )
        else:
            await self.send_message(chat_id, response, keyboard=keyboard)

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
    ):
        """Delegate to the canonical model routing screen implementation."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_models_command(
            self,
            chat_id,
            user_id=user_id,
            message_id=message_id,
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

    @staticmethod
    def _provider_vault_validation_status(manifest) -> tuple[bool, bool]:
        """Delegate static vault-key validation used by provider list rendering."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return TelegramCommandsMixin._provider_vault_validation_status(manifest)

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
    ) -> None:
        """Delegate to the canonical provider hub implementation in TelegramCommandsMixin."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_providers(
            self,
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
        )

    async def _show_provider_model_picker(
        self,
        chat_id: int,
        prov_id: str,
        page: int = 0,
        selected_tier: str = "s",
        message_id: int | None = None,
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

    async def _handle_debug(self, chat_id: int) -> None:
        """Show daemon debug info (/debug)."""
        import os
        import sys

        lines = ["🛠 *Debug*\n"]
        lines.append(f"Python: `{sys.version.split()[0]}`")
        try:
            import navig as _navig_pkg

            pkg_file = getattr(_navig_pkg, "__file__", "unknown")
            pkg_ver = getattr(_navig_pkg, "__version__", "unknown")
            lines.append(f"navig pkg: `{pkg_file}`")
            lines.append(f"version: `{pkg_ver}`")
        except Exception as e:
            lines.append(f"navig: ❌ `{e}`")
        try:
            from navig.platform import paths as _paths
            from navig.vault import get_vault_v2

            _vpath = str(_paths.vault_dir())
            v = get_vault_v2()
            items = v.list() if hasattr(v, "list") else []
            count = len(items)
            lines.append(f"vault: 🟢 `{count} entries` ({_vpath})")
        except Exception as e:
            try:
                from navig.platform import paths as _paths

                _vpath = str(_paths.vault_dir())
            except Exception:
                _vpath = "?"
            lines.append(f"vault: ❌ `{e}` — path: `{_vpath}`")
        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                s_list = sm.list_sessions() if hasattr(sm, "list_sessions") else []
                lines.append(f"sessions: `{len(s_list)} loaded`")
            except Exception:
                lines.append("sessions: ❌")
        lines.append(rf"HAS\_VOICE: `{HAS_VOICE}`")
        lines.append(rf"HAS\_KEYBOARDS: `{HAS_KEYBOARDS}`")
        lines.append(rf"HAS\_SESSIONS: `{HAS_SESSIONS}`")
        pp = os.environ.get("PYTHONPATH", "_(not set)_")
        lines.append(f"PYTHONPATH: `{pp}`")
        dg = os.environ.get("DEEPGRAM_KEY") or os.environ.get("DEEPGRAM_API_KEY")
        if not dg:
            # Also check vault for deepgram key
            try:
                from navig.vault import get_vault_v2
                _v2 = get_vault_v2()
                if _v2 is not None:
                    _store = _v2.store()
                    for _lbl in ("deepgram", "DEEPGRAM_API_KEY", "DEEPGRAM_KEY"):
                        try:
                            _item = _store.get(_lbl)
                            if _item is not None:
                                dg = "(vault)"
                                break
                        except Exception:
                            pass
            except Exception:
                pass
        lines.append(rf"DEEPGRAM\_KEY: `{'✓ set' if dg else '✗ missing'}`")
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

        SEP = "━━━━━━━━━━━━━━━━━━━━━━"
        now_utc = _dt.now(_tz.utc).strftime("%H:%M UTC")
        lines: list = [f"🔍 *Recent Trace* — {now_utc}", SEP]

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

        lines.append("🔌 *Routing*")
        if _bridge_active:
            lines.append("  🟢 Bridge (VS Code) — *connected*")
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
                    lines.append(f"  {icon} {label} → `{provider}:{model}`")
        except Exception:
            lines.append("  _(model router unavailable)_")

        lines.append(SEP)

        # ── Gather session messages ────────────────────────────────────────────
        session_messages: list = []
        all_sessions_count = 0

        if HAS_SESSIONS:
            try:
                sm = get_session_manager()
                all_sessions_count = len(sm.sessions)
                sk = f"agent:default:telegram:default:dm:{user_id}"
                # Prefer in-memory cache; fall back to disk load
                raw_session = sm.sessions.get(sk)
                if raw_session is None and sm._get_session_file(sk).exists():
                    try:
                        raw_session = await sm.get_session(sk)
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
            trace_file = _os.path.expanduser("~/.navig/msg_trace.jsonl")
            if _os.path.exists(trace_file):
                try:
                    with open(trace_file, encoding="utf-8") as _f:
                        for raw in _f.readlines()[-8:]:
                            try:
                                entry = _json.loads(raw)
                                role = entry.get("role") or entry.get("type", "?")
                                content = (
                                    entry.get("content") or entry.get("text") or ""
                                )
                                session_messages.append(
                                    {"role": role, "content": content}
                                )
                            except Exception:  # noqa: BLE001
                                pass  # best-effort; failure is non-critical
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # ── Memory snapshot ────────────────────────────────────────────────────
        lines.append(
            f"🧠 *Memory* — {len(session_messages)} msgs · {all_sessions_count} session(s)"
        )
        lines.append(SEP)

        # ── Recent messages ────────────────────────────────────────────────────
        lines.append("💬 *Recent*")
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
                    preview = "_(empty)_"
                arrow = "⬅" if role in ("user", "human") else "➡"
                actor = "👤" if role in ("user", "human") else "🤖"
                ts_raw = msg.get("timestamp") or msg.get("ts") or ""
                ts_prefix = ""
                if ts_raw:
                    try:
                        if isinstance(ts_raw, (int, float)):
                            ts_prefix = (
                                _dt.utcfromtimestamp(ts_raw).strftime("%H:%M") + " "
                            )
                        else:
                            ts_prefix = str(ts_raw)[:5] + " "
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                lines.append(f"  {ts_prefix}{arrow} {actor}: {preview}")
        else:
            lines.append("  _(no recent activity)_")

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
                    voice_label = (
                        "on" if _s.metadata.get("voice_enabled", False) else "off"
                    )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(
            f"⚙️  *Session* — tier: `{tier_label}` · host: `{active_host}` · voice: `{voice_label}`"
        )
        lines.append(
            f"🛡  Voice pipeline: {'🟢 active' if HAS_VOICE else '⚫ inactive'}"
        )
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
                daemon_issues = [
                    ln.strip() for ln in _tail if any(kw in ln.lower() for kw in _kw)
                ]
                break
            except OSError:
                pass  # best-effort cleanup

        if daemon_issues:
            lines.append("📋 *Daemon Warnings*")
            for issue in daemon_issues[-5:]:
                display = issue if len(issue) <= 100 else issue[:97] + "…"
                lines.append(f"  ⚠️  `{display}`")
        else:
            lines.append("📋 *Daemon* — ✅ no warnings")

        # ── Vault status ───────────────────────────────────────────────────────
        vault_ok = False
        vault_msg = "unavailable"
        try:
            from navig.vault import get_vault_v2

            _v = get_vault_v2()
            _items = _v.list() if hasattr(_v, "list") else []
            vault_ok = True
            vault_msg = f"{len(_items)} entries"
        except Exception as _ve:
            vault_msg = str(_ve)[:60]

        lines.append(f"🔐 *Vault* — {'✅' if vault_ok else '❌'} {vault_msg}")
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
            await self.send_message(
                chat_id, "🔄 Restarting navig-daemon in 3s…", parse_mode=None
            )
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
            await self._handle_cli_command(
                chat_id, user_id, metadata, f"docker restart {arg}"
            )

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

    async def _handle_start(self, chat_id: int, username: str, user_id: int = 0):
        """Delegate to canonical /start flow with nav reset + main screen."""
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_start(
            self,
            chat_id=chat_id,
            username=username,
            user_id=user_id,
        )

    async def _handle_help(self, chat_id: int):
        """Command reference (/help) from the slash registry."""
        from .telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._handle_help(self, chat_id)

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
            modes_list = ", ".join(f"`{m}`" for m in valid_modes)
            await self.send_message(
                chat_id, f"🎯 Available modes: {modes_list}\n\nUsage: `/mode work`"
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
                await self.send_message(
                    chat_id, "…gateway not connected.", parse_mode=None
                )
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

        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            now = _dt.now(_tz.utc)
            lines: list = [
                f"📊 *System Briefing* — {now.strftime('%H:%M UTC, %d %b')}",
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
                lines.append(f"{icon} *Daemon:* {state}{since}")
            except Exception:
                lines.append("⚡ *Daemon:* status unavailable")

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
                f"\u26a1 *Bridge:* {'online (bridge_copilot)' if bridge_ok else 'offline'}"
            )

            # ── Vault ──
            try:
                from navig.vault import get_vault_v2

                v = get_vault_v2()
                key_count = len(v.list()) if hasattr(v, "list") else "?"
                lines.append(f"🔑 *Vault:* {key_count} keys stored")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            # ── Sessions ──
            if HAS_SESSIONS:
                try:
                    sm = get_session_manager()
                    lines.append(f"💬 *Sessions:* {len(sm.sessions)} active")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            # ── Server uptime ──
            try:
                up = _sp.run(
                    ["uptime", "-p"], capture_output=True, text=True, timeout=2
                )
                lines.append(f"⏱ *Server:* {up.stdout.strip()}")
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
                        lines.append(
                            f"💾 *Disk:* {parts[0]} used, {parts[1]} free ({parts[2]})"
                        )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            lines.append("━" * 22)

            # ── Recent slash commands from trace ──
            recent: list = []
            trace_file = _os.path.expanduser("~/.navig/msg_trace.jsonl")
            if _os.path.exists(trace_file):
                try:
                    with open(trace_file, encoding="utf-8") as _tf:
                        for raw in _tf.readlines()[-20:]:
                            try:
                                e = _json.loads(raw)
                                role = e.get("role") or e.get("type", "")
                                content = str(e.get("content") or e.get("text") or "")[
                                    :60
                                ]
                                if role in ("user", "human") and content.startswith(
                                    "/"
                                ):
                                    recent.append(f"  • `{content}`")
                            except Exception:  # noqa: BLE001
                                pass  # best-effort; failure is non-critical
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            if recent:
                lines.append("*Recent commands:*")
                lines.extend(recent[-5:])
            else:
                lines.append("_No recent command history._")

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
                    f"  `{s.id}` — {s.name}"
                    for s in sorted(index.values(), key=lambda x: x.id)[:20]
                )
                await self.send_message(
                    chat_id,
                    f"❓ Skill `{skill_id}` not found.\n\nAvailable:\n{available}",
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
                    output_text = (
                        result.output.get("output") or result.output.get("info") or ""
                    )
                else:
                    output_text = str(result.output or "")

                header = f"🧩 **{skill_name}**" + (f" › `{command}`" if command else "")
                msg = (
                    f"{header}\n\n{output_text[:3800]}"
                    if output_text
                    else f"{header}\n✅ Done."
                )
                await self.send_message(chat_id, msg)
            else:
                await self.send_message(
                    chat_id, f"❌ Skill error:\n{result.error}", parse_mode=None
                )

        except Exception as exc:  # noqa: BLE001
            await self.send_message(
                chat_id, f"❌ /skill crashed: {exc}", parse_mode=None
            )

    async def _skill_list(self, chat_id: int) -> None:
        """Send a paginated list of all available skills."""
        try:
            from navig.skills.loader import load_all_skills  # lazy

            skills = load_all_skills()
        except Exception as exc:
            await self.send_message(
                chat_id, f"❌ Could not load skills: {exc}", parse_mode=None
            )
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

        lines: list[str] = ["🧩 **Available Skills**\n"]
        for cat, cat_skills in sorted(by_cat.items()):
            lines.append(f"\n**{cat.title()}**")
            for s in cat_skills:
                safety_icon = {"safe": "🟢", "elevated": "🟡", "destructive": "🔴"}.get(
                    s.safety, "⚪"
                )
                lines.append(f"  {safety_icon} `{s.id}` — {s.name}")

        lines.append(
            "\n\nUsage: `/skill <id>` for info · `/skill <id> <command>` to run"
        )

        await self.send_message(chat_id, "\n".join(lines))

    async def _register_commands(self):
        """Register slash commands with Telegram via registry-backed mixin."""
        from .telegram_commands import TelegramCommandsMixin

        await TelegramCommandsMixin._register_commands(self)

    async def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        """Check if user has admin rights in the current group chat."""
        member = await self._api_call(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )
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
        parse_mode: str | None = "Markdown",
        reply_to_message_id: int | None = None,
        keyboard: list[list[dict]] | None = None,
    ) -> dict | None:
        """Send a message to a chat."""
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

    async def send_typing(self, chat_id: int):
        """Send typing indicator."""
        await self._api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
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

    @staticmethod
    def _strip_internal_tags(text: str) -> str:
        """Remove LLM internal reasoning tags from response text."""
        import re as _re

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
