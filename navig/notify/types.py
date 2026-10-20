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
    # Rings through silent mode and repeats until acknowledged (emergency priority).
    # No type defaults to it: `navig notify pushover setup` turns it on for the few
    # must-not-miss types once the keys validate.
    {"key": "pushover", "label": "Pushover", "desc": "Rings on silent, repeats until you acknowledge"},
    # Local desktop toast (Windows). No type defaults to it — opt in per type from the matrix.
    {"key": "toast",    "label": "Desktop",  "desc": "Windows desktop notification on this PC"},
]
CHANNEL_KEYS: list[str] = [c["key"] for c in CHANNELS]

# ── Notification types (matrix rows) ─────────────────────────────────────────
NOTIFICATION_TYPES: list[dict] = [
    {"key": "sms_inbound",      "label": "Incoming SMS",       "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "email_important",  "label": "Important email",    "category": "Messages", "default_channels": ["deck", "telegram"]},
    # Every new unread email the proactive engine sees, once each. Deck only by default:
    # "new mail" is not news worth a phone buzz for most inboxes — opt Telegram in per type.
    {"key": "email_new",        "label": "New email",          "category": "Messages", "default_channels": ["deck"]},
    # Mailroom outbox (navig-email): a reply queued / changed / cancelled, one confirmed sent,
    # and the ones that need you (not sent, outcome unknown, mailbox needs re-authorising).
    # "Sent" is only ever raised from a verified SENT state, never when a job is created.
    {"key": "mail_outbox",      "label": "Mail queued",        "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "mail_sent",        "label": "Mail sent",          "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "mail_send_problem", "label": "Mail send problems", "category": "Messages", "default_channels": ["deck", "telegram"]},
    # The mailroom's digest briefing (products, events, jobs extracted from newsletters).
    {"key": "mail_digest",      "label": "Mail digest",        "category": "Messages", "default_channels": ["deck", "telegram"]},
    {"key": "reminder",         "label": "Reminders",          "category": "Personal", "default_channels": ["deck", "telegram"]},
    # Appointments are the one Personal type that earns SMS by default. A routine
    # nudge you can read later belongs on Telegram; a doctor's appointment you are
    # about to miss has to reach you even when Telegram does not. Keeping them a
    # SEPARATE type from `reminder` is the whole point — the operator wanted SMS for
    # "really important things like appointments, not simple ones", and a per-type
    # channel matrix cannot express that while both share one type.
    {"key": "appointment",      "label": "Appointments",       "category": "Personal", "default_channels": ["deck", "telegram", "sms"]},
    {"key": "briefing",         "label": "Briefings",          "category": "Personal", "default_channels": ["deck", "telegram", "email"]},
    {"key": "mission_complete", "label": "Mission complete",   "category": "Agent",    "default_channels": ["deck"]},
    # One per cycle of a standing goal (`navig goal`). Telegram by default: the operator
    # asked for the goal, so its progress is news, not noise — unlike mission_complete.
    {"key": "goal_digest",      "label": "Goal progress",      "category": "Agent",    "default_channels": ["deck", "telegram"]},
    {"key": "approval",         "label": "Approvals needed",   "category": "Agent",    "default_channels": ["deck", "telegram"]},
    # Agent Monitor (navig-agentctl): a tracked run finished / failed / needs you / went quiet.
    # Deck only by default — Telegram and the Windows toast are opt-in per type.
    {"key": "agent_done",        "label": "Agent run finished", "category": "Agent",    "default_channels": ["deck"]},
    {"key": "agent_failed",      "label": "Agent run failed",   "category": "Agent",    "default_channels": ["deck"]},
    {"key": "agent_needs_input", "label": "Agent needs you",    "category": "Agent",    "default_channels": ["deck"]},
    # Creative Mode's grounded improvement proposal (navig creative / Dashboard card).
    # Deck only by default — suggestions are not phone buzz; Telegram is opt-in per type.
    {"key": "creative_proposal", "label": "NAVIG suggestions",  "category": "Agent",    "default_channels": ["deck"]},
    {"key": "agent_stalled",     "label": "Agent run stalled",  "category": "Agent",    "default_channels": ["deck"]},
    {"key": "agent_message",    "label": "Agent messages",     "category": "Agent",    "default_channels": ["deck"]},
    {"key": "system_alert",     "label": "System alerts",      "category": "System",   "default_channels": ["deck", "telegram"]},
    {"key": "node_status",      "label": "Node status",        "category": "System",   "default_channels": ["deck"]},
    {"key": "security_alert",   "label": "Security alerts",    "category": "System",   "default_channels": ["deck", "telegram", "sms"]},
    {"key": "invoice_due",      "label": "Invoices due",       "category": "Finance",  "default_channels": ["deck", "telegram", "email"]},
    {"key": "finance_alert",    "label": "Finance alerts",     "category": "Finance",  "default_channels": ["deck", "telegram"]},
    # A paid sale came in (navig-msstore today; any store plugin can raise it). NAVIG OS
    # plays a cash-register ring for it.
    {"key": "sale",             "label": "Sales",              "category": "Finance",  "default_channels": ["deck", "telegram"]},
    # Once a day: yesterday's sales and the last 7 days (navig-msstore). Never rings —
    # in NAVIG OS or on Telegram (`silent_default`: sent with disable_notification), so
    # the cash-register sound keeps meaning "a sale just happened".
    {"key": "sales_digest",     "label": "Daily sales summary", "category": "Finance", "default_channels": ["deck", "telegram"], "silent_default": True},
    # The hourly Store sales check failed several times in a row (expired credentials…),
    # and its "working again" follow-up. Without it the ring just goes quiet.
    {"key": "sales_check_failed", "label": "Sales check failing", "category": "Finance", "default_channels": ["deck", "telegram"]},
    # Privacy — local device-sensor monitors (webcam/mic/screen), Telegram-manager
    # events, and breach finds from the opt-in `monitors.breach` vault scan.
    {"key": "webcam_on",        "label": "Webcam in use",      "category": "Privacy",  "default_channels": ["deck", "telegram"]},
    {"key": "webcam_off",       "label": "Webcam released",    "category": "Privacy",  "default_channels": ["deck"]},
    {"key": "message_deleted",  "label": "Message deleted",    "category": "Privacy",  "default_channels": ["telegram"]},
    # A vault email turned up in leak dumps (opt-in `monitors.breach` scan). The body
    # carries masked passwords and counts only — the full record list is
    # `navig vault breach list` — so it is safe on every channel by default.
    {"key": "breach_alert",     "label": "Breach found",       "category": "Privacy",  "default_channels": ["deck", "telegram"]},
    # Signals — inbound public ingest (your websites → deck + Telegram). One generic
    # row for now; per-source rows (signal:<source>) are a fast-follow.
    {"key": "signal_event",     "label": "Signal / webhook",   "category": "Signals",  "default_channels": ["deck", "telegram"]},
    # NAVIG — first-party events about your own system (daemon errors, deploys).
    {"key": "self_error",       "label": "NAVIG errors",       "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    # Deck only: a deploy is something YOU just did, so a phone buzz for it is noise
    # (9 a month on the operator's install). Opt Telegram in from the matrix if wanted.
    {"key": "deploy",           "label": "Deployments",        "category": "NAVIG",    "default_channels": ["deck"]},
    {"key": "connectivity",     "label": "Brain reachability", "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    {"key": "config_incident",  "label": "Config rescues",     "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    # A primary model stopped answering and replies switched to a fallback model (and
    # the "it's back" follow-up). Deduped by navig.llm.model_fallback (first switch,
    # then at most every 6 h). An alert, not day-bound routine — never chat-tidied.
    {"key": "model_fallback",   "label": "Model fallbacks",    "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    # A Lens is waiting on a person: an agent proposed one, or an update was fetched from
    # its install link (navig.lenses.review). Nothing runs until it is approved.
    {"key": "lens_review",      "label": "Lens reviews",       "category": "NAVIG",    "default_channels": ["deck", "telegram"]},
    # Games — free-game claims (navig-games plugin).
    {"key": "free_game",        "label": "Free games",         "category": "Games",    "default_channels": ["deck", "telegram"]},
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
        "sms_inbound": "📩", "email_important": "📧", "email_new": "✉️",
        "mail_outbox": "📤", "mail_sent": "📨", "mail_send_problem": "📛", "mail_digest": "📰",
        "reminder": "⏰", "appointment": "📅", "briefing": "☀️", "mission_complete": "✅", "goal_digest": "🎯",
        "approval": "🔔", "agent_message": "🤖",
        "agent_done": "✅", "agent_failed": "❌", "agent_needs_input": "🙋", "agent_stalled": "⏳", "system_alert": "🚨",
        "creative_proposal": "💡",
        "node_status": "🛰️", "security_alert": "🛡️", "invoice_due": "🧾",
        "finance_alert": "💸", "webcam_on": "📷", "webcam_off": "📷",
        "message_deleted": "🗑", "breach_alert": "🔓",
        "signal_event": "📡", "self_error": "💥", "deploy": "🚀",
        "connectivity": "📶", "config_incident": "🩺", "model_fallback": "🔀", "lens_review": "👓", "free_game": "🎮",
        "sale": "💰", "sales_digest": "📊", "sales_check_failed": "⚠️",
        "custom": "📢",
    }.get(type_key, "📢")
