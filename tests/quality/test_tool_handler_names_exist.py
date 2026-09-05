"""A tool's declared `handler_name` must name something that exists and can load.

`ToolMeta.handler_name` is a **string**, resolved at runtime by
``ToolRegistry.get_handler`` via ``getattr(module, meta.handler_name)``. Nothing
type-checks a string, so `code_sandbox` shipped declaring:

    module_path  = "navig.tools.sandbox"
    handler_name = "execute"          # the module defines sandboxed_execute

`get_handler()` caught the AttributeError and returned None, so the tool sat in
the LLM prompt as available (its status only flips to ERROR *after* something
tries to load it) and answered "No handler loaded" when the model called it —
along with its three aliases: sandbox / exec / run_code.

Same class as `core/tests/quality/test_channel_adapter_classes_exist.py` and as
`scripts/check_module_attrs.py`, which cannot see it: the name lives in a
string, not in an attribute access.
"""
from __future__ import annotations

import importlib

import pytest

from navig.tools.router import get_tool_registry

_registry = get_tool_registry()
_registry.initialize()

_DECLARED = [
    (name, meta.module_path, meta.handler_name)
    for name, meta in sorted(_registry._tools.items())
    if meta.module_path and meta.handler_name
]


def test_some_tool_actually_declares_a_lazy_handler() -> None:
    """Anti-vacuity: the checks below are parametrised over `_DECLARED`. If every
    tool switched to a pre-bound `handler=`, this file would go green having
    verified nothing — and the guard would need re-pointing, not deleting."""
    assert _DECLARED, "no tool declares module_path + handler_name; this guard is inert"


@pytest.mark.parametrize("tool,module_path,handler_name", _DECLARED)
def test_the_declared_handler_exists(tool: str, module_path: str, handler_name: str) -> None:
    module = importlib.import_module(module_path)

    assert hasattr(module, handler_name), (
        f"tool {tool!r} declares handler_name={handler_name!r}, which does not exist in "
        f"{module_path}. `get_handler()` swallows the AttributeError and returns None, so "
        f"the tool is offered to the model and then answers 'No handler loaded'."
    )


@pytest.mark.parametrize("tool,module_path,handler_name", _DECLARED)
def test_the_registry_can_actually_load_it(tool: str, module_path: str, handler_name: str) -> None:
    """The end-to-end check: the one above explains *why* a load fails, this is
    the behaviour a caller sees."""
    assert _registry.get_handler(tool) is not None, (
        f"registry.get_handler({tool!r}) returned None despite declaring a lazy handler"
    )


@pytest.mark.parametrize("tool,module_path,handler_name", _DECLARED)
def test_the_declared_handler_is_callable(tool: str, module_path: str, handler_name: str) -> None:
    """`execute()` invokes it as `handler(**parameters)`. A module-level constant
    or a class attribute that happens to share the name would pass the existence
    check and then raise TypeError on the first real call."""
    module = importlib.import_module(module_path)
    handler = getattr(module, handler_name)

    assert callable(handler), (
        f"tool {tool!r} declares handler_name={handler_name!r} in {module_path}, but it is "
        f"{type(handler).__name__}, not callable"
    )


def test_every_tool_offered_to_the_model_can_actually_be_dispatched() -> None:
    """The whole-surface check, and the one that would have caught this first.

    `get_tools_for_llm_prompt(available_only=True)` is what the model is told it
    can call. Measured before the fix: 17 advertised, 1 (`code_sandbox`) whose
    handler could not load — so the model spent a turn on a tool that could only
    answer "No handler loaded". This covers pre-bound `handler=` tools too, which
    the parametrised checks above cannot see.
    """
    advertised = [t["name"] for t in _registry.get_tools_for_llm_prompt(available_only=True)]
    assert advertised, "the model is offered no tools at all; this guard is inert"

    undispatchable = [name for name in advertised if _registry.get_handler(name) is None]
    assert not undispatchable, (
        f"advertised to the model but not dispatchable: {undispatchable}"
    )


def test_an_unavailable_tool_states_a_reason() -> None:
    """The other half. Declining to wire a handler is legitimate — `code_sandbox`
    has no adapter for its own schema — but then the tool must say so rather than
    advertise itself as available and fail on use."""
    for name, meta in _registry._tools.items():
        if meta.is_available():
            continue
        assert meta.status_message, (
            f"tool {name!r} is {meta.status.value} with no status_message; a caller "
            f"(and the operator) cannot tell why it will not run"
        )
