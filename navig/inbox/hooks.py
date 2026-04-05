"""
navig.inbox.hooks — Pre/post routing hook system.

Hooks are registered globally or per-session and fired at well-known
events in the inbox pipeline.  Each hook receives a HookEvent and can:

 - Return None  → event continues unchanged
 - Return a modified HookEvent → downstream uses the modified version
 - Raise HookAbort → pipeline stops for this item (item stays in inbox)

Hooks can be defined inline (for tests and scripts) or discovered from
`skills.json` entries that declare an `inbox_hooks` section.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.inbox.hooks")

class HookAbort(Exception):
    """Raised by a hook to abort routing for the current inbox item."""

    def __init__(self, reason: str = "aborted by hook") -> None:
        super().__init__(reason)
        self.reason = reason

@dataclass
class HookEvent:
    """Payload passed to every hook."""

    # Which pipeline stage fired this hook
    stage: str  # "before_classify" | "after_classify" | "before_route" | "after_route"
    source_path: str  # file path or URL
    source_type: str  # "file" | "url" | "telegram"
    filename: str
    content: str  # raw text (may be empty for binary files)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Set by classifier stage
    category: str | None = None
    confidence: float | None = None

    # Set by router stage
    destination: str | None = None
    route_status: str | None = None

    fired_at: float = field(default_factory=time.time)

HookFn = Callable[[HookEvent], HookEvent | None]

class HookSystem:
    """
    Registry and executor for inbox routing hooks.

    Usage::

        hooks = HookSystem()

        @hooks.register("before_classify")
        def my_hook(event: HookEvent) -> HookEvent | None:
            if "PRIVATE" in event.content:
                raise HookAbort("private file — not routing")
            return event

        event = hooks.fire("before_classify", event)
    """

    # Valid stage names
    STAGES = frozenset({"before_classify", "after_classify", "before_route", "after_route"})

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {s: [] for s in self.STAGES}

    def register(
        self,
        stage: str,
        fn: HookFn | None = None,
    ) -> Any:
        """
        Register a hook.  Can be used as a decorator or direct call.

        As decorator::

            @hooks.register("before_classify")
            def fn(e): ...

        Direct call::

            hooks.register("before_classify", my_fn)
        """
        if stage not in self.STAGES:
            raise ValueError(f"Unknown hook stage: {stage!r}. Valid: {sorted(self.STAGES)}")

        if fn is None:
            # Used as decorator factory
            def _decorator(f: HookFn) -> HookFn:
                self._hooks[stage].append(f)
                return f

            return _decorator

        self._hooks[stage].append(fn)
        return fn

    def fire(self, stage: str, event: HookEvent) -> HookEvent:
        """
        Fire all hooks registered for *stage* in registration order.

        Each hook may return a modified event (or None to keep current).
        Raises HookAbort if any hook aborts the pipeline.
        """
        for hook in self._hooks.get(stage, []):
            try:
                result = hook(event)
                if result is not None:
                    event = result
            except HookAbort:
                raise  # propagate as-is
            except Exception as exc:
                logger.warning("Hook %s raised unexpectedly: %s", hook.__name__, exc)
        return event

    def clear(self, stage: str | None = None) -> None:
        """Remove all hooks (or hooks for a specific stage)."""
        if stage:
            self._hooks[stage] = []
        else:
            for s in self.STAGES:
                self._hooks[s] = []

    def load_from_skills(self, skills_path: Path) -> int:
        """
        Discover and register hooks declared in a skills.json file.

        Expected format::

            {
              "inbox_hooks": [
                {
                  "stage": "before_classify",
                  "module": "my_skill.hooks",
                  "fn": "my_hook_fn"
                }
              ]
            }

        Returns the number of hooks loaded.
        """
        if not skills_path.is_file():
            return 0
        import importlib
        import json

        try:
            data = json.loads(skills_path.read_text(encoding="utf-8"))
        except Exception:
            return 0

        loaded = 0
        for entry in data.get("inbox_hooks", []):
            stage = entry.get("stage", "")
            module_name = entry.get("module", "")
            fn_name = entry.get("fn", "")
            if not stage or not module_name or not fn_name:
                continue
            try:
                mod = importlib.import_module(module_name)
                fn = getattr(mod, fn_name)
                self.register(stage, fn)
                loaded += 1
            except Exception as exc:
                logger.warning("Could not load hook %s.%s: %s", module_name, fn_name, exc)
        return loaded

# ── Global default hook system ────────────────────────────────

_default_hooks = HookSystem()

def get_hooks() -> HookSystem:
    """Return the module-level default HookSystem."""
    return _default_hooks
