"""Who decided this browser should be visible — and were they in the room?

**The defect this exists to fix.** Window visibility used to be a property of the
*controller*: ``SystemChromeController(headless=False)``, ``cdp_actions.new(headless=False)``,
``StealthConfig.headless = False``. That default has a legitimate origin — ``cdp login`` and
``cdp profile open`` need a visible window so a *human* can type a password, and
``test_system_chrome.py`` pins it with the comment ``# headful by default (human login)``.

But a default set at the controller is inherited by *every* caller, including the ones with
no human attached. The MCP schema for ``cdp_new`` advertised ``"headless": {"default":
False, "description": "…opt-in…"}``, so an autonomous agent — which has no eyes and no
reason to opt in — opened a **real window on the operator's screen** on every call. Those
windows render blank, because NAVIG drives content in a tab target it then closes, and they
accumulate, because browsers are spawned ``DETACHED_PROCESS`` and outlive the CLI. The
operator watched stacks of empty windows pile up during unattended sessions.

So visibility is not the controller's business. It belongs to the **caller's context**:

    agent   an LLM/MCP/gateway call. Nobody is watching.        -> headless
    script  cron, harness, CI, batch claim jobs. Unattended.    -> headless
    human   the operator typed the command and is looking at    -> visible
            the screen (``cdp login``, ``profile open``, ``do``)

An explicit argument always wins, so ``--headed`` / ``--headless`` still mean exactly what
they say and no existing caller changes behaviour. Between the two sits one operator-level
override, ``browser.headless``.

⚠ That config key only works because ``BrowserConfig`` is declared in
``navig.core.config_schema``. ``_load_global_config(validate=True)`` returns
``GlobalConfig(...).model_dump()`` and ``GlobalConfig`` does not set ``extra="allow"``, so an
undeclared top-level key is dropped on the way out and every read of it returns the caller's
default forever. Verified before this module was written: a raw config carrying
``{"browser": {"headless": True}}`` came back from validation with no ``browser`` key at all.
Do not add a setting here without adding it to the schema too.
"""

from __future__ import annotations

from typing import Any, Literal

__all__ = ["LaunchContext", "resolve_headless", "context_default"]

LaunchContext = Literal["agent", "script", "human"]

# The whole policy, in one table. A context missing from here is a programming error, not a
# reason to guess — see `context_default`.
_CONTEXT_DEFAULT: dict[str, bool] = {
    "agent": True,  # no eyes attached
    "script": True,  # unattended by definition
    "human": False,  # they asked to watch, and may need to log in
}


def context_default(context: str) -> bool:
    """Default headless value for *context*.

    Raises ``ValueError`` for an unknown context. A silent fallback here would be the
    original bug wearing a different hat: the caller whose context nobody classified is
    exactly the caller that should not be opening windows by accident.
    """
    try:
        return _CONTEXT_DEFAULT[context]
    except KeyError:
        raise ValueError(
            f"unknown launch context {context!r}; expected one of "
            f"{sorted(_CONTEXT_DEFAULT)}"
        ) from None


def _configured_headless() -> Any:
    """Raw ``browser.headless`` from global config, or ``None`` when unset/unreadable.

    Never raises. A browser launch must not fail because the config file is momentarily
    locked by an antivirus scan — that would turn a cosmetic preference into an outage.
    ``None`` means "the operator expressed no preference", which is distinct from a
    configured ``false``; collapsing those two is what makes a tri-state setting useless.
    """
    try:
        from navig.config import get_config_manager  # noqa: PLC0415 — lazy: keeps `navig help` fast

        return get_config_manager().get("browser.headless", None)
    except Exception:  # noqa: BLE001 — config is advisory here, never load-bearing
        return None


def resolve_headless(explicit: bool | None = None, *, context: LaunchContext) -> bool:
    """Decide whether this launch gets a window.

    Precedence, highest first:

    1. *explicit* — the caller said so (``--headless`` / ``--headed``, an MCP argument).
       ``None`` means "not specified", which is why this parameter is tri-state and not a
       plain ``bool``: ``False`` has to be distinguishable from "unset" or ``--headed``
       could never override a headless context.
    2. ``browser.headless`` in global config — the operator's standing preference.
    3. The context default.

    Args:
        explicit: What the caller asked for, or ``None`` if they did not ask.
        context: Who is launching — ``"agent"``, ``"script"`` or ``"human"``.

    Returns:
        ``True`` to launch windowless.

    Examples:
        >>> resolve_headless(context="agent")            # nobody is watching
        True
        >>> resolve_headless(context="human")            # a login flow
        False
        >>> resolve_headless(False, context="agent")     # --headed wins
        False
    """
    fallback = context_default(context)

    if explicit is not None:
        return bool(explicit)

    configured = _configured_headless()
    if configured is not None:
        # `navig config set` stores values verbatim as strings, so a configured "false"
        # arrives as the truthy string "false". coerce_bool is the one canonical fix.
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        return coerce_bool(configured, default=fallback)

    return fallback
