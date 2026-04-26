"""Tests for navig.registry.meta — CommandMeta, DeprecationInfo, command_meta decorator."""
from __future__ import annotations

import pytest

from navig.registry import meta as _meta_mod
from navig.registry.meta import (
    CommandMeta,
    DeprecationInfo,
    command_meta,
    get_meta_for_callback,
    get_registry,
)


def _clear_registry():
    """Empty the module-level registries to avoid test bleed."""
    _meta_mod._REGISTRY.clear()
    _meta_mod._BY_CALLBACK.clear()


class TestDeprecationInfo:
    def test_construction(self):
        d = DeprecationInfo(since="1.0", remove_after="2.0", replaced_by="new_cmd")
        assert d.since == "1.0"
        assert d.replaced_by == "new_cmd"

    def test_frozen(self):
        d = DeprecationInfo(since="1.0", remove_after="2.0", replaced_by="x")
        with pytest.raises((TypeError, AttributeError)):
            d.since = "2.0"  # type: ignore[misc]


class TestCommandMeta:
    def test_construction(self):
        meta = CommandMeta(summary="A command", status="stable", since="1.0")
        assert meta.summary == "A command"
        assert meta.status == "stable"
        assert meta.deprecated is None

    def test_defaults(self):
        meta = CommandMeta(summary="X", status="beta", since="0.1")
        assert meta.tags == []
        assert meta.aliases == []
        assert meta.examples == []

    def test_frozen(self):
        meta = CommandMeta(summary="X", status="stable", since="1.0")
        with pytest.raises((TypeError, AttributeError)):
            meta.summary = "Y"  # type: ignore[misc]

    def test_with_deprecation(self):
        dep = DeprecationInfo(since="1.5", remove_after="3.0", replaced_by="new")
        meta = CommandMeta(summary="old", status="deprecated", since="1.0", deprecated=dep)
        assert meta.deprecated is dep


class TestCommandMetaDecorator:
    def setup_method(self):
        _clear_registry()

    def test_decorator_returns_original_function(self):
        @command_meta(summary="Test cmd", status="stable", since="1.0")
        def my_cmd():
            pass

        assert callable(my_cmd)

    def test_function_has_meta_attr(self):
        @command_meta(summary="Test cmd", status="stable", since="1.0")
        def my_cmd():
            pass

        assert hasattr(my_cmd, _meta_mod._META_ATTR)
        meta = getattr(my_cmd, _meta_mod._META_ATTR)
        assert isinstance(meta, CommandMeta)

    def test_registry_populated(self):
        @command_meta(summary="reg cmd", status="stable", since="1.0")
        def reg_cmd():
            pass

        reg = get_registry()
        assert reg_cmd.__qualname__ in reg

    def test_get_meta_for_callback(self):
        @command_meta(summary="cb cmd", status="beta", since="0.5")
        def cb_cmd():
            pass

        meta = get_meta_for_callback(cb_cmd)
        assert meta is not None
        assert meta.summary == "cb cmd"

    def test_get_meta_for_none_returns_none(self):
        assert get_meta_for_callback(None) is None

    def test_get_meta_for_unknown_returns_none(self):
        def unknown():
            pass
        assert get_meta_for_callback(unknown) is None

    def test_deprecated_dict_converted_to_deprecation_info(self):
        @command_meta(
            summary="old cmd", status="deprecated", since="1.0",
            deprecated={"since": "1.5", "remove_after": "3.0", "replaced_by": "new_cmd"},
        )
        def old_cmd():
            pass

        meta = get_meta_for_callback(old_cmd)
        assert isinstance(meta.deprecated, DeprecationInfo)
        assert meta.deprecated.replaced_by == "new_cmd"

    def test_get_registry_returns_copy(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is not r2  # independent copies
