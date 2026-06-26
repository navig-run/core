"""Signal presets — opinionated, pre-templated event shapes for the most common
things a website/backend wants to ping you about.

Picking a preset when you create a source pre-fills its priority + title/body
templates so the deck/Telegram message looks polished without you writing any
template. Templates render over the JSON payload your app POSTs (missing fields
render empty), e.g. preset ``payment_success`` + payload ``{"amount":"$9","customer":"Ada"}``
→ "💰 Payment received — $9 / Ada paid $9".

Kept dependency-free (pure data) so the store, CLI and routes can all import it.
"""

from __future__ import annotations

# key → {label, emoji, priority, title, body, default_channels?}
PRESETS: dict[str, dict] = {
    # ── Revenue ───────────────────────────────────────────────────────────────
    "payment_success": {
        "label": "Payment received", "emoji": "💰", "priority": "normal",
        "title": "💰 Payment received — {amount}",
        "body": "{customer} paid {amount} for {plan}. {note}",
    },
    "payment_failed": {
        "label": "Payment failed", "emoji": "🚫", "priority": "high",
        "title": "🚫 Payment failed — {amount}",
        "body": "{customer}'s payment for {plan} failed: {reason}",
    },
    "refund": {
        "label": "Refund issued", "emoji": "↩️", "priority": "normal",
        "title": "↩️ Refund — {amount}",
        "body": "Refunded {amount} to {customer}. {reason}",
    },
    "order": {
        "label": "New order", "emoji": "🛒", "priority": "normal",
        "title": "🛒 New order — {amount}",
        "body": "{customer} ordered {item} ({amount}).",
    },
    # ── Lifecycle ─────────────────────────────────────────────────────────────
    "user_signup": {
        "label": "New signup", "emoji": "🙌", "priority": "normal",
        "title": "🙌 New signup — {email}",
        "body": "{name} ({email}) just signed up for {plan}.",
    },
    "user_deleted": {
        "label": "Account deleted", "emoji": "👋", "priority": "normal",
        "title": "👋 Account deleted — {email}",
        "body": "{name} ({email}) deleted their account. {reason}",
    },
    "subscription_new": {
        "label": "New subscription", "emoji": "🎉", "priority": "normal",
        "title": "🎉 New subscription — {plan}",
        "body": "{customer} subscribed to {plan} ({amount}).",
    },
    "subscription_canceled": {
        "label": "Subscription canceled", "emoji": "💔", "priority": "high",
        "title": "💔 Subscription canceled — {plan}",
        "body": "{customer} canceled {plan}. {reason}",
    },
    "trial_started": {
        "label": "Trial started", "emoji": "🧪", "priority": "low",
        "title": "🧪 Trial started — {email}",
        "body": "{name} ({email}) started a {plan} trial.",
    },
    # ── Growth & feedback ─────────────────────────────────────────────────────
    "lead": {
        "label": "New lead", "emoji": "🧲", "priority": "normal",
        "title": "🧲 New lead — {name}",
        "body": "{name} ({email}) — {message}",
    },
    "feedback": {
        "label": "User feedback", "emoji": "💬", "priority": "low",
        "title": "💬 Feedback from {name}",
        "body": "{message}",
    },
    "review": {
        "label": "New review", "emoji": "⭐", "priority": "low",
        "title": "⭐ {rating}★ review",
        "body": "{name}: {message}",
    },
    "support_ticket": {
        "label": "Support ticket", "emoji": "🎫", "priority": "high",
        "title": "🎫 Support — {subject}",
        "body": "{name} ({email}): {message}",
    },
    # ── Engineering ───────────────────────────────────────────────────────────
    "error": {
        "label": "App error / bug", "emoji": "🐞", "priority": "high",
        "title": "🐞 Error — {message}",
        "body": "{detail}\nwhere: {url}\nuser: {user}",
        "default_channels": ["deck", "telegram"],
    },
    "deployment": {
        "label": "Deployment", "emoji": "🚀", "priority": "normal",
        "title": "🚀 Deployed {service} {version}",
        "body": "{status} — {note}",
    },
    "traffic_spike": {
        "label": "Traffic spike", "emoji": "📈", "priority": "normal",
        "title": "📈 Traffic spike — {metric}",
        "body": "{detail}",
    },
    "cron_failed": {
        "label": "Job failed", "emoji": "⏱️", "priority": "high",
        "title": "⏱️ Job failed — {job}",
        "body": "{detail}",
    },
    # ── Security ──────────────────────────────────────────────────────────────
    "security": {
        "label": "Security alert", "emoji": "🛡️", "priority": "critical",
        "title": "🛡️ Security — {event}",
        "body": "{detail}\nip: {ip}\nuser: {user}",
        "default_channels": ["deck", "telegram"],
    },
    # ── Knowledge ─────────────────────────────────────────────────────────────
    "brief": {
        "label": "Context brief", "emoji": "📝", "priority": "low",
        "title": "📝 {title}",
        "body": "{summary}",
        "default_channels": ["deck"],
    },
    # ── Catch-all ─────────────────────────────────────────────────────────────
    "generic": {
        "label": "Generic signal", "emoji": "📡", "priority": "normal",
        "title": "📡 {title}",
        "body": "{message}",
    },
}

PRESET_KEYS: list[str] = list(PRESETS)
DEFAULT_CHANNELS = ["deck", "telegram"]


def get_preset(key: str | None) -> dict | None:
    return PRESETS.get(key) if key else None


def preset_emoji(key: str | None) -> str:
    p = get_preset(key)
    return p["emoji"] if p else "📡"


def list_presets() -> list[dict]:
    """Catalog for the CLI/UI (key + display fields, no internal-only data)."""
    return [
        {
            "key": k,
            "label": p["label"],
            "emoji": p["emoji"],
            "priority": p["priority"],
            "title": p["title"],
            "body": p["body"],
        }
        for k, p in PRESETS.items()
    ]
