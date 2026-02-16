"""
NAVIG Bot Module - Telegram bot utilities and handlers.
"""

from navig.bot.help_system import (
    CATEGORIES,
    get_all_commands,
    get_commands_by_category,
    get_command,
    search_commands,
    format_command_help,
    format_category_help,
    format_main_help,
    CommandInfo,
)

from navig.bot.stats_store import (
    BotStatsStore,
    get_bot_store,
    Reminder,
    CommandStat,
)

from navig.bot.start_menu import (
    build_main_menu,
    build_section,
    get_action_info,
    ACTION_COMMANDS,
)

# NLP Intent Parser - optional, imported on demand
try:
    from navig.bot.intent_parser import (
        IntentParser,
        IntentParseResult,
        ConfirmationHandler,
    )
    from navig.bot.command_tools import (
        COMMAND_TOOLS,
        COMMAND_HANDLER_MAP,
        get_command_string,
    )
    NLP_AVAILABLE = True
except ImportError:
    NLP_AVAILABLE = False
    IntentParser = None
    IntentParseResult = None
    ConfirmationHandler = None
    COMMAND_TOOLS = []
    COMMAND_HANDLER_MAP = {}
    get_command_string = None

__all__ = [
    'CATEGORIES',
    'get_all_commands',
    'get_commands_by_category',
    'get_command',
    'search_commands',
    'format_command_help',
    'format_category_help',
    'format_main_help',
    'CommandInfo',
    'BotStatsStore',
    'get_bot_store',
    'Reminder',
    'CommandStat',
    # Start Menu
    'build_main_menu',
    'build_section',
    'get_action_info',
    'ACTION_COMMANDS',
    # NLP
    'NLP_AVAILABLE',
    'IntentParser',
    'IntentParseResult',
    'ConfirmationHandler',
    'COMMAND_TOOLS',
    'COMMAND_HANDLER_MAP',
    'get_command_string',
]
