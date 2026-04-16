"""
Cheap-turn model routing for NAVIG.

Ported and adapted from hermes-agent ``agent/smart_model_routing.py``.

For inexpensive conversational turns (short, plain-text, no code/tools), the
router can redirect to a cheaper/faster model rather than always using the
configured primary model.  This reduces latency and cost without sacrificing
quality for simple requests.

The caller decides whether to use the returned route:

    from navig.core.model_routing import choose_cheap_model_route

    route = choose_cheap_model_route(user_message, routing_config)
    if route:
        # use route["provider"] / route["model"]
        ...
    else:
        # use primary model
        ...

The ``routing_config`` dict (from ``config/defaults.yaml`` or runtime overrides)
is expected to have the shape::

    cheap_model_routing:
      enabled: true
      max_simple_chars: 160
      max_simple_words: 28
      cheap_model:
        provider: deepseek
        model: deepseek-chat

All tunables come from config — no hardcoded literals per the single-source-of-truth rule.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Keyword signals for "complex" turns that should keep the primary model
# ---------------------------------------------------------------------------

_COMPLEX_KEYWORDS: frozenset[str] = frozenset({
    "debug",
    "debugging",
    "implement",
    "implementation",
    "refactor",
    "patch",
    "traceback",
    "stacktrace",
    "exception",
    "error",
    "analyze",
    "analysis",
    "investigate",
    "architecture",
    "design",
    "compare",
    "benchmark",
    "optimize",
    "optimise",
    "review",
    "terminal",
    "shell",
    "tool",
    "tools",
    "pytest",
    "test",
    "tests",
    "plan",
    "planning",
    "delegate",
    "subagent",
    "cron",
    "docker",
    "kubernetes",
    "deploy",
    "deployment",
    "script",
    "workflow",
})

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

# Default tunable values (also declared in config/defaults.yaml).
_DEFAULT_MAX_SIMPLE_CHARS = 160
_DEFAULT_MAX_SIMPLE_WORDS = 28


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    if isinstance(value, int):
        return bool(value)
    return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_simple_turn(
    message: str,
    *,
    max_chars: int = _DEFAULT_MAX_SIMPLE_CHARS,
    max_words: int = _DEFAULT_MAX_SIMPLE_WORDS,
) -> bool:
    """Return ``True`` when *message* looks like a simple conversational turn.

    Conservative: any sign of code, tools, debugging, or long-form work
    returns ``False`` so the primary (stronger) model is used.

    Parameters
    ----------
    message:
        The raw user message text.
    max_chars:
        Character length budget for a "simple" message (default: 160).
    max_words:
        Word count budget (default: 28).
    """
    text = (message or "").strip()
    if not text:
        return False
    if len(text) > max_chars:
        return False
    if len(text.split()) > max_words:
        return False
    if text.count("\n") > 1:
        return False
    if "```" in text or "`" in text:
        return False
    if _URL_RE.search(text):
        return False

    lowered = text.lower()
    words = {token.strip(".,:;!?()'\"[]{}") for token in lowered.split()}
    if words & _COMPLEX_KEYWORDS:
        return False

    return True


def choose_cheap_model_route(
    user_message: str,
    routing_config: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Return the cheap-model route dict when a message looks simple.

    If the message is not simple, or if the routing config is disabled or
    missing, returns ``None`` (meaning: use the primary model).

    Parameters
    ----------
    user_message:
        Raw user message text.
    routing_config:
        Dict under the ``cheap_model_routing`` config key, or ``None``.

    Returns
    -------
    dict or None
        When routing is triggered, returns a dict with at minimum
        ``"provider"``, ``"model"``, and ``"routing_reason"`` keys.
    """
    cfg = routing_config or {}
    if not _coerce_bool(cfg.get("enabled"), default=False):
        return None

    cheap_model = cfg.get("cheap_model") or {}
    if not isinstance(cheap_model, dict):
        return None

    provider = str(cheap_model.get("provider") or "").strip().lower()
    model = str(cheap_model.get("model") or "").strip()
    if not provider or not model:
        return None

    max_chars = _coerce_int(cfg.get("max_simple_chars"), default=_DEFAULT_MAX_SIMPLE_CHARS)
    max_words = _coerce_int(cfg.get("max_simple_words"), default=_DEFAULT_MAX_SIMPLE_WORDS)

    if not is_simple_turn(user_message, max_chars=max_chars, max_words=max_words):
        return None

    route = {
        **cheap_model,
        "provider": provider,
        "model": model,
        "routing_reason": "simple_turn",
    }
    return route


def get_routing_config() -> Optional[dict[str, Any]]:
    """Return the ``cheap_model_routing`` section from the active config.

    Returns ``None`` when the config manager is unavailable or the key is
    not set.  Callers should treat ``None`` the same as ``{"enabled": false}``.
    """
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        value = cfg.get("cheap_model_routing")
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001
        return None
