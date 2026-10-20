"""navig.agent.conv.sources — the user's switches over where answers come from.

Config keys (global ``~/.navig/config.yaml``; the OS Personalization page writes them):

* ``ai.web_search.mode`` — ``auto`` (default: the model gets web tools when the turn
  calls for them, as before) · ``always`` (every turn is offered web search) · ``off``
  (no web search, page fetch or browser — the agent answers from what it has).
* ``ai.fast_answers`` — bool, default off. On: a turn the classifier rates SIMPLE goes
  to the fast small model instead of the heavy one. An explicit tier always wins.
* ``ai.sources.library`` / ``ai.sources.connectors`` — bool, default on. Read by the
  unified search tool to decide which of the user's own sources it may search.

Every reader is fail-safe: an unreadable or invalid value means the default.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

WEB_MODES: tuple[str, ...] = ("auto", "always", "off")
#: Tool names that reach the open web. "off" removes every one of them.
WEB_TOOLS: frozenset[str] = frozenset({"search", "web_fetch", "browser_tool"})
#: Toolsets that exist only to reach the web.
WEB_TOOLSETS: frozenset[str] = frozenset({"search", "browser"})


def _cfg_get(key: str, default: Any) -> Any:
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        read = getattr(cm, "get_live", cm.get)  # a terminal `config set` applies at once
        return read(key, default)
    except Exception as exc:  # noqa: BLE001 — a bad setting must never break a turn
        logger.debug("config %s unreadable (%s); using default", key, exc)
        return default


def normalize_web_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in WEB_MODES else "auto"


def web_search_mode() -> str:
    return normalize_web_mode(_cfg_get("ai.web_search.mode", "auto"))


def apply_web_search_mode(toolsets: list[str], mode: str) -> list[str]:
    """Toolsets for a turn after the user's web-search switch."""
    if mode == "always":
        return toolsets if "search" in toolsets else [*toolsets, "search"]
    if mode == "off":
        return [t for t in toolsets if t not in WEB_TOOLSETS]
    return toolsets


def drop_web_tools(schemas: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """With web search off, remove web tools that rode in on a mixed toolset
    (``research`` and ``code`` both carry ``search``/``web_fetch``)."""
    if mode != "off":
        return schemas
    return [s for s in schemas if (s.get("function") or {}).get("name") not in WEB_TOOLS]


def fast_answers_enabled() -> bool:
    from navig.core.coerce import coerce_bool

    return coerce_bool(_cfg_get("ai.fast_answers", False), default=False)


def source_enabled(name: str) -> bool:
    """``ai.sources.<name>`` (``library`` / ``connectors``), default on."""
    from navig.core.coerce import coerce_bool

    return coerce_bool(_cfg_get(f"ai.sources.{name}", True), default=True)


def is_simple_request(message: str) -> bool:
    """True when the routing classifier rates *message* SIMPLE (sub-millisecond, no LLM)."""
    try:
        from navig.agent.routing_strategy import classify_request

        return classify_request([{"role": "user", "content": message or ""}]).tier == "SIMPLE"
    except Exception as exc:  # noqa: BLE001
        logger.debug("fast-answers classification skipped: %s", exc)
        return False
