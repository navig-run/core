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
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

# Lazy imports
aiohttp = None
try:
    import aiohttp
except ImportError:
    pass

# Inline keyboard system
try:
    from navig.gateway.channels.telegram_keyboards import (
        ResponseKeyboardBuilder,
        CallbackHandler,
        get_callback_store,
    )
    HAS_KEYBOARDS = True
except ImportError:
    HAS_KEYBOARDS = False

# Session management
try:
    from navig.gateway.channels.telegram_sessions import (
        get_session_manager,
        get_mention_gate,
        SessionManager,
        MentionGate
    )
    HAS_SESSIONS = True
except ImportError:
    HAS_SESSIONS = False

# Message templates
try:
    from navig.gateway.channels.telegram_templates import (
        enforce_response_limits,
    )
    HAS_TEMPLATES = True
except ImportError:
    HAS_TEMPLATES = False

# Decoy responder for unauthorized users
try:
    from navig.gateway.decoy_responder import generate as generate_decoy
    HAS_DECOY = True
except ImportError:
    HAS_DECOY = False


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
    reply_to_message_id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_metadata(self) -> Dict[str, Any]:
        return {
            'chat_id': self.chat_id,
            'user_id': self.user_id,
            'username': self.username,
            'message_id': self.message_id,
            'is_group': self.is_group,
            'reply_to': self.reply_to_message_id,
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
    
    def __init__(
        self,
        bot_token: str,
        allowed_users: Optional[List[int]] = None,
        allowed_groups: Optional[List[int]] = None,
        on_message: Optional[Callable] = None,
        enable_notifications: bool = True,
        require_auth: bool = True,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.allowed_users = set(allowed_users or [])
        self.allowed_groups = set(allowed_groups or [])
        self.on_message = on_message
        self.enable_notifications = enable_notifications
        self.require_auth = require_auth
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_update_id = 0
        self._poll_task: Optional[asyncio.Task] = None
        self._notifier = None
        self._use_webhook = bool(webhook_url)

        # Per-user model tier preference: {user_id: "small"|"big"|"coder_big"|""}
        self._user_model_prefs: Dict[int, str] = {}

        # Inline keyboard system
        self._kb_builder: Optional["ResponseKeyboardBuilder"] = None
        self._cb_handler: Optional["CallbackHandler"] = None
        if HAS_KEYBOARDS:
            self._kb_builder = ResponseKeyboardBuilder()
            self._cb_handler = CallbackHandler(self)

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
            return
            
        self._session = aiohttp.ClientSession()
        self._running = True
        
        # Get bot info
        try:
            me = await self._api_call("getMe")
            if me:
                self._bot_username = me.get('username', '')
                logger.info(f"Telegram bot started: @{self._bot_username}")

                # Auth status
                if self.require_auth:
                    if self.allowed_users:
                        logger.info("Auth ENFORCED: %d allowed users", len(self.allowed_users))
                    else:
                        logger.warning("Auth ENFORCED but allowed_users is EMPTY — all DMs will be blocked!")
                else:
                    logger.warning("Auth DISABLED (require_auth=false) — bot is open to everyone")

                # Register slash commands with Telegram
                await self._register_commands()
                
                # Send startup notification to allowed users
                for user_id in self.allowed_users:
                    try:
                        await self.send_message(
                            user_id,
                            "…online. systems nominal. I'm here.",
                            parse_mode=None,
                        )
                    except Exception:
                        pass
                        
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            return
            
        # Start notifications if enabled
        if self.enable_notifications and self.allowed_users:
            await self._start_notifier()

        # Start polling or webhook
        if self._use_webhook:
            await self._setup_webhook()
        else:
            self._poll_task = asyncio.create_task(self._poll_updates())
        
    async def _start_notifier(self):
        """Start the notification system."""
        try:
            from navig.gateway.notifications import TelegramNotifier
            
            # Use first allowed user as default notification target
            default_chat = list(self.allowed_users)[0] if self.allowed_users else None
            
            if default_chat:
                self._notifier = TelegramNotifier(self, default_chat)
                await self._notifier.start()
                logger.info(f"Telegram notifier started for chat {default_chat}")
        except Exception as e:
            logger.error(f"Failed to start notifier: {e}")

        
    async def stop(self):
        """Stop the Telegram channel."""
        self._running = False
        
        # Stop notifier
        if self._notifier:
            try:
                await self._notifier.stop()
            except Exception:
                pass

        # Remove webhook if we set one
        if self._use_webhook:
            try:
                await self._api_call("deleteWebhook")
                logger.info("Telegram webhook removed")
            except Exception:
                pass
        
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
                
        if self._session:
            await self._session.close()

            
    async def _api_call(self, method: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """Make an API call to Telegram."""
        if not self._session:
            return None
            
        url = f"{self.base_url}/{method}"
        
        try:
            async with self._session.post(url, json=data or {}) as resp:
                result = await resp.json()
                if result.get('ok'):
                    return result.get('result')
                else:
                    logger.error(f"Telegram API error: {result.get('description')}")
                    return None
        except Exception as e:
            logger.error(f"Telegram API call failed: {e}")
            return None
            
    async def _poll_updates(self):
        """Long-poll for updates from Telegram."""
        while self._running:
            try:
                updates = await self._api_call("getUpdates", {
                    "offset": self._last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"]
                })
                
                if updates:
                    for update in updates:
                        self._last_update_id = update['update_id']
                        await self._process_update(update)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)

    # ── Webhook mode ───────────────────────────────────────────────

    async def _setup_webhook(self):
        """Register the webhook URL with Telegram and delete any pending updates."""
        params: Dict[str, Any] = {
            "url": self.webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        }
        if self.webhook_secret:
            params["secret_token"] = self.webhook_secret

        result = await self._api_call("setWebhook", params)
        if result is not None:
            logger.info(f"Telegram webhook set: {self.webhook_url}")
        else:
            logger.error("Failed to set Telegram webhook — falling back to polling")
            self._use_webhook = False
            self._poll_task = asyncio.create_task(self._poll_updates())

    async def handle_webhook_update(self, update: Dict, secret_header: str = "") -> bool:
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
            logger.error(f"Webhook update processing error: {e}")
            return False
                
    async def _process_update(self, update: Dict):
        """Process a single update from Telegram."""
        # ── Handle callback queries (inline button presses) ──
        callback_query = update.get('callback_query')
        if callback_query and self._cb_handler:
            cb_user = callback_query.get('from', {})
            cb_user_id = cb_user.get('id')
            cb_chat = (callback_query.get('message') or {}).get('chat', {})
            cb_is_group = cb_chat.get('type') in ('group', 'supergroup')
            if not self._is_user_authorized(cb_user_id, cb_chat.get('id', 0), cb_is_group):
                logger.warning("Unauthorized callback: user_id=%s", cb_user_id)
                return
            try:
                await self._cb_handler.handle(callback_query)
            except Exception as e:
                logger.error("Callback handler error: %s", e)
            return

        message = update.get('message', {})
        if not message:
            return
            
        chat = message.get('chat', {})
        sender = message.get('from', {})
        text = message.get('text', '')
        
        chat_id = chat.get('id')
        user_id = sender.get('id')
        username = sender.get('username', str(user_id))
        is_group = chat.get('type') in ('group', 'supergroup')
        message_id = message.get('message_id')
        reply_to_msg = message.get('reply_to_message', {})
        reply_to_message_id = reply_to_msg.get('message_id')

        # ── Handle non-text messages (voice, sticker, photo, etc.) ──
        if not text and not message.get('caption'):
            # Check what kind of non-text content it is
            if message.get('voice') or message.get('audio'):
                content_type = 'voice'
            elif message.get('sticker'):
                content_type = 'sticker'
            elif message.get('photo'):
                content_type = 'photo'
            elif message.get('video') or message.get('video_note'):
                content_type = 'video'
            elif message.get('document'):
                content_type = 'document'
            elif message.get('animation'):
                content_type = 'gif'
            elif message.get('location'):
                content_type = 'location'
            elif message.get('contact'):
                content_type = 'contact'
            else:
                content_type = None

            if content_type:
                logger.debug("Non-text message (%s) from user %s — skipping", content_type, user_id)
                # Only acknowledge in DMs, not in groups
                if not is_group and chat_id:
                    ack = {
                        'voice': "can't process voice messages yet — try typing it out?",
                        'sticker': random.choice(["👀", "😄", "nice one"]),
                        'photo': "can't see images yet, but working on it.",
                        'video': "video processing isn't wired up yet.",
                        'document': "can't read files through Telegram yet. try uploading via the deck.",
                        'gif': random.choice(["😄", "ha"]),
                        'location': "noted — but I can't do much with locations yet.",
                        'contact': "got it, but contact handling isn't set up.",
                    }.get(content_type, "got something I can't process yet.")
                    await self.send_message(chat_id, ack, parse_mode=None)
                return
            # If we can't identify it and there's no text, just ignore
            if not text:
                return

        # Use caption as text for media with captions (photos, videos with text)
        if not text and message.get('caption'):
            text = message.get('caption', '')
        
        # Check if replying to a bot message
        is_reply_to_bot = False
        if reply_to_msg:
            reply_from = reply_to_msg.get('from', {})
            is_reply_to_bot = reply_from.get('is_bot', False)
        
        # ── Access control ──
        # When require_auth is True (default), only listed users get through.
        # An empty allowed_users list with require_auth = deny everyone.
        is_authorized = self._is_user_authorized(user_id, chat_id, is_group)
        if not is_authorized:
            logger.warning(
                "Unauthorized access: user_id=%s username=%s chat_id=%s text=%.60s",
                user_id, username, chat_id, (text or "")[:60],
            )
            if not is_group:
                # ── Decoy mode: playful non-actionable response ──
                if HAS_DECOY and text:
                    try:
                        await self.send_typing(chat_id)
                        decoy_text = generate_decoy(user_id, text)
                        await self.send_message(
                            chat_id, decoy_text, parse_mode=None,
                        )
                    except Exception as e:
                        logger.debug("Decoy responder error: %s", e)
            return
        
        # Session management
        session = None
        if HAS_SESSIONS:
            session_manager = get_session_manager()
            session = session_manager.get_session(chat_id, user_id, is_group)
            
            # Mention gating for groups
            if is_group and hasattr(self, '_bot_username'):
                mention_gate = get_mention_gate(self._bot_username)
                should_respond = mention_gate.should_respond(
                    text=text,
                    user_id=user_id,
                    is_group=is_group,
                    is_reply_to_bot=is_reply_to_bot,
                    session=session,
                    reply_to_message_id=reply_to_message_id
                )
                
                if not should_respond:
                    logger.debug(f"Skipping group message (no mention): {text[:50]}")
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
                username=username
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
            metadata['session_key'] = session.session_key
            metadata['context_messages'] = session.get_context_messages(limit=10)
        
        # Dispatch to handler
        if self.on_message:
            try:
                # ── Parse tier override from /big /small /coder prefix ──
                tier_override = ""
                clean_text = text
                for prefix, tier in (("/big ", "big"), ("/small ", "small"), ("/coder ", "coder_big")):
                    if text.lower().startswith(prefix):
                        tier_override = tier
                        clean_text = text[len(prefix):].strip()
                        break

                # Apply persistent user preference if no per-message override
                if not tier_override and user_id in self._user_model_prefs:
                    tier_override = self._user_model_prefs[user_id]

                if tier_override:
                    metadata["tier_override"] = tier_override

                # ── Slash command routing ──
                cmd = text.strip().lower()
                if cmd in ("/models", "/status"):
                    await self._handle_models_command(chat_id, user_id)
                    return
                if cmd == "/start":
                    await self._handle_start(chat_id, username)
                    return
                if cmd == "/help":
                    await self._handle_help(chat_id)
                    return
                if cmd.startswith("/mode"):
                    mode_arg = cmd[5:].strip()
                    await self._handle_mode(chat_id, mode_arg)
                    return
                if cmd == "/briefing":
                    await self._handle_briefing(chat_id, user_id, metadata)
                    return
                if cmd == "/deck":
                    await self._handle_deck(chat_id)
                    return
                if cmd == "/ping":
                    await self.send_message(chat_id, "🏓 pong.", parse_mode=None)
                    return

                # ── Server / infra commands → navig CLI ──
                cli_result = self._match_cli_command(text.strip())
                if cli_result:
                    await self._handle_cli_command(chat_id, user_id, metadata, cli_result)
                    return

                # ── Typing indicator only (no ghost messages) ──
                typing_task = asyncio.create_task(
                    self._keep_typing(chat_id)
                )

                try:
                    response = await self.on_message(
                        channel='telegram',
                        user_id=str(user_id),
                        message=clean_text,
                        metadata=metadata
                    )
                finally:
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
                
                if response:
                    # Enforce message limits based on verbosity preference
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

                    # Record assistant message in session
                    if HAS_SESSIONS and session:
                        session_manager.add_assistant_message(
                            chat_id=chat_id,
                            user_id=user_id,
                            text=response,
                            is_group=is_group
                        )

                    # Build inline keyboard for the response
                    keyboard = None
                    if self._kb_builder:
                        try:
                            keyboard = self._kb_builder.build(
                                ai_response=response,
                                user_message=clean_text,
                                message_id=message_id,
                            )
                        except Exception as kb_err:
                            logger.debug("Keyboard build failed: %s", kb_err)

                    # Send (multi-part or single)
                    if parts and len(parts) > 1:
                        for i, part in enumerate(parts):
                            is_last = (i == len(parts) - 1)
                            await self.send_message(
                                chat_id, part,
                                keyboard=keyboard if is_last else None,
                            )
                    elif len(response) > 4000:
                        chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
                        for i, chunk in enumerate(chunks):
                            is_last = (i == len(chunks) - 1)
                            await self.send_message(
                                chat_id, chunk,
                                keyboard=keyboard if is_last else None,
                            )
                    else:
                        await self.send_message(chat_id, response, keyboard=keyboard)
                    
            except Exception as e:
                import traceback
                logger.error(f"Message handler error: {e}\n{traceback.format_exc()}")
                # Friendly error — no robotic entity-speak
                err_msg = random.choice([
                    f"sorry, something went wrong — {e}",
                    f"oops, hit an error: {e}",
                    f"ran into a problem. {e}",
                ])
                await self.send_message(chat_id, f"❌ {err_msg}", parse_mode=None)

                
    async def _keep_typing(self, chat_id: int, interval: float = 4.0):
        """Re-send 'typing' indicator every ``interval`` seconds until cancelled."""
        try:
            while True:
                await self.send_typing(chat_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass


    async def _handle_models_command(self, chat_id: int, user_id: int = 0):
        """Show active model config with interactive switcher keyboard."""
        try:
            from navig.agent.ai_client import get_ai_client
            client = get_ai_client()
            router = client.model_router

            if not router or not router.is_active:
                await self.send_message(chat_id, "ℹ️ Routing disabled — single-model mode.")
                return

            cfg = router.cfg
            # Current user preference
            user_pref = self._user_model_prefs.get(user_id, "")
            pref_label = {
                "small": "⚡ Small/Fast",
                "big": "🧠 Large/Smart",
                "coder_big": "💻 Coding",
            }.get(user_pref, "🔄 Auto (heuristic)")

            # Build status message
            lines = [
                "🧠 *Model Configuration*\n",
                f"Mode: `{cfg.mode}`",
                f"Your preset: *{pref_label}*\n",
                "*Available tiers:*",
            ]
            tier_info = [
                ("⚡ small", cfg.small),
                ("🧠 big", cfg.big),
                ("💻 coder", cfg.coder_big),
            ]
            for label, slot in tier_info:
                model_name = slot.model or "—"
                provider = slot.provider or "—"
                lines.append(f"  {label}: `{model_name}` ({provider})")

            text = "\n".join(lines)

            # Build inline keyboard
            check = lambda t: " ✓" if user_pref == t else ""
            keyboard = [
                [
                    {"text": f"⚡ Small/Fast{check('small')}", "callback_data": "ms_tier_small"},
                    {"text": f"🧠 Large/Smart{check('big')}", "callback_data": "ms_tier_big"},
                    {"text": f"💻 Coding{check('coder_big')}", "callback_data": "ms_tier_coder"},
                ],
                [
                    {"text": f"🔄 Auto{check('')}", "callback_data": "ms_tier_auto"},
                    {"text": "📊 Full table", "callback_data": "ms_info"},
                ],
            ]
            await self.send_message(chat_id, text, keyboard=keyboard)

        except Exception as e:
            text = f"⚠️ Could not read routing info: {e}"
            await self.send_message(chat_id, text)

    async def _handle_start(self, chat_id: int, username: str):
        """Greeting on /start — warm, natural, human-like."""
        hour = datetime.now().hour
        name = username if username and username != 'None' else 'hey'
        if 5 <= hour < 8:
            greeting = random.choice([
                f"morning, {name}. you're up early — what's going on?",
                f"hey {name}, early start today. what's up?",
            ])
        elif 8 <= hour < 12:
            greeting = random.choice([
                f"hey {name}! what are we working on?",
                f"morning. what do you need?",
            ])
        elif 12 <= hour < 18:
            greeting = random.choice([
                f"hey! what's on your mind?",
                f"yo {name}, what can I do for you?",
            ])
        elif 18 <= hour < 22:
            greeting = random.choice([
                f"hey {name}. still at it?",
                f"evening. what do you need?",
            ])
        else:
            greeting = random.choice([
                f"late one, huh? what's up?",
                f"hey {name}. burning the midnight oil?",
            ])
        await self.send_message(chat_id, greeting, parse_mode=None)

    async def _handle_help(self, chat_id: int):
        """Command reference — entity style."""
        text = (
            "*things I respond to:*\n\n"
            "⚡ *core*\n"
            "/status — how I'm doing\n"
            "/models — what's running under the hood\n"
            "/mode — shift my focus\n"
            "/briefing — today compressed\n"
            "/ping — am I alive?\n"
            "/deck — open the command deck\n\n"
            "📊 *monitoring*\n"
            "/disk — disk usage\n"
            "/memory — RAM status\n"
            "/cpu — load average\n"
            "/uptime — how long the server's been up\n"
            "/services — running services\n"
            "/ports — open ports\n\n"
            "🐳 *docker*\n"
            "/docker — list containers\n"
            "/logs <name> — container logs\n"
            "/restart <name> — restart container\n\n"
            "🗃 *database*\n"
            "/db — list databases\n"
            "/tables <db> — show tables\n\n"
            "🔧 *tools*\n"
            "/hosts — configured servers\n"
            "/use <host> — switch server\n"
            "/run <cmd> — execute remote command\n"
            "/backup — backup status\n\n"
            "🛠 *utilities*\n"
            "/ip — server IP\n"
            "/time — server time\n"
            "/weather — weather check\n"
            "/dns <domain> — DNS lookup\n"
            "/ssl <domain> — SSL cert check\n"
            "/whois <domain> — domain whois\n\n"
            "🧠 *model control*\n"
            "/big /small /coder — force a specific mind\n\n"
            "…or just talk. I understand."
        )
        await self.send_message(chat_id, text)

    async def _handle_mode(self, chat_id: int, mode_arg: str):
        """Set focus mode and persist to UserStateTracker."""
        valid_modes = ("work", "deep-focus", "planning", "creative", "relax", "sleep")
        if not mode_arg or mode_arg not in valid_modes:
            modes_list = ", ".join(f"`{m}`" for m in valid_modes)
            await self.send_message(chat_id, f"🎯 Available modes: {modes_list}\n\nUsage: `/mode work`")
            return
        # Persist mode in UserStateTracker
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker
            tracker = get_user_state_tracker()
            tracker.set_preference("chat_mode", mode_arg)
        except Exception as e:
            logger.debug("Failed to persist mode: %s", e)
        emoji_map = {
            "work": "💼", "deep-focus": "🎯", "planning": "📋",
            "creative": "🎨", "relax": "☕", "sleep": "🌙",
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

    # ── CLI-backed slash commands ──────────────────────────────────────
    # Maps /command → navig CLI string. Use {args} placeholder for user args.
    _SLASH_CLI_MAP = {
        # monitoring
        "/disk":     "host monitor show --disk",
        "/memory":   "run \"free -h\"",
        "/cpu":      "run \"uptime\"",
        "/uptime":   "run \"uptime -p\"",
        "/services": "run \"systemctl list-units --type=service --state=running --no-pager | head -40\"",
        "/ports":    "run \"ss -tlnp | head -30\"",
        "/top":      "run \"top -bn1 | head -20\"",
        "/df":       "run \"df -h\"",
        "/cron":     "run \"crontab -l 2>/dev/null || echo 'no crontab'\"",
        # docker
        "/docker":   "docker ps",
        "/logs":     "docker logs {args} -n 50",
        "/restart":  "docker restart {args}",
        # database
        "/db":       "db list",
        "/tables":   "db tables {args}",
        # hosts / tools
        "/hosts":    "host list",
        "/use":      "host use {args}",
        "/run":      "run \"{args}\"",
        "/backup":   "backup show",
        # utilities
        "/ip":       "run \"curl -s ifconfig.me\"",
        "/time":     "run \"date\"",
        "/weather":  "run \"curl -s 'wttr.in/?format=3'\"",
        "/dns":      "run \"dig +short {args}\"",
        "/ssl":      "run \"echo | openssl s_client -connect {args}:443 -servername {args} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null || echo 'no cert found'\"",
        "/whois":    "run \"whois {args} | head -30\"",
        "/netstat":  "run \"ss -s\"",
    }

    def _match_cli_command(self, text: str) -> Optional[str]:
        """Match a slash command to a navig CLI string. Returns None if no match."""
        parts = text.strip().split(None, 1)
        if not parts:
            return None
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        template = self._SLASH_CLI_MAP.get(cmd)
        if not template:
            return None

        if "{args}" in template:
            if not args:
                # Command needs args but none given
                return template.replace(" {args}", "").replace("{args}", "")
            return template.replace("{args}", args)
        return template

    async def _handle_cli_command(
        self,
        chat_id: int,
        user_id: int,
        metadata: Dict,
        navig_cmd: str,
    ):
        """Execute a navig CLI command with typing indicator and send output."""
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            if self.on_message:
                # Route through channel_router as "navig <cmd>" so it hits _execute_navig_command
                response = await self.on_message(
                    channel='telegram',
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
                pass

    async def _handle_briefing(self, chat_id: int, user_id: int, metadata: Dict):
        """Daily briefing — T5 template."""
        # Generate briefing via AI
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            if self.on_message:
                response = await self.on_message(
                    channel='telegram',
                    user_id=str(user_id),
                    message="Give me a brief status summary of today. What's been done, what's pending, any issues? Keep it to 3-5 bullet points, under 300 characters total.",
                    metadata=metadata,
                )
                if response:
                    await self.send_message(chat_id, f"📊 *Daily Briefing*\n\n{response}")
                else:
                    await self.send_message(chat_id, "📊 No activity to report yet today.")
            else:
                await self.send_message(chat_id, "📊 Briefing system not available.")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def _handle_deck(self, chat_id: int):
        """Send a WebApp button to open the Deck."""
        deck_url = self._get_deck_url()
        if deck_url:
            await self.send_message(
                chat_id,
                "…opening the deck.",
                parse_mode=None,
                keyboard=[[{
                    "text": "🦑 Open Deck",
                    "web_app": {"url": deck_url},
                }]],
            )
        else:
            await self.send_message(
                chat_id,
                "…deck not configured yet. set `telegram.deck_url` in config.",
                parse_mode=None,
            )

    async def _register_commands(self):
        """Register slash commands with Telegram via setMyCommands API."""
        commands = [
            # core
            {"command": "start", "description": "Wake up greeting"},
            {"command": "help", "description": "Command reference"},
            {"command": "status", "description": "System health check"},
            {"command": "models", "description": "Active model routing table"},
            {"command": "mode", "description": "Set focus mode (work, deep-focus, etc.)"},
            {"command": "briefing", "description": "Today's summary"},
            {"command": "deck", "description": "Open the command deck"},
            {"command": "ping", "description": "Quick alive check"},
            # monitoring
            {"command": "disk", "description": "Disk usage"},
            {"command": "memory", "description": "RAM status"},
            {"command": "cpu", "description": "Load / CPU info"},
            {"command": "uptime", "description": "Server uptime"},
            {"command": "services", "description": "Running services"},
            {"command": "ports", "description": "Open ports"},
            # docker
            {"command": "docker", "description": "List containers"},
            {"command": "logs", "description": "Container logs (+ name)"},
            {"command": "restart", "description": "Restart container (+ name)"},
            # database
            {"command": "db", "description": "List databases"},
            {"command": "tables", "description": "Tables in a database (+ db name)"},
            # hosts / tools
            {"command": "hosts", "description": "Configured servers"},
            {"command": "use", "description": "Switch active host (+ name)"},
            {"command": "run", "description": "Execute remote command"},
            {"command": "backup", "description": "Backup status"},
            # utilities
            {"command": "ip", "description": "Server public IP"},
            {"command": "time", "description": "Server time"},
            {"command": "weather", "description": "Weather report"},
            {"command": "dns", "description": "DNS lookup (+ domain)"},
            {"command": "ssl", "description": "SSL cert check (+ domain)"},
            {"command": "whois", "description": "Domain whois (+ domain)"},
            # model override
            {"command": "big", "description": "Force big model for next message"},
            {"command": "small", "description": "Force small model for next message"},
            {"command": "coder", "description": "Force coder model for next message"},
        ]
        result = await self._api_call("setMyCommands", {"commands": commands})
        if result is not None:
            logger.info("Registered %d bot commands with Telegram", len(commands))
        else:
            logger.warning("Failed to register bot commands")

        # Register persistent menu button → opens Deck WebApp
        deck_url = self._get_deck_url()
        if deck_url:
            # Set default menu button for all chats (no chat_id = global default)
            await self._api_call("setChatMenuButton", {
                "menu_button": {
                    "type": "web_app",
                    "text": "🖲️ Deck",
                    "web_app": {"url": deck_url},
                },
            })
            logger.info("Registered Deck menu button: %s", deck_url)

    def _get_deck_url(self) -> Optional[str]:
        """Resolve the Deck WebApp URL from config."""
        try:
            import yaml
            import os
            # Try project config first, then global
            for cfg_path in [".navig/config.yaml", os.path.expanduser("~/.navig/config.yaml")]:
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
        parse_mode: Optional[str] = "Markdown",
        reply_to_message_id: Optional[int] = None,
        keyboard: Optional[List[List[Dict]]] = None,
    ) -> Optional[Dict]:
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
            data["reply_markup"] = {
                "inline_keyboard": keyboard
            }
            
        result = await self._api_call("sendMessage", data)
        if result is None and parse_mode:
            retry_data = {k: v for k, v in data.items() if k != "parse_mode"}
            result = await self._api_call("sendMessage", retry_data)
        return result
        
    async def send_typing(self, chat_id: int):
        """Send typing indicator."""
        await self._api_call("sendChatAction", {
            "chat_id": chat_id,
            "action": "typing"
        })
        
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> Optional[Dict]:
        """Edit an existing message."""
        return await self._api_call("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        })
        
    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message."""
        result = await self._api_call("deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        })
        return result is not None


def create_telegram_channel(
    gateway,
    config: Dict[str, Any]
) -> Optional[TelegramChannel]:
    """
    Create a Telegram channel from config.
    
    Config structure:
    {
        "bot_token": "123456:ABC-DEF...",
        "allowed_users": [12345, 67890],
        "allowed_groups": [-123456789]
    }
    """
    bot_token = config.get('bot_token')
    if not bot_token:
        logger.error("Telegram bot_token not configured")
        return None
        
    async def handle_message(channel, user_id, message, metadata):
        """Route message through gateway."""
        return await gateway.router.route_message(
            channel=channel,
            user_id=user_id,
            message=message,
            metadata=metadata
        )
        
    return TelegramChannel(
        bot_token=bot_token,
        allowed_users=config.get('allowed_users'),
        allowed_groups=config.get('allowed_groups'),
        on_message=handle_message,
        require_auth=config.get('require_auth', True),
    )
