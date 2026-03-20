"""
navig.inbox — Inbox Neuron Router

Global cross-platform file/URL inbox with:
- Filesystem watcher (watchfiles >= 0.21)
- LLM-based classifier with BM25 keyword fallback
- COPY / MOVE / LINK dispatch modes with conflict resolution
- Pre/post routing hook system
- SQLite event persistence via storage.engine
"""

from navig.inbox.classifier import Classifier, ClassifyResult
from navig.inbox.hooks import HookEvent, HookSystem
from navig.inbox.router import InboxRouter, RouteMode, RouteResult
from navig.inbox.store import InboxStore

__all__ = [
    "Classifier",
    "ClassifyResult",
    "InboxRouter",
    "RouteMode",
    "RouteResult",
    "HookSystem",
    "HookEvent",
    "InboxStore",
]
