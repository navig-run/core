"""
NAVIG Unified LLM Router — single canonical routing module.

All entrypoints (Forge Chat, Telegram, CLI, MCP) call through here.
Eliminates the split-brain between llm_router.py and model_router.py.

Public API:
    route(request)  → RouteDecision (mode, provider, model, capabilities)
    run(request)    → LLMResponse   (route + execute + audit + retry)
    status()        → ProviderStatus (availability, models, health)
"""

from navig.routing.capabilities import CAPABILITY_TAGS, MODE_CAPABILITIES
from navig.routing.detect import detect_mode
from navig.routing.router import UnifiedRouter, get_router
from navig.routing.trace import RouteTrace

__all__ = [
    "UnifiedRouter",
    "get_router",
    "detect_mode",
    "MODE_CAPABILITIES",
    "CAPABILITY_TAGS",
    "RouteTrace",
]
