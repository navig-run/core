"""ActionRegistry: single source of truth for all dispatchable action handlers.

Replaces the hardcoded ``if action == ...`` cascade in TaskExecutor._execute_step.
Each handler is an async callable (params: dict[str, Any]) -> Any.

Usage
-----
Registration (done once at import time inside _register_core_actions)::

    registry = get_action_registry()

    @registry.register("my.action", requires_params=True)
    async def _my_handler(params: dict[str, Any]) -> Any:
        ...

Dispatch from executor::

    matched, result = await registry.dispatch(action_id, params)
    if not matched:
        # fall through to ToolRouter
        ...

Planner integration::

    KNOWN_ACTIONS = get_action_registry().known_ids()
    ACTIONS_REQUIRING_PARAMS = get_action_registry().requires_params_ids()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Type alias for handler callables
ActionHandler = Callable[[dict[str, Any]], Awaitable[Any]]


def _chk(result: object) -> object:
    """Raise RuntimeError if *result* carries an automation-adapter failure signal."""
    if hasattr(result, "success") and not result.success:
        raise RuntimeError(getattr(result, "stderr", str(result)))
    return result


def _get_adapter():
    """Lazy-load WorkflowEngine and return its automation adapter (fail-fast if unavailable)."""
    from navig.core.automation_engine import WorkflowEngine  # type: ignore[import]

    engine = WorkflowEngine()
    adapter = engine.adapter
    if not adapter or not adapter.is_available():
        raise RuntimeError("Automation adapter not available")
    return adapter


class ActionRegistry:
    """Registry that maps action IDs to async handler callables.

    Design goals:
    - Zero hardcoded action strings anywhere else in the codebase.
    - Single place to add/remove/replace an action implementation.
    - Safe dispatch: always returns (matched, result) — never raises for missing IDs.
    - Planner/validator integration via .known_ids() / .requires_params_ids().
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}
        self._requires_params: set[str] = set()

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register(
        self,
        action_id: str,
        *,
        requires_params: bool = False,
    ) -> Callable[[ActionHandler], ActionHandler]:
        """Decorator that registers an async handler for ``action_id``.

        Parameters
        ----------
        action_id:
            Unique action identifier string (e.g. ``"auto.click"``).
        requires_params:
            If *True*, PlanValidator will require a non-empty ``params`` block
            for any plan step that uses this action.
        """

        def decorator(fn: ActionHandler) -> ActionHandler:
            if action_id in self._handlers:
                logger.warning("ActionRegistry: overwriting handler for %r", action_id)
            self._handlers[action_id] = fn
            if requires_params:
                self._requires_params.add(action_id)
            return fn

        return decorator

    # ------------------------------------------------------------------
    # Dispatch API
    # ------------------------------------------------------------------

    async def dispatch(
        self, action_id: str, params: dict[str, Any]
    ) -> tuple[bool, Any]:
        """Dispatch *action_id* with *params*.

        Returns
        -------
        tuple[bool, Any]
            ``(True, result)`` when a handler is found and executed,
            ``(False, None)`` when no handler is registered for *action_id*.

        This method never raises for missing actions — callers handle that case
        (e.g. by falling through to the ToolRouter).
        """
        handler = self._handlers.get(action_id)
        if handler is None:
            return False, None
        result = await handler(params)
        return True, result

    # ------------------------------------------------------------------
    # Introspection (used by planner.py)
    # ------------------------------------------------------------------

    def known_ids(self) -> frozenset[str]:
        """Return the set of all registered action identifiers."""
        return frozenset(self._handlers.keys())

    def requires_params_ids(self) -> frozenset[str]:
        """Return the subset of registered IDs that require a params block."""
        return frozenset(self._requires_params)

    def is_registered(self, action_id: str) -> bool:
        """Return True if *action_id* has a registered handler."""
        return action_id in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ActionRegistry({len(self._handlers)} actions)"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: ActionRegistry | None = None


def get_action_registry() -> ActionRegistry:
    """Return the process-wide ActionRegistry singleton (lazy-initialised)."""
    global _registry
    if _registry is None:
        _registry = ActionRegistry()
        _register_core_actions(_registry)
    return _registry


def _register_core_actions(reg: ActionRegistry) -> None:
    """Register all built-in action handlers.

    This is the *only* place where action implementations live.
    Every ``if action == ...`` branch previously in executor.py is now here.
    """

    # ── Fast-path actions (no engine/adapter needed) ─────────────────────────

    @reg.register("wait")
    async def _wait(params: dict[str, Any]) -> str:
        await asyncio.sleep(params.get("seconds", 1))
        return "Waited"

    @reg.register("command", requires_params=True)
    async def _command(params: dict[str, Any]) -> str:
        import subprocess

        from navig.config import get_config_manager

        timeout: int = (
            get_config_manager()
            .global_config.get("executor", {})
            .get("command_timeout_seconds", 60)
        )
        cmd = params.get("cmd", "")
        res = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr or f"Exit code: {res.returncode}")
        return res.stdout

    @reg.register("evolve.workflow", requires_params=True)
    async def _evolve_workflow(params: dict[str, Any]) -> str:
        from navig.core.evolution.workflow import WorkflowEvolver  # type: ignore[import]

        return f"Created: {WorkflowEvolver().evolve(params.get('goal', ''))}"

    # ── Engine-dependent actions ─────────────────────────────────────────────

    @reg.register("workflow.run", requires_params=True)
    async def _workflow_run(params: dict[str, Any]) -> Any:
        from navig.core.automation_engine import WorkflowEngine  # type: ignore[import]

        engine = WorkflowEngine()
        name = params.get("name", "")
        wf = engine.load_workflow(name)
        if not wf:
            raise RuntimeError(f"Workflow {name!r} not found")
        return engine.execute_workflow(wf, params.get("variables", {}))

    # ── Adapter-dependent auto.* actions ─────────────────────────────────────

    @reg.register("auto.open_app", requires_params=True)
    async def _auto_open_app(params: dict[str, Any]) -> Any:
        return _chk(_get_adapter().open_app(params.get("target", "")))

    @reg.register("auto.click", requires_params=True)
    async def _auto_click(params: dict[str, Any]) -> Any:
        return _chk(
            _get_adapter().click(
                params.get("x"), params.get("y"), params.get("button", "left")
            )
        )

    @reg.register("auto.type", requires_params=True)
    async def _auto_type(params: dict[str, Any]) -> Any:
        return _chk(
            _get_adapter().type_text(params.get("text", ""), params.get("delay", 50))
        )

    @reg.register("auto.snap_window")
    async def _auto_snap_window(params: dict[str, Any]) -> Any:
        return _chk(
            _get_adapter().snap_window(
                params.get("selector", ""), params.get("position", "left")
            )
        )

    @reg.register("auto.get_focused_window")
    async def _auto_get_focused_window(params: dict[str, Any]) -> Any:
        return _get_adapter().get_focused_window()

    @reg.register("auto.windows")
    async def _auto_windows(params: dict[str, Any]) -> list:
        adapter = _get_adapter()
        return [
            w.to_dict() if hasattr(w, "to_dict") else str(w)
            for w in adapter.get_all_windows()
        ]

    @reg.register("auto.get_clipboard")
    async def _auto_get_clipboard(params: dict[str, Any]) -> Any:
        return _get_adapter().get_clipboard()

    @reg.register("auto.set_clipboard", requires_params=True)
    async def _auto_set_clipboard(params: dict[str, Any]) -> Any:
        return _get_adapter().set_clipboard(params.get("text", ""))
