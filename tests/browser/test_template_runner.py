"""
Tests for navig.browser.template_runner — pure helper methods and logic.
"""

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from navig.browser.template_runner import TemplateRunner


# ---------------------------------------------------------------------------
# _substitute (static method)
# ---------------------------------------------------------------------------


def test_substitute_replaces_placeholder():
    result = TemplateRunner._substitute("hello {{name}}", {"name": "world"})
    assert result == "hello world"


def test_substitute_no_placeholders():
    result = TemplateRunner._substitute("no placeholders here", {})
    assert result == "no placeholders here"


def test_substitute_multiple_placeholders():
    result = TemplateRunner._substitute("{{a}} and {{b}}", {"a": "foo", "b": "bar"})
    assert result == "foo and bar"


def test_substitute_missing_key_leaves_placeholder():
    result = TemplateRunner._substitute("hello {{missing}}", {})
    assert "{{missing}}" in result


def test_substitute_empty_text():
    result = TemplateRunner._substitute("", {"key": "value"})
    assert result == ""


def test_substitute_numeric_value():
    result = TemplateRunner._substitute("count: {{n}}", {"n": "42"})
    assert "42" in result


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_stores_driver():
    driver = MagicMock()
    runner = TemplateRunner(driver)
    assert runner._driver is driver
    assert runner._templates == []


def test_init_empty_templates():
    runner = TemplateRunner(None)
    assert isinstance(runner._templates, list)
    assert len(runner._templates) == 0


# ---------------------------------------------------------------------------
# find_template
# ---------------------------------------------------------------------------


def _runner_with_templates(*templates) -> TemplateRunner:
    runner = TemplateRunner(None)
    for t in templates:
        runner._templates[t.get("site", t.get("_file", "unknown"))] = t
    return runner


def test_find_template_matches_hostname():
    runner = TemplateRunner(None)
    runner._templates = [
        {"site": "example.com", "match": "example.com", "flows": {}}
    ]
    result = runner.find_template("https://www.example.com/page")
    assert result is not None
    assert result["site"] == "example.com"


def test_find_template_skips_wildcard():
    runner = TemplateRunner(None)
    runner._templates = [
        {"site": "wildcard", "match": "*", "flows": {}},
        {"site": "specific.io", "match": "specific.io", "flows": {}},
    ]
    result = runner.find_template("https://specific.io/page")
    assert result["site"] == "specific.io"


def test_find_template_returns_none_on_no_match():
    runner = TemplateRunner(None)
    runner._templates = [
        {"site": "other.com", "match": "other.com", "flows": {}}
    ]
    result = runner.find_template("https://unrelated.io/page")
    assert result is None


def test_find_template_returns_none_empty():
    runner = TemplateRunner(None)
    result = runner.find_template("https://anything.com")
    assert result is None


# ---------------------------------------------------------------------------
# get_template_by_name
# ---------------------------------------------------------------------------


def test_get_template_by_name_matches_site():
    runner = TemplateRunner(None)
    runner._templates = [{"site": "MyTool", "match": "mytool.com", "flows": {}}]
    result = runner.get_template_by_name("MyTool")
    assert result["site"] == "MyTool"


def test_get_template_by_name_matches_file_stem():
    runner = TemplateRunner(None)
    runner._templates = [
        {"site": "other", "_file": "my_template.yaml", "match": "x.com", "flows": {}}
    ]
    result = runner.get_template_by_name("my_template")
    assert result["_file"] == "my_template.yaml"


def test_get_template_by_name_returns_none_on_miss():
    runner = TemplateRunner(None)
    runner._templates = [{"site": "Alpha", "_file": "alpha.yaml", "flows": {}}]
    result = runner.get_template_by_name("beta")
    assert result is None


def test_get_template_by_name_empty_templates():
    runner = TemplateRunner(None)
    result = runner.get_template_by_name("something")
    assert result is None


# ---------------------------------------------------------------------------
# load_all — skips when _YAML_OK is False
# ---------------------------------------------------------------------------


def test_load_all_returns_zero_when_yaml_not_ok():
    runner = TemplateRunner(None)
    with patch("navig.browser.template_runner._YAML_OK", False):
        count = runner.load_all()
    assert count == 0
    assert runner._templates == []


def test_load_all_loads_yaml_files(tmp_path):
    """load_all reads YAML files from TEMPLATES_DIR and populates _templates."""
    yaml_content = textwrap.dedent(
        """
        site: testsite.com
        match: testsite.com
        flows:
          login:
            steps:
              - action: navigate
                url: https://testsite.com/login
        """
    )
    tpl_file = tmp_path / "testsite.yaml"
    tpl_file.write_text(yaml_content)

    runner = TemplateRunner(None)
    with (
        patch("navig.browser.template_runner._YAML_OK", True),
        patch.object(TemplateRunner, "TEMPLATES_DIR", tmp_path),
    ):
        count = runner.load_all()

    assert count == 1
    assert any(t.get("site") == "testsite.com" for t in runner._templates)


def test_load_all_skips_non_yaml_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not yaml")
    (tmp_path / "readme.md").write_text("# header")
    runner = TemplateRunner(None)
    with (
        patch("navig.browser.template_runner._YAML_OK", True),
        patch.object(TemplateRunner, "TEMPLATES_DIR", tmp_path),
    ):
        count = runner.load_all()
    assert count == 0


# ---------------------------------------------------------------------------
# run_flow — raises ValueError for unknown flow
# ---------------------------------------------------------------------------


def test_run_flow_raises_for_missing_flow():
    runner = TemplateRunner(None)
    template = {"site": "s.com", "match": "s.com", "flows": {}}
    import asyncio

    with pytest.raises(ValueError, match="Flow"):
        asyncio.run(runner.run_flow(template, "unknown_flow", {}))


def test_run_flow_executes_steps():
    """run_flow with empty steps list returns empty results list."""
    runner = TemplateRunner(None)
    template = {"site": "s.com", "flows": {"login": {"steps": []}}}
    import asyncio

    results = asyncio.run(runner.run_flow(template, "login", {}))
    assert results == []
