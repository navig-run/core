"""
Telegram Voice Bot for NAVIG

A production-grade Telegram bot that handles voice messages end-to-end:
  Voice note received → STT → LLM routing → TTS → sendVoice + sendMessage

Also handles all existing bot features:
  - Inline keyboards for interactive menus
  - /help, /start, /status commands
  - Text messages via IntentParser → LLM
  - Graceful error replies with fallback text-only mode
  - Webhook (production) and polling (development) modes

Security:
  - Bot token loaded exclusively from Vault at startup; fail-fast if missing.
  - Optional whitelist of allowed chat_ids (empty = allow all authenticated users).

Usage:
    from navig.integrations.telegram_voice_bot import TelegramVoiceBot, VoiceBotConfig

    # Development (polling):
    bot = TelegramVoiceBot(config=VoiceBotConfig())
    await bot.run_polling()

    # Production (webhook):
    bot = TelegramVoiceBot(config=VoiceBotConfig(
        webhook_url="https://your-domain.com/telegram/webhook",
    ))
    await bot.run_webhook(listen="0.0.0.0", port=8443)
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("navig.integrations.telegram_voice_bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class VoiceBotConfig:
    """Configuration for TelegramVoiceBot."""

    # Vault label for the bot token. Fail-fast if not present at startup.
    token_vault_label: str = "telegram/bot-token"

    # Optional set of numeric chat IDs to whitelist. Empty = allow all.
    allowed_chat_ids: set[int] = field(default_factory=set)

    # STT provider preference
    stt_provider: str = "deepgram"  # "deepgram" | "whisper_api" | "whisper_local"
    stt_fallback: str = "whisper_api"

    # TTS provider preference
    tts_provider: str = "edge"  # "edge" | "openai" | "elevenlabs" | "deepgram"
    tts_voice: str | None = None

    # Language for STT / TTS
    language: str = "en"

    # Max voice note duration accepted by bot (seconds) — Telegram limit is 600s
    max_voice_duration_seconds: int = 120

    # Path where temporary audio files are stored during processing
    audio_temp_dir: Path | None = None

    # Whether to send "typing" / "record_audio" action while processing
    send_chat_action: bool = True

    # Webhook settings (only used in run_webhook mode)
    webhook_url: str | None = None
    webhook_secret_token: str | None = None

    # navig-echo bridge URL (to notify of voice events)
    echo_bridge_url: str | None = None

    # LLM system prompt
    system_prompt: str = (
        "You are NAVIG, a concise voice-first AI assistant. "
        "Respond in 1–3 sentences. You are speaking; avoid markdown."
    )


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class TelegramVoiceBot:
    """
    Full-featured Telegram bot with voice message support.

    Handles:
      - Voice messages (`.oga` / OGG Opus via Telegram)
      - Video notes (round video messages — same codec)
      - Text messages (via IntentParser → LLM)
      - /start — interactive main menu with inline keyboard
      - /help — command listing from navig.bot.help_system
      - /status — NAVIG daemon status
      - Callback queries for inline keyboard buttons
    """

    def __init__(self, config: VoiceBotConfig | None = None):
        self.config = config or VoiceBotConfig()
        self._token: str | None = None
        self._app: object | None = None  # telegram.ext.Application

    # ------------------------------------------------------------------ #
    # Startup — fail-fast vault check
    # ------------------------------------------------------------------ #

    def _load_token(self) -> str:
        """Load bot token from Vault. Raises RuntimeError if not set."""
        try:
            from navig.vault import get_vault

            token = get_vault().get_secret(self.config.token_vault_label)
            if not token:
                raise KeyError("empty token")
            return token
        except KeyError as exc:
            raise RuntimeError(
                f"Telegram bot token not found in vault "
                f"(label={self.config.token_vault_label!r}). "
                f"Provision it with: navig vault put {self.config.token_vault_label} <token>"
            ) from exc

    def _build_application(self, token: str):
        """Build the python-telegram-bot Application with all handlers registered."""
        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,  # noqa: F401
            )
        except ImportError as exc:
            raise RuntimeError(
                "python-telegram-bot not installed. Install with: pip install python-telegram-bot"
            ) from exc

        builder = Application.builder().token(token)
        app = builder.build()

        # ── Command handlers ──────────────────────────────────────────
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))

        # ── Voice / video note ────────────────────────────────────────
        from telegram.ext import filters as F

        app.add_handler(MessageHandler(F.VOICE | F.VIDEO_NOTE, self._handle_voice))

        # ── Text (non-command) via IntentParser ───────────────────────
        app.add_handler(MessageHandler(F.TEXT & ~F.COMMAND, self._handle_text))

        # ── Inline keyboard callbacks ─────────────────────────────────
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        return app

    # ------------------------------------------------------------------ #
    # Run modes
    # ------------------------------------------------------------------ #

    async def run_polling(self) -> None:
        """Start the bot in long-polling mode (development)."""
        self._token = self._load_token()
        self._app = self._build_application(self._token)

        logger.info("TelegramVoiceBot starting (polling mode)")
        async with self._app:
            await self._app.start()
            await self._app.updater.start_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            logger.info("🤖 NAVIG Telegram bot is running (polling). Press Ctrl+C to stop.")
            # Run until cancelled
            await asyncio.Event().wait()
            await self._app.updater.stop()
            await self._app.stop()

    async def run_webhook(
        self,
        listen: str = "0.0.0.0",
        port: int = 8443,
        url_path: str | None = None,
    ) -> None:
        """Start the bot in webhook mode (production)."""
        self._token = self._load_token()
        self._app = self._build_application(self._token)

        if not self.config.webhook_url:
            raise ValueError(
                "VoiceBotConfig.webhook_url must be set for webhook mode. "
                "Example: https://your-domain.com/telegram/bot"
            )

        webhook_path = url_path or f"/telegram/{self._token}"

        logger.info(
            "TelegramVoiceBot starting (webhook mode) — %s%s",
            self.config.webhook_url,
            webhook_path,
        )

        async with self._app:
            await self._app.start()
            await self._app.updater.start_webhook(
                listen=listen,
                port=port,
                url_path=webhook_path,
                webhook_url=f"{self.config.webhook_url}{webhook_path}",
                secret_token=self.config.webhook_secret_token,
            )
            await asyncio.Event().wait()
            await self._app.updater.stop()
            await self._app.stop()

    # ------------------------------------------------------------------ #
    # Command handlers
    # ------------------------------------------------------------------ #

    async def _cmd_start(self, update, context) -> None:
        """Reply with main menu inline keyboard."""
        if not self._is_allowed(update):
            return

        try:
            from navig.bot.start_menu import build_main_menu

            text, markup = build_main_menu()
        except Exception:
            text = "👋 Welcome to <b>NAVIG</b>!\n\nI'm your AI assistant. Send me a voice message or text to get started."
            markup = None

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )

    async def _cmd_help(self, update, context) -> None:
        """Reply with formatted command reference."""
        if not self._is_allowed(update):
            return

        try:
            from navig.bot.help_system import format_main_help

            text = format_main_help()
        except Exception:
            text = (
                "🤖 <b>NAVIG Commands</b>\n\n"
                "/start — Main menu\n"
                "/help — This message\n"
                "/status — System status\n\n"
                "<i>Send a voice message or text to chat with NAVIG.</i>"
            )

        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_status(self, update, context) -> None:
        """Reply with current NAVIG system status."""
        if not self._is_allowed(update):
            return

        lines = ["📊 <b>NAVIG Status</b>\n"]
        try:
            from navig.voice.pipeline import get_pipeline

            p = get_pipeline()
            lines.append(f"• Voice pipeline: {'🟢 running' if p._running else '🔴 stopped'}")
        except Exception:
            lines.append("• Voice pipeline: ⚪ unavailable")

        try:
            from navig.mesh.registry import get_registry

            reg = get_registry()
            peers = reg.live_peers()
            lines.append(f"• Mesh peers: {len(peers)} live")
        except Exception:
            lines.append("• Mesh: ⚪ unavailable")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # ------------------------------------------------------------------ #
    # Voice message handler — the core feature
    # ------------------------------------------------------------------ #

    async def _handle_voice(self, update, context) -> None:
        """
        Process an incoming voice message or video note:
          1. Download OGG audio from Telegram
          2. Transcribe via STT (Deepgram primary, Whisper fallback)
          3. Route transcript through LLM router
          4. Synthesise TTS audio response
          5. Reply with sendVoice (audio) + sendMessage (text)

        Sends "🎙 Listening..." typing indicator while processing.
        Falls back to text-only reply if TTS fails.
        """
        if not self._is_allowed(update):
            return

        msg = update.message
        is_video_note = msg.video_note is not None
        media = msg.video_note if is_video_note else msg.voice

        if media is None:
            return  # unreachable but guards type narrowing

        # Guard: reject excessively long voice notes
        duration = getattr(media, "duration", 0) or 0
        if duration > self.config.max_voice_duration_seconds:
            await msg.reply_text(
                f"⚠️ Voice message too long ({duration}s). "
                f"Maximum is {self.config.max_voice_duration_seconds}s."
            )
            return

        # Show "recording audio" action to indicate we're processing
        if self.config.send_chat_action:
            await context.bot.send_chat_action(
                chat_id=msg.chat_id,
                action="record_voice",
            )

        # ── 1. Download audio ─────────────────────────────────────────
        tmp_dir = self.config.audio_temp_dir or Path(tempfile.gettempdir())
        tmp_path = tmp_dir / f"navig_tg_voice_{msg.message_id}.oga"

        try:
            file_obj = await context.bot.get_file(media.file_id)
            await file_obj.download_to_drive(custom_path=str(tmp_path))
            logger.info(
                "Voice note downloaded: %s (%.1f KB, %ds)",
                tmp_path.name,
                tmp_path.stat().st_size / 1024,
                duration,
            )
        except Exception as exc:
            logger.error("Failed to download voice note: %s", exc)
            await msg.reply_text("⚠️ Failed to download your voice message. Please try again.")
            return

        try:
            # ── 2. STT ────────────────────────────────────────────────
            if self.config.send_chat_action:
                await context.bot.send_chat_action(
                    chat_id=msg.chat_id,
                    action="typing",
                )

            transcript = await self._transcribe(tmp_path, is_voice=True)

            if not transcript:
                await msg.reply_text(
                    "🔇 I couldn't understand that voice message. "
                    "Please speak clearly and try again."
                )
                return

            logger.info("Transcript: %r", transcript[:120])

            # Echo the transcript as a subtle confirmation
            await msg.reply_text(f"🎤 <i>{transcript}</i>", parse_mode="HTML")

            # ── 3. Intent Parsing (Voice-to-Action) ───────────────────
            if self.config.send_chat_action:
                await context.bot.send_chat_action(
                    chat_id=msg.chat_id,
                    action="typing",
                )

            # Try intent parser first to trigger real actions from voice
            response_text: str | None = None
            _intent_handled = False
            try:
                from navig.bot import NLP_AVAILABLE, IntentParser

                if NLP_AVAILABLE and IntentParser is not None:
                    parser = IntentParser()
                    intent = await parser.parse(transcript)
                    if intent and intent.command:
                        # Execute the parsed command
                        from navig.bot import COMMAND_HANDLER_MAP

                        handler = COMMAND_HANDLER_MAP.get(intent.command)
                        if handler:
                            result = await handler(intent)
                            if result:
                                response_text = str(result)
                                _intent_handled = True
            except Exception as intent_exc:
                logger.debug("Voice intent parser error (falling back to LLM): %s", intent_exc)

            # ── 4. Generative LLM fallback ────────────────────────────
            if not _intent_handled:
                response_text = await self._call_llm(transcript)
                if not response_text:
                    await msg.reply_text("⚠️ I couldn't generate a response. Please try again.")
                    return

            # ── 5. TTS Output ─────────────────────────────────────────
            audio_path = await self._call_tts(response_text)

            # ── 6. Reply ──────────────────────────────────────────────
            if audio_path and Path(audio_path).exists():
                # Send voice note first, then text caption
                with open(audio_path, "rb") as audio_fh:
                    await context.bot.send_voice(
                        chat_id=msg.chat_id,
                        voice=audio_fh,
                        caption=(response_text[:1024] if len(response_text) <= 1024 else None),
                    )
                # If response is longer than caption limit, send full text separately
                if len(response_text) > 1024:
                    await msg.reply_text(response_text)
            else:
                # TTS unavailable — text-only fallback
                logger.warning("TTS audio not available; replying text-only")
                await msg.reply_text(f"🤖 {response_text}")

        finally:
            # Always clean up the downloaded file
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass  # best-effort cleanup

    # ------------------------------------------------------------------ #
    # Text message handler
    # ------------------------------------------------------------------ #

    async def _handle_text(self, update, context) -> None:
        """Route non-command text messages through the intent parser and LLM."""
        if not self._is_allowed(update):
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        if self.config.send_chat_action:
            await context.bot.send_chat_action(
                chat_id=update.message.chat_id,
                action="typing",
            )

        # Try intent parser first (navig.bot.IntentParser)
        try:
            from navig.bot import NLP_AVAILABLE, IntentParser

            if NLP_AVAILABLE and IntentParser is not None:
                parser = IntentParser()
                intent = await parser.parse(text)
                if intent and intent.command:
                    # Execute the parsed command
                    from navig.bot import COMMAND_HANDLER_MAP

                    handler = COMMAND_HANDLER_MAP.get(intent.command)
                    if handler:
                        result = await handler(intent)
                        if result:
                            await update.message.reply_text(str(result))
                            return
        except Exception as intent_exc:
            logger.debug("Intent parser error (falling back to LLM): %s", intent_exc)

        # Fallback to direct LLM
        response = await self._call_llm(text)
        if response:
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("⚠️ I couldn't process that request right now.")

    # ------------------------------------------------------------------ #
    # Callback query handler (inline keyboards)
    # ------------------------------------------------------------------ #

    async def _handle_callback(self, update, context) -> None:
        """Handle button presses from inline keyboards."""
        query = update.callback_query
        await query.answer()  # Acknowledge immediately (removes loading spinner)

        data = query.data or ""

        # TelegramBridge callback pattern (correlation:value)
        if ":" in data:
            from navig.integrations.telegram_bridge import _bridge_instance

            if _bridge_instance is not None:
                _bridge_instance.resolve_callback(data)
                return

        # Menu action routing
        try:
            from navig.bot.start_menu import get_action_info

            info = get_action_info(data)
            if info:
                await query.edit_message_text(info["description"], parse_mode="HTML")
                return
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        await query.edit_message_text(f"⚙️ Action: <code>{data}</code>", parse_mode="HTML")

    # ------------------------------------------------------------------ #
    # STT, LLM, TTS helpers (delegate to pipeline components)
    # ------------------------------------------------------------------ #

    async def _transcribe(self, audio_path: Path, *, is_voice: bool = True) -> str | None:
        """Transcribe an audio file using vault-keyed STT providers."""
        from navig.voice.stt import STT, STTConfig, STTProvider

        _map = {
            "deepgram": STTProvider.DEEPGRAM,
            "whisper_api": STTProvider.WHISPER_API,
            "whisper_local": STTProvider.WHISPER_LOCAL,
        }
        config = STTConfig(
            provider=_map.get(self.config.stt_provider, STTProvider.DEEPGRAM),
            fallback_providers=[_map.get(self.config.stt_fallback, STTProvider.WHISPER_API)],
            language=self.config.language,
        )
        stt = STT(config=config)
        result = await stt.transcribe(audio_path, is_voice=is_voice)

        if result.success and result.text:
            return result.text.strip()

        logger.warning(
            "STT failed (provider=%s): %s",
            result.provider,
            result.error,
        )
        return None

    async def _call_llm(self, text: str) -> str | None:
        """Route text through navig-core's UnifiedRouter."""
        try:
            from navig.routing.router import RouteRequest, get_router

            router = get_router()
            messages = [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": text},
            ]
            request = RouteRequest(messages=messages, entrypoint="telegram_voice_bot")
            response_text, _trace = await router.run(request)
            return response_text.strip() if response_text else None
        except Exception as exc:
            logger.error("LLM routing error: %s", exc)
            return None

    async def _call_tts(self, text: str) -> str | None:
        """Synthesise speech. Returns the local audio file path or None."""
        try:
            from navig.voice.tts import TTS, TTSConfig, TTSProvider

            _map = {
                "openai": TTSProvider.OPENAI,
                "elevenlabs": TTSProvider.ELEVENLABS,
                "edge": TTSProvider.EDGE,
                "google_cloud": TTSProvider.GOOGLE_CLOUD,
                "deepgram": TTSProvider.DEEPGRAM,
            }
            config = TTSConfig(
                provider=_map.get(self.config.tts_provider, TTSProvider.EDGE),
                fallback_providers=[TTSProvider.EDGE],
            )
            if self.config.tts_voice:
                config.voice = self.config.tts_voice

            tts = TTS(config=config)
            result = await tts.synthesize(text)

            if result.success and result.audio_path:
                logger.info(
                    "TTS synthesised %d chars → %s (%.1f KB)",
                    len(text),
                    result.audio_path.name,
                    result.audio_path.stat().st_size / 1024,
                )
                return str(result.audio_path)
            logger.warning("TTS failed: %s", result.error)
            return None
        except Exception as exc:
            logger.error("TTS error: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Access control
    # ------------------------------------------------------------------ #

    def _is_allowed(self, update) -> bool:
        """Return True if the sender is in the whitelist (or no whitelist set)."""
        if not self.config.allowed_chat_ids:
            return True  # No whitelist — allow all
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            return False
        allowed = chat_id in self.config.allowed_chat_ids
        if not allowed:
            logger.warning("Blocked update from chat_id=%s (not in whitelist)", chat_id)
        return allowed


# ---------------------------------------------------------------------------
# Module-level singleton + convenience runner
# ---------------------------------------------------------------------------

_bot: TelegramVoiceBot | None = None


def get_voice_bot(config: VoiceBotConfig | None = None) -> TelegramVoiceBot:
    """Return (or create) the global TelegramVoiceBot singleton."""
    global _bot
    if _bot is None:
        _bot = TelegramVoiceBot(config=config)
    return _bot


async def run_bot_polling(config: VoiceBotConfig | None = None) -> None:
    """Convenience: start the bot in polling mode and run until cancelled."""
    bot = TelegramVoiceBot(config=config)
    await bot.run_polling()
