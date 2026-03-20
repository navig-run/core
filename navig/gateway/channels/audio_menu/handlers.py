"""Callback dispatcher for all audio: callbacks.

Entry point: handle_audio_callback()
Routing pattern: audio:{action}:{...args}

All navigation is in-place (editMessageText + reply_markup).
Zero new messages sent during menu navigation.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from .keyboards import (
    screen_a_keyboard,
    screen_a_text,
    screen_b_keyboard,
    screen_b_text,
    screen_c_keyboard,
    screen_c_text,
    screen_d_keyboard,
    screen_d_text,
    screen_e_keyboard,
    screen_e_text,
    screen_f_keyboard,
    screen_f_text,
)
from .state import load_config, save_config


async def _edit(
    channel: Any,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: list[list[dict[str, Any]]],
) -> None:
    """Edit a message in-place with new text + inline keyboard.

    Silently ignores 'message is not modified' errors from Telegram.
    """
    try:
        await channel._api_call("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": keyboard},
        })
    except Exception as exc:
        # Telegram returns 400 when the message body hasn't changed — that's fine.
        if "not modified" not in str(exc).lower():
            logger.debug("audio_menu: editMessageText failed: %s", exc)


async def handle_audio_callback(
    channel: Any,
    cb_id: str,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int,
) -> None:
    """Route an 'audio:...' callback to the correct screen handler.

    Args:
        channel:    TelegramChannel instance (for _answer, _api_call).
        cb_id:      Telegram callback query ID — must be answered.
        cb_data:    Full callback_data string starting with 'audio:'.
        chat_id:    Chat that contains the keyboard message.
        message_id: Message to edit in-place.
        user_id:    User who pressed the button.
    """
    # Always answer the callback query first to remove the spinner.
    # channel is a TelegramChannel (has _api_call), NOT a CallbackHandler (has _answer).
    await channel._api_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": ""})

    cfg = load_config(user_id)
    parts = cb_data.split(":")  # ["audio", action, ...]
    if len(parts) < 2:
        return
    action = parts[1]

    try:
        # ── Screen A — provider list ────────────────────────────────
        if action == "providers":
            await _edit(channel, chat_id, message_id,
                        screen_a_text(cfg), screen_a_keyboard(cfg))

        # ── Screen B — model list ───────────────────────────────────
        elif action == "models" and len(parts) >= 3:
            provider_id = parts[2]
            await _edit(channel, chat_id, message_id,
                        screen_b_text(provider_id, cfg),
                        screen_b_keyboard(provider_id, cfg))

        # ── Screen C — model settings panel ────────────────────────
        elif action == "settings" and len(parts) >= 4:
            provider_id, model_id = parts[2], parts[3]
            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Screen D — voice picker (paginated) ─────────────────────
        elif action == "voice_pick" and len(parts) >= 5:
            provider_id, model_id = parts[2], parts[3]
            page = int(parts[4]) if parts[4].isdigit() else 0
            await _edit(channel, chat_id, message_id,
                        screen_d_text(provider_id, model_id, page, cfg),
                        screen_d_keyboard(provider_id, model_id, page, cfg))

        # ── Screen D — voice selected → save → back to C ───────────
        elif action == "voice_set" and len(parts) >= 5:
            provider_id, model_id = parts[2], parts[3]
            cfg.voice = parts[4]
            save_config(user_id, cfg)
            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Screen E — speed picker ─────────────────────────────────
        elif action == "speed" and len(parts) >= 4:
            provider_id, model_id = parts[2], parts[3]
            await _edit(channel, chat_id, message_id,
                        screen_e_text(provider_id, model_id, cfg),
                        screen_e_keyboard(provider_id, model_id, cfg))

        # ── Screen E — speed selected → save → back to C ───────────
        elif action == "speed_set" and len(parts) >= 5:
            provider_id, model_id = parts[2], parts[3]
            try:
                cfg.speed = float(parts[4])
            except ValueError:
                pass  # malformed value; skip
            save_config(user_id, cfg)
            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Screen F — format picker ────────────────────────────────
        elif action == "format" and len(parts) >= 4:
            provider_id, model_id = parts[2], parts[3]
            await _edit(channel, chat_id, message_id,
                        screen_f_text(provider_id, model_id, cfg),
                        screen_f_keyboard(provider_id, model_id, cfg))

        # ── Screen F — format selected → save → back to C ──────────
        elif action == "fmt_set" and len(parts) >= 5:
            provider_id, model_id = parts[2], parts[3]
            cfg.format = parts[4]
            save_config(user_id, cfg)
            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Screen C — auto toggle (in-place) ──────────────────────
        elif action == "auto" and len(parts) >= 4:
            provider_id, model_id = parts[2], parts[3]
            cfg.auto = not cfg.auto
            save_config(user_id, cfg)
            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Screen C — activate this model ──────────────────────────
        elif action == "activate" and len(parts) >= 4:
            provider_id, model_id = parts[2], parts[3]
            cfg.provider = provider_id
            cfg.model = model_id
            cfg.active = True
            save_config(user_id, cfg)

            # Mirror into session.tts_provider so voice replies switch immediately
            _TTS_MAP = {
                "openai":       "openai",
                "edge":         "edge",
                "deepgram":     "deepgram",
                "google_cloud": "google_cloud",
            }
            try:
                from navig.gateway.channels.telegram_sessions import get_session_manager
                sm = get_session_manager()
                is_group = chat_id < 0
                session = sm.get_or_create_session(chat_id, user_id, is_group)
                session.tts_provider = _TTS_MAP.get(provider_id, "auto")
                sm._save_session(session)
            except Exception as sync_err:
                logger.debug("audio_menu: session tts_provider sync: %s", sync_err)

            await _edit(channel, chat_id, message_id,
                        screen_c_text(provider_id, model_id, cfg),
                        screen_c_keyboard(provider_id, model_id, cfg))

        # ── Close / dismiss ─────────────────────────────────────────
        elif action == "close":
            try:
                await channel._api_call("deleteMessage", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                })
            except Exception:
                pass  # already deleted or not found — harmless

    except Exception as exc:
        # Never crash the bot on keyboard navigation errors.
        logger.warning("audio_menu handler error [%s]: %s", cb_data, exc)
