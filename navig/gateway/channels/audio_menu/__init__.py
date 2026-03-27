"""audio_menu — Deep /audio inline keyboard navigation for NAVIG Telegram bot.

Public API:
  handle_audio_callback(channel, cb_id, cb_data, chat_id, message_id, user_id)
  screen_a_keyboard(cfg) / screen_a_text(cfg) — Screen A (entry point)
  load_config(user_id) / save_config(user_id, cfg)
"""

from .handlers import handle_audio_callback
from .keyboards import screen_a_keyboard, screen_a_text
from .state import AudioConfig, load_config, save_config

__all__ = [
    "handle_audio_callback",
    "screen_a_keyboard",
    "screen_a_text",
    "AudioConfig",
    "load_config",
    "save_config",
]
