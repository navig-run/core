"""Notification taxonomy — the rows (types) and columns (channels) of the
per-type×channel routing matrix, plus priority levels.

Kept dependency-free so it can be imported by the store, router, and routes
without cycles.
"""

from __future__ import annotations

# ── Channels (matrix columns) ────────────────────────────────────────────────
# `deck` is the always-available in-app channel (bell feed + Inbox + toast).
# The rest map to existing senders (NotificationManager / messaging adapters /
# Gmail connector).
CHANNELS: list[dict[str, str]] = [
    {"key": "deck",     "label": "Deck",     "desc": "In-app bell, Inbox & toasts"},
    {"key": "telegram", "label": "Telegram", "desc": "Your bound Telegram chat"},
    {"key": "email",    "label": "Email",    "desc": "Sent via the Gmail connector"},
    {"key": "sms",      "label": "SMS",      "desc": "Twilio / Vonage — costs apply"},
    {"key": "discord",  "label": "Discord",  "desc": "Discord bot channel/DM"},
    {"key": "whatsapp", "label": "WhatsApp", "desc": "WhatsApp Cloud number"},
    {"key": "matrix",   "label": "Matrix",   "desc": "Matrix room (+ personal bridges)"},
]
CHANNEL_KEYS: list[str] = [c["key"] for c in CHANNELS]

# ── Notification types (matrix rows) ─────────────────────────────────────────
NOTIFICATION_TYPES: list[dict] = [
    {"key": "sms_inbound",      "label": "Incoming SMS",       "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "email_important",  "label": "Important email",    "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "reminder",         "label": "Reminders",          "category": "Personal", "default_channels": ["deck", "telegram"]},
    {"key": "briefing",         "label": "Briefings",          "category": "Personal", "default_channels": ["deck", "telegram", "email"]},
    {"key": "mission_complete", "label": "Mission complete",   "category": "Agent",    "default_channels": ["deck"]},
    {"key": "approval",         "label": "Approvals needed",   "category": "Agent",    "default_channels": ["deck", "telegram"]},
    {"key": "agent_message",    "label": "Agent messages",     "category": "Agent",    "default_channels": ["deck"]},
    {"key": "system_alert",     "label": "System alerts",      "category": "System",   "default_channels": ["deck", "telegram"]},
    {"key": "node_status",      "label": "Node status",        "category": "System",   "default_channels": ["deck"]},
    {"key": "security_alert",   "label": "Security alerts",    "category": "System",   "default_channels": ["deck", "telegram", "sms"]},
    {"key": "invoice_due",      "label": "Invoices due",       "category": "Finance",  "default_channels": ["deck", "telegram", "email"]},
    {"key": "finance_alert",    "label": "Finance alerts",     "category": "Finance",  "default_channels": ["deck", "telegram"]},
    # Privacy — local device-sensor monitors (webcam/mic/screen). Opt-in producers.
    {"key": "webcam_on",        "label": "Webcam in use",      "category": "Privacy",  "default_channels": ["deck", "telegram"]},
    {"key": "webcam_off",       "label": "Webcam released",    "category": "Privacy",  "default_channels": ["deck"]},
    # Signals — inbound public ingest (your websites → deck + Telegram). One generic
    # row for now; per-source rows (signal:<source>) are a fast-follow.
    {"key": "signal_event",     "label": "Signal / webhook",   "category": "Signals",  "default_channels": ["deck", "telegram"]},
    # NAVIG — first-party events about your own system (daemon errors, deploys).
    {"key": "self_error",       "label": "NAVIG errors",       "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    {"key": "deploy",           "label": "Deployments",        "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    {"key": "connectivity",     "label": "Brain reachability", "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    {"key": "custom",           "label": "Custom / other",     "category": "Other",    "default_channels": ["deck"]},
]
TYPE_KEYS: list[str] = [t["key"] for t in NOTIFICATION_TYPES]

# ── Priorities ───────────────────────────────────────────────────────────────
PRIORITIES = ("low", "normal", "high", "critical")


def emoji_for_type(type_key: str) -> str:
    # Dynamic per-source Signals rows (signal:<source>) share the Signals glyph.
    if type_key.startswith("signal:"):
        return "📡"
    return {
        "sms_inbound": "📩", "email_important": "📧",
        "reminder": "⏰", "briefing": "☀️", "mission_complete": "✅",
        "approval": "🔔", "agent_message": "🤖", "system_alert": "🚨",
        "node_status": "🛰️", "security_alert": "🛡️", "invoice_due": "🧾",
        "finance_alert": "💸", "webcam_on": "📷", "webcam_off": "📷",
        "signal_event": "📡", "self_error": "💥", "deploy": "🚀",
        "connectivity": "📶", "custom": "📢",
    }.get(type_key, "📢")
