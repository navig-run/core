"""
NAVIG Telegram Human-in-the-Loop Bridge

Enables NAVIG to pause browser/desktop tasks when human intervention is needed
(CAPTCHA, 2FA codes, financial confirmation, repeated failures) and resume
execution once the user responds via Telegram.

Architecture:
    navig-host sends "pause" signal → telegram_bridge.py notified
    telegram_bridge.py sends message → user's phone
    User replies → bridge decodes → resumes task with user's input

Setup:
    1. Create a bot at t.me/BotFather, get the token
    2. navig cred add telegram --token <bot_token>
    3. Set your chat_id: navig kg remember user telegram_chat_id <your_chat_id>
    4. Start the bridge daemon: navig telegram listen

Usage (from code):
    from navig.integrations.telegram_bridge import get_telegram_bridge

    bridge = get_telegram_bridge()
    code = await bridge.send_2fa_request("GitHub")   # waits for user reply
    choice = await bridge.pause_and_ask("Continue?", ["Yes", "No", "Skip"])
    await bridge.send_notification("Task done!", screenshot_path="/tmp/shot.png")
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────── lazy Telegram import ────────────────────────────

def _import_bot():
    try:
        from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
        return Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
    except ImportError:
        raise RuntimeError(
            "python-telegram-bot not installed. Run: pip install python-telegram-bot"
        )


# ─────────────────────────── bridge class ────────────────────────────────────

class TelegramBridge:
    """
    Async human-in-the-loop Telegram bridge.

    Provides:
    - pause_and_ask(): send options keyboard, wait for button press
    - send_2fa_request(): ask for a code, wait for text reply
    - send_notification(): one-way notification with optional screenshot

    All methods are async. Use asyncio.run() or an existing event loop.
    """

    def __init__(self, bot_token: str, chat_id: int | str, timeout_seconds: int = 300) -> None:
        self._token = bot_token
        self._chat_id = int(chat_id)
        self._timeout = timeout_seconds
        self._pending: Dict[str, asyncio.Future] = {}  # correlation_id → Future
        self._app = None  # telegram Application (started lazily)

    # ─────────────────────── public API ────────────────────────────────────

    async def send_notification(
        self,
        message: str,
        screenshot_path: Optional[str] = None,
    ) -> None:
        """Send a one-way notification message, with optional screenshot."""
        Bot, *_ = _import_bot()
        bot = Bot(token=self._token)
        async with bot:
            if screenshot_path and Path(screenshot_path).exists():
                with open(screenshot_path, "rb") as fh:
                    await bot.send_photo(
                        chat_id=self._chat_id,
                        photo=fh,
                        caption=f"📸 NAVIG\n\n{message}",
                    )
            else:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=f"🤖 NAVIG\n\n{message}",
                )

    async def pause_and_ask(
        self,
        question: str,
        options: List[str],
        screenshot_path: Optional[str] = None,
    ) -> str:
        """
        Send a question with inline keyboard options and wait for user selection.

        Args:
            question: The question text to display
            options: List of button labels
            screenshot_path: Optional screenshot to attach

        Returns:
            The text of the selected option, or "" on timeout.
        """
        Bot, _, InlineKeyboardButton, InlineKeyboardMarkup, *_ = _import_bot()

        import uuid
        corr = str(uuid.uuid4())[:8]

        # Build inline keyboard
        keyboard = [
            [InlineKeyboardButton(opt, callback_data=f"{corr}:{opt}")]
            for opt in options
        ]
        markup = InlineKeyboardMarkup(keyboard)

        bot = Bot(token=self._token)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[corr] = future

        async with bot:
            text = f"⏸️ NAVIG PAUSED\n\n{question}"
            if screenshot_path and Path(screenshot_path).exists():
                with open(screenshot_path, "rb") as fh:
                    await bot.send_photo(
                        chat_id=self._chat_id,
                        photo=fh,
                        caption=text,
                        reply_markup=markup,
                    )
            else:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    reply_markup=markup,
                )

        try:
            result = await asyncio.wait_for(future, timeout=self._timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("Telegram pause_and_ask timed out after %ds", self._timeout)
            return ""
        finally:
            self._pending.pop(corr, None)

    async def send_2fa_request(
        self,
        service: str,
        screenshot_path: Optional[str] = None,
    ) -> str:
        """
        Ask the user for a 2FA / verification code.

        Sends a message to the user's phone and waits for their text reply
        (up to self._timeout seconds). Returns the code they typed, or "".
        """
        Bot, *_ = _import_bot()

        import uuid
        corr = str(uuid.uuid4())[:8]

        bot = Bot(token=self._token)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[f"2fa:{corr}"] = future

        async with bot:
            text = (
                f"🔐 NAVIG needs a verification code\n\n"
                f"Service: *{service}*\n"
                f"Please reply with the code from your authenticator app or SMS.\n"
                f"_(Ref: {corr})_"
            )
            if screenshot_path and Path(screenshot_path).exists():
                with open(screenshot_path, "rb") as fh:
                    await bot.send_photo(
                        chat_id=self._chat_id,
                        photo=fh,
                        caption=text,
                        parse_mode="Markdown",
                    )
            else:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="Markdown",
                )

        try:
            code = await asyncio.wait_for(future, timeout=self._timeout)
            return code.strip()
        except asyncio.TimeoutError:
            logger.warning("Telegram 2FA request timed out after %ds", self._timeout)
            return ""
        finally:
            self._pending.pop(f"2fa:{corr}", None)

    async def send_task_complete(
        self,
        task_description: str,
        success: bool,
        screenshot_path: Optional[str] = None,
    ) -> None:
        """Notify the user that a task completed (or failed)."""
        icon = "✅" if success else "❌"
        status = "completed successfully" if success else "FAILED"
        msg = f"{icon} Task {status}\n\n{task_description}"
        await self.send_notification(msg, screenshot_path=screenshot_path)

    # ─────────────────────── callback handler ──────────────────────────────

    def resolve_callback(self, callback_data: str, text_reply: Optional[str] = None) -> None:
        """
        Called by the Telegram listener loop when a user presses a button or replies.
        Resolves any waiting future.
        """
        if ":" in callback_data:
            corr, value = callback_data.split(":", 1)
        else:
            corr = callback_data
            value = text_reply or callback_data

        # Check for 2FA reply (text message with pending 2fa:corr key)
        future2fa = self._pending.get(f"2fa:{corr}")
        if future2fa and not future2fa.done():
            future2fa.set_result(value)
            return

        future = self._pending.get(corr)
        if future and not future.done():
            future.set_result(value)

    # ─────────────────────── long-poll listener ─────────────────────────────

    async def start_listener(self) -> None:
        """
        Start the Telegram long-poll update listener.
        This blocks indefinitely — run in a background task or thread.
        The listener picks up button presses and text replies and routes them
        to waiting futures via resolve_callback().
        """
        (Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup,
         Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler) = _import_bot()

        async def on_callback(update, context):
            query = update.callback_query
            await query.answer()
            self.resolve_callback(query.data)

        async def on_message(update, context):
            # Only accept messages from our own chat
            if update.effective_chat.id != self._chat_id:
                return
            text = update.message.text or ""
            # Check if there's a pending 2FA request — resolve it with the text
            for key in list(self._pending.keys()):
                if key.startswith("2fa:"):
                    future = self._pending[key]
                    if not future.done():
                        corr = key.split(":", 1)[1]
                        self.resolve_callback(corr, text)
                        return

        async def on_start(update, context):
            await update.message.reply_text("🤖 NAVIG bot is running. I'll contact you when I need help!")

        app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        app.add_handler(CommandHandler("start", on_start))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

        logger.info("Telegram bridge listener started (chat_id=%s)", self._chat_id)

        async with app:
            await app.start()
            await app.updater.start_polling()
            await asyncio.Event().wait()  # run forever
            await app.updater.stop()
            await app.stop()


# ─────────────────────────── singleton ───────────────────────────────────────

_bridge_instance: Optional[TelegramBridge] = None


def get_telegram_bridge() -> TelegramBridge:
    """
    Return the singleton TelegramBridge.

    Reads the bot token from the NAVIG vault (provider='telegram')
    and the chat_id from the knowledge graph.
    """
    global _bridge_instance
    if _bridge_instance is None:
        from navig.vault import get_vault
        from navig.memory.knowledge_graph import get_knowledge_graph

        vault = get_vault()
        cred_list = vault.list(provider="telegram")
        if not cred_list:
            raise RuntimeError(
                "Telegram credential not found. Run: navig cred add telegram --token <bot_token>"
            )
        token = cred_list[0].data.get("token", "")
        if not token:
            raise RuntimeError("Telegram credential has no 'token' field.")

        kg = get_knowledge_graph()
        chat_id_facts = kg.recall("user", predicate="telegram_chat_id")
        if not chat_id_facts:
            raise RuntimeError(
                "Telegram chat_id not set. Run: navig kg remember user telegram_chat_id <your_chat_id>"
            )
        chat_id = chat_id_facts[0].object

        _bridge_instance = TelegramBridge(token, chat_id)

    return _bridge_instance
