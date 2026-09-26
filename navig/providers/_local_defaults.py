"""
navig.providers._local_defaults — zero-dependency leaf for local provider URLs.

The canonical defaults live here so every module can import a named constant
instead of repeating the same URL literal.

Two variants per provider:
  *_BASE_URL   — explicit loopback (127.0.0.1), used for internal probes and
                  routing dictionaries.
  *_USER_BASE_URL — localhost, used for user-facing defaults (config, TUI,
                    provider constructors) where the user may also substitute
                    a remote host.
"""

from __future__ import annotations

# ── Ollama ────────────────────────────────────────────────────────────────────

# Internal probe / routing dict default  →  explicit loopback
_OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"

# User-facing config default  →  symbolic localhost
_OLLAMA_USER_BASE_URL: str = "http://localhost:11434"

#: Env var and config key that move Ollama off this machine.
_OLLAMA_ENV_VARS: tuple[str, ...] = ("NAVIG_OLLAMA_URL", "OLLAMA_HOST")
_OLLAMA_CONFIG_KEY = "ai.ollama_host"


def _normalise_base(value: str, default: str) -> str:
    """A user-typed host → a usable base URL. "" when *value* is empty."""
    text = (value or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    # A bare host with no port means the provider's default port.
    tail = text.split("://", 1)[1]
    if ":" not in tail.split("/", 1)[0]:
        port = default.rsplit(":", 1)[-1]
        text = f"{text}:{port}"
    return text.rstrip("/")


def ollama_base_url() -> str:
    """Where Ollama actually lives: ``NAVIG_OLLAMA_URL`` / ``OLLAMA_HOST`` env, then
    ``ai.ollama_host`` in the global config, else this machine.

    The constant used to be the only answer, so a model running on another box in the
    house was unreachable from the mode router — and ``ai.ollama_host`` (which the setup
    wizard has always written) was read by nothing. Resolution is deliberately cheap and
    never raises: a missing/broken config just means loopback.
    """
    import os

    for var in _OLLAMA_ENV_VARS:
        resolved = _normalise_base(os.environ.get(var, ""), _OLLAMA_BASE_URL)
        if resolved:
            return resolved
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config or {}
        section, _, key = _OLLAMA_CONFIG_KEY.partition(".")
        resolved = _normalise_base(str((cfg.get(section) or {}).get(key) or ""), _OLLAMA_BASE_URL)
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 — no config is not an error, it is the default
        pass
    return _OLLAMA_BASE_URL


def ollama_is_local() -> bool:
    """True when Ollama runs on this machine (so navig may start it)."""
    host = ollama_base_url().split("://", 1)[-1].split(":", 1)[0].lower()
    return host in ("127.0.0.1", "localhost", "::1", "[::1]")


# ── llama.cpp ─────────────────────────────────────────────────────────────────

# Internal probe / routing dict default  →  explicit loopback
_LLAMACPP_BASE_URL: str = "http://127.0.0.1:8080"

# User-facing config default  →  symbolic localhost
_LLAMACPP_USER_BASE_URL: str = "http://localhost:8080"
