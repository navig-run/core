"""Tests for navig.tools.router autodoc methods (to_markdown_summary, to_openapi_schema, ToolMeta.to_openapi_schema)."""
from __future__ import annotations

import json
import pytest

from navig.tools.router import (
    SafetyLevel,
    ToolDomain,
    ToolMeta,
    ToolRegistry,
    ToolStatus,
)


def _make_meta(name="test_tool", domain=ToolDomain.WEB, description="A test tool", output_schema=None):
    return ToolMeta(
        name=name,
        domain=domain,
        description=description,
        safety=SafetyLevel.SAFE,
        status=ToolStatus.AVAILABLE,
        parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema=output_schema,
    )


class TestToolMetaToDict:
    def test_basic_fields(self):
        meta = _make_meta()
        d = meta.to_dict()
        assert d["name"] == "test_tool"
        assert d["domain"] == "web"
        assert d["safety"] == "safe"
        assert "output_schema" not in d  # omitted when None

    def test_output_schema_included_when_set(self):
        meta = _make_meta(output_schema={"type": "string"})
        d = meta.to_dict()
        assert d["output_schema"] == {"type": "string"}


class TestToolMetaToOpenapiSchema:
    def test_basic_structure(self):
        meta = _make_meta()
        schema = meta.to_openapi_schema()
        assert schema["operationId"] == "test_tool"
        assert schema["summary"] == "A test tool"
        assert "requestBody" in schema

    def test_output_schema_in_responses(self):
        meta = _make_meta(output_schema={"type": "object"})
        schema = meta.to_openapi_schema()
        assert "responses" in schema
        assert "200" in schema["responses"]

    def test_no_responses_when_no_output_schema(self):
        meta = _make_meta()
        schema = meta.to_openapi_schema()
        assert "responses" not in schema


class TestToolRegistryMarkdownSummary:
    def _make_registry(self):
        reg = ToolRegistry()
        reg.register(_make_meta("tool_a", domain=ToolDomain.WEB, description="Web A"))
        reg.register(_make_meta("tool_b", domain=ToolDomain.CODE, description="Code B"))
        reg._initialized = True
        return reg

    def test_returns_markdown_table(self):
        reg = self._make_registry()
        md = reg.to_markdown_summary()
        assert "| Tool |" in md
        assert "tool_a" in md
        assert "tool_b" in md

    def test_empty_registry(self):
        reg = ToolRegistry()
        reg._initialized = True
        md = reg.to_markdown_summary()
        assert "No tools" in md

    def test_domain_filter(self):
        reg = self._make_registry()
        md = reg.to_markdown_summary(domain=ToolDomain.WEB)
        assert "tool_a" in md
        assert "tool_b" not in md


class TestToolRegistryOpenapiSchema:
    def _make_registry(self):
        reg = ToolRegistry()
        reg.register(_make_meta("fetch", domain=ToolDomain.WEB))
        reg._initialized = True
        return reg

    def test_openapi_schema_structure(self):
        reg = self._make_registry()
        schema = reg.to_openapi_schema()
        assert schema["openapi"] == "3.0.0"
        assert "/tools/fetch" in schema["paths"]

    def test_valid_json(self):
        reg = self._make_registry()
        schema = reg.to_openapi_schema()
        # Should be JSON-serializable
        text = json.dumps(schema)
        reparsed = json.loads(text)
        assert reparsed["openapi"] == "3.0.0"
