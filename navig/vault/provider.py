"""NAVIG Vault Provider Registry — well-known service shortcuts.

``navig vault provider set <name>`` uses this to know which field to store
and which env var to check for non-interactive seeding.

Adding a new provider
---------------------
Add an entry to PROVIDERS with at minimum: key_field, env.
``label`` is shown in ``navig vault provider list``.
``test_url`` (optional) is used by ``navig vault provider test``.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["PROVIDERS", "get_provider", "list_providers", "resolve_env"]

# Registry of well-known providers.
# key_field   : name of the secret field stored in the VaultItem payload
# env         : environment variable that shadows the vault value
# test_url    : (optional) endpoint for navig vault provider test
PROVIDERS: dict[str, dict] = {
    # ── AI / LLM providers ───────────────────────────────────────────────────
    "openai": {
        "label": "OpenAI",
        "key_field": "api_key",
        "env": "OPENAI_API_KEY",
        "test_url": "https://api.openai.com/v1/models",
    },
    "anthropic": {
        "label": "Anthropic",
        "key_field": "api_key",
        "env": "ANTHROPIC_API_KEY",
        "test_url": "https://api.anthropic.com/v1/messages",
    },
    "openrouter": {
        "label": "OpenRouter",
        "key_field": "api_key",
        "env": "OPENROUTER_API_KEY",
        "test_url": "https://openrouter.ai/api/v1/auth/key",
    },
    "groq": {
        "label": "Groq",
        "key_field": "api_key",
        "env": "GROQ_API_KEY",
        "test_url": "https://api.groq.com/openai/v1/models",
    },
    "mistral": {
        "label": "Mistral AI",
        "key_field": "api_key",
        "env": "MISTRAL_API_KEY",
    },
    "cohere": {
        "label": "Cohere",
        "key_field": "api_key",
        "env": "COHERE_API_KEY",
    },
    # ── Code & DevOps ────────────────────────────────────────────────────────
    "github": {
        "label": "GitHub",
        "key_field": "token",
        "env": "GITHUB_TOKEN",
        "test_url": "https://api.github.com/user",
    },
    # Separate PAT for Self-Heal contribution submissions (requires repo + fork
    # scopes).  Kept isolated from the "github" entry which is used for GitHub
    # Models LLM access and should remain read-only.
    "github_contribute": {
        "label": "GitHub (Self-Heal Contributions)",
        "key_field": "token",
        "env": "NAVIG_GITHUB_TOKEN",
        "test_url": "https://api.github.com/user",
    },
    "gitlab": {
        "label": "GitLab",
        "key_field": "token",
        "env": "GITLAB_TOKEN",
        "test_url": "https://gitlab.com/api/v4/user",
    },
    "vercel": {
        "label": "Vercel",
        "key_field": "token",
        "env": "VERCEL_TOKEN",
    },
    # ── Messaging / bots ──────────────────────────────────────────────────────
    "telegram": {
        "label": "Telegram Bot",
        "key_field": "bot_token",
        "env": "TELEGRAM_BOT_TOKEN",
        "test_url": "https://api.telegram.org/bot{token}/getMe",
    },
    "slack": {
        "label": "Slack",
        "key_field": "bot_token",
        "env": "SLACK_BOT_TOKEN",
    },
    "discord": {
        "label": "Discord",
        "key_field": "bot_token",
        "env": "DISCORD_BOT_TOKEN",
    },
    # ── Cloud / infra ─────────────────────────────────────────────────────────
    "aws": {
        "label": "AWS",
        "key_field": "secret_access_key",
        "env": "AWS_SECRET_ACCESS_KEY",
        "extra_fields": ["access_key_id", "region"],
    },
    "gcp": {
        "label": "Google Cloud",
        "key_field": "service_account_json",
        "env": "GOOGLE_APPLICATION_CREDENTIALS",
    },
    "azure": {
        "label": "Azure",
        "key_field": "client_secret",
        "env": "AZURE_CLIENT_SECRET",
    },
    "example-vps": {
        "label": "Hetzner Cloud",
        "key_field": "api_token",
        "env": "HCLOUD_TOKEN",
    },
    "digitalocean": {
        "label": "DigitalOcean",
        "key_field": "api_token",
        "env": "DIGITALOCEAN_TOKEN",
    },
    # ── Payments ──────────────────────────────────────────────────────────────
    "stripe": {
        "label": "Stripe",
        "key_field": "secret_key",
        "env": "STRIPE_SECRET_KEY",
    },
    # ── Data / storage ────────────────────────────────────────────────────────
    "supabase": {
        "label": "Supabase",
        "key_field": "service_role_key",
        "env": "SUPABASE_SERVICE_ROLE_KEY",
    },
    "planetscale": {
        "label": "PlanetScale",
        "key_field": "password",
        "env": "PLANETSCALE_PASSWORD",
    },
}


def get_provider(name: str) -> Optional[dict]:
    """Return provider config for *name*, or None if unknown."""
    return PROVIDERS.get(name.lower())


def list_providers() -> list[str]:
    """Return sorted list of known provider names."""
    return sorted(PROVIDERS.keys())


def resolve_env(provider_name: str) -> Optional[str]:
    """Return the value of the provider's env var, or None if unset/empty.

    Supports format strings in test_url (e.g. ``{token}``).
    """
    import os

    meta = get_provider(provider_name)
    if not meta:
        return None
    env_var = meta.get("env", "")
    value = os.environ.get(env_var, "").strip()
    return value if value else None
