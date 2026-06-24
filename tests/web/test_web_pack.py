"""Tests for navig.tools.domains.web_pack — register_tools."""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from navig.tools.domains.web_pack import register_tools


class TestRegisterTools:
    def _mock_registry(self):
        return MagicMock()

    def test_calls_register_three_times(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        assert reg.register.call_count == 3

    def test_registers_web_search(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        names = [call.args[0].name for call in reg.register.call_args_list]
        assert "web_search" in names

    def test_registers_web_fetch(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        names = [call.args[0].name for call in reg.register.call_args_list]
        assert "web_fetch" in names

    def test_registers_docs_search(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        names = [call.args[0].name for call in reg.register.call_args_list]
        assert "docs_search" in names

    def test_web_search_has_query_param(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        metas = [c.args[0] for c in reg.register.call_args_list]
        ws = next(m for m in metas if m.name == "web_search")
        assert "query" in ws.parameters_schema

    def test_web_fetch_has_url_param(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        metas = [c.args[0] for c in reg.register.call_args_list]
        wf = next(m for m in metas if m.name == "web_fetch")
        assert "url" in wf.parameters_schema

    def test_web_search_has_web_tag(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        metas = [c.args[0] for c in reg.register.call_args_list]
        ws = next(m for m in metas if m.name == "web_search")
        assert "web" in ws.tags

    def test_docs_search_has_docs_tag(self) -> None:
        reg = self._mock_registry()
        register_tools(reg)
        metas = [c.args[0] for c in reg.register.call_args_list]
        ds = next(m for m in metas if m.name == "docs_search")
        assert "docs" in ds.tags
