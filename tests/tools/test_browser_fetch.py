"""
Tests for navig.tools.browser_fetch — pure helper functions.
"""

import pytest

from navig.tools.browser_fetch import BrowserFetchTool, _extract_text, _is_js_gated


# ---------------------------------------------------------------------------
# _is_js_gated
# ---------------------------------------------------------------------------


def test_is_js_gated_empty_content():
    # Less than 250 chars of text
    assert _is_js_gated("<html><body></body></html>") is True


def test_is_js_gated_minimal_text_false():
    # Enough plain text
    big_body = "<html><body>" + ("This is real content. " * 20) + "</body></html>"
    assert _is_js_gated(big_body) is False


def test_is_js_gated_noscript_js_required():
    html = (
        "<html><body>"
        + ("x " * 200)
        + "<noscript>Please enable JavaScript to use this site</noscript>"
        + "</body></html>"
    )
    assert _is_js_gated(html) is True


def test_is_js_gated_react_root_empty_div():
    html = (
        "<html><body>"
        + ("x " * 200)
        + '<div id="root"></div>'
        + "</body></html>"
    )
    assert _is_js_gated(html) is True


def test_is_js_gated_next_data_script():
    html = (
        "<html><body>"
        + ("x " * 200)
        + '<script id="__NEXT_DATA__" type="application/json">{}</script>'
        + "</body></html>"
    )
    assert _is_js_gated(html) is True


def test_is_js_gated_app_root_angular():
    html = (
        "<html><body>"
        + ("x " * 200)
        + "<app-root></app-root>"
        + "</body></html>"
    )
    assert _is_js_gated(html) is True


def test_is_js_gated_plain_large_html():
    html = "<html><body>" + "<p>Paragraph content here. </p>" * 30 + "</body></html>"
    assert _is_js_gated(html) is False


def test_is_js_gated_vite_bundle_script():
    html = (
        "<html><body>"
        + ("x " * 200)
        + '<script src="/assets/main.abc12345defg.js"></script>'
        + "</body></html>"
    )
    assert _is_js_gated(html) is True


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_strips_tags():
    html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
    text = _extract_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "<" not in text


def test_extract_text_skips_script_content():
    html = "<html><body><p>visible</p><script>secret_js_code()</script></body></html>"
    text = _extract_text(html)
    assert "visible" in text
    assert "secret_js_code" not in text


def test_extract_text_skips_style_content():
    html = "<html><body><p>text</p><style>body { color: red; }</style></body></html>"
    text = _extract_text(html)
    assert "text" in text
    assert "color" not in text


def test_extract_text_skips_noscript_content():
    html = "<html><body><p>content</p><noscript>Enable JS</noscript></body></html>"
    text = _extract_text(html)
    assert "content" in text
    assert "Enable JS" not in text


def test_extract_text_handles_empty():
    result = _extract_text("")
    assert isinstance(result, str)


def test_extract_text_plain_text_passthrough():
    html = "<p>Simple text here without complications.</p>"
    text = _extract_text(html)
    assert "Simple text here" in text


def test_extract_text_collapses_whitespace():
    html = "<p>A   lot   of   spaces</p>"
    text = _extract_text(html)
    # Should not have multiple consecutive spaces
    assert "  " not in text or "lot" in text


# ---------------------------------------------------------------------------
# BrowserFetchTool — basic non-network checks
# ---------------------------------------------------------------------------


def test_tool_name():
    assert BrowserFetchTool().name == "browser_fetch"


def test_tool_description():
    desc = BrowserFetchTool().description
    assert "URL" in desc or "url" in desc.lower()


def test_tool_run_requires_url():
    import asyncio

    tool = BrowserFetchTool()
    result = asyncio.run(tool.run({}))
    assert result.success is False
    assert "url" in result.error.lower()


def test_tool_run_rejects_no_url():
    import asyncio

    tool = BrowserFetchTool()
    result = asyncio.run(tool.run({"url": "   "}))
    assert result.success is False
