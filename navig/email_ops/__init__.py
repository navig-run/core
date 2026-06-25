"""navig.email_ops — proactive email layer on the Gmail connector.

Filters that fire notifications (via the unified notify router → deck bell +
Telegram per prefs) and AI briefings (daily / weekly / monthly, optionally per
label). Driven by the OAuth Gmail connector — no IMAP/app-passwords.
"""

from navig.email_ops.config import load_config, save_config
from navig.email_ops.service import get_email_service

__all__ = ["load_config", "save_config", "get_email_service"]
