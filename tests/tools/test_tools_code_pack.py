"""Tests for navig/tools/domains/code_pack.py."""

from unittest.mock import MagicMock

from navig.tools.domains.code_pack import register_tools

# ---------------------------------------------------------------------------
# register_tools — basic invocation
# ---------------------------------------------------------------------------

def test_register_tools_calls_registry_register():
    registry = MagicMock()
    register_tools(registry)
    registry.register.assert_called_once()


def test_register_tools_meta_name_is_code_sandbox():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.name == "code_sandbox"


def test_register_tools_meta_domain_is_code():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "CODE" in str(meta.domain).upper()


def test_register_tools_meta_safety_is_dangerous():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "DANGEROUS" in str(meta.safety).upper()


def test_register_tools_declares_no_lazy_handler():
    """These two assertions used to pin `module_path="navig.tools.sandbox"` and
    `handler_name="execute"` — and `navig.tools.sandbox` has never defined
    `execute` (it exposes `sandboxed_execute`). So the tests were green while
    `get_handler()` caught the AttributeError and returned None on every call:
    the test asserted the *typo*, not the contract.

    There is deliberately no lazy handler now. Wiring one is not a rename — the
    real function takes a shell `command` while this tool's schema promises
    `code` + `language` — so the tool reports UNAVAILABLE until an adapter
    exists. `tests/quality/test_tool_handler_names_exist.py` fails the build if
    a declared handler name ever stops resolving again.
    """
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]

    assert meta.module_path is None
    assert meta.handler_name is None


def test_register_tools_meta_is_unavailable_with_a_reason():
    from navig.tools.router import ToolStatus

    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]

    assert meta.status is ToolStatus.UNAVAILABLE
    assert not meta.is_available()
    assert meta.status_message, "an unavailable tool must say why"


# ---------------------------------------------------------------------------
# register_tools — ToolMeta parameters schema
# ---------------------------------------------------------------------------

def test_register_tools_meta_has_code_parameter():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "code" in meta.parameters_schema


def test_register_tools_meta_code_param_required():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.parameters_schema["code"]["required"] is True


def test_register_tools_meta_code_param_is_string_type():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.parameters_schema["code"]["type"] == "string"


def test_register_tools_meta_has_language_parameter():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "language" in meta.parameters_schema


def test_register_tools_meta_language_default_is_python():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.parameters_schema["language"]["default"] == "python"


def test_register_tools_meta_has_timeout_parameter():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "timeout" in meta.parameters_schema


def test_register_tools_meta_timeout_default_is_300():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.parameters_schema["timeout"]["default"] == 300


# ---------------------------------------------------------------------------
# register_tools — tags
# ---------------------------------------------------------------------------

def test_register_tools_meta_tags_contains_code():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "code" in meta.tags


def test_register_tools_meta_tags_contains_sandbox():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "sandbox" in meta.tags


def test_register_tools_meta_tags_contains_docker():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "docker" in meta.tags


# ---------------------------------------------------------------------------
# register_tools — description
# ---------------------------------------------------------------------------

def test_register_tools_meta_description_mentions_sandbox():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "sandbox" in meta.description.lower() or "docker" in meta.description.lower()


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

def test_register_tools_is_callable():
    assert callable(register_tools)


def test_register_tools_no_handler_in_register_kwargs():
    """code_pack uses module_path/handler_name, not a direct handler= kwarg."""
    registry = MagicMock()
    register_tools(registry)
    call_kwargs = registry.register.call_args[1]
    # handler kwarg should be absent (module_path pattern used instead)
    assert "handler" not in call_kwargs
