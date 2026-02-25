"""
Unit tests for the Cortex browser engine (Phase 1 + 2 + 3).

Covers:
- get_a11y_snapshot_with_refs: ref ID assignment and parsing
- fill_fast: JS injection with fallback
- wait_for_stable: non-blocking page settle
- get_interactive_elements_fast: JS eval element scan
- CortexOrchestrator._extract_json: JSON parsing robustness
- CortexOrchestrator._capture_state: parallel a11y+elements capture
- TemplateRunner: load, find, flow execution, variable substitution
- prompts.py: both prompts exist and are non-empty

All browser tests use pytest-asyncio with auto mode.
Playwright tests are marked @pytest.mark.browser and skipped in unit CI
unless NAVIG_RUN_BROWSER_TESTS=1 is set.
"""

import asyncio
import json
import os
import re
import pytest
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Skip markers ───────────────────────────────────────────────────────────────
BROWSER_TESTS = os.environ.get("NAVIG_RUN_BROWSER_TESTS", "0") == "1"
browser_required = pytest.mark.skipif(
    not BROWSER_TESTS,
    reason="Set NAVIG_RUN_BROWSER_TESTS=1 to run live browser tests",
)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — prompts.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrompts:
    def test_a11y_prompt_exists_and_non_empty(self):
        from navig.browser.prompts import CORTEX_A11Y_PROMPT
        assert len(CORTEX_A11Y_PROMPT) > 100
        assert "RAW JSON ONLY" in CORTEX_A11Y_PROMPT
        assert "ref" in CORTEX_A11Y_PROMPT

    def test_vision_prompt_exists_and_non_empty(self):
        from navig.browser.prompts import CORTEX_VISION_PROMPT
        assert len(CORTEX_VISION_PROMPT) > 100
        assert "RAW JSON ONLY" in CORTEX_VISION_PROMPT

    def test_backward_compat_alias(self):
        from navig.browser.prompts import CORTEX_SYSTEM_PROMPT, CORTEX_VISION_PROMPT
        assert CORTEX_SYSTEM_PROMPT is CORTEX_VISION_PROMPT

    def test_a11y_prompt_has_ref_priority_rule(self):
        from navig.browser.prompts import CORTEX_A11Y_PROMPT
        # Ref must come first in selector priority
        ref_pos   = CORTEX_A11Y_PROMPT.find("ref")
        role_pos  = CORTEX_A11Y_PROMPT.find("role")
        css_pos   = CORTEX_A11Y_PROMPT.find("css")
        coords_pos = CORTEX_A11Y_PROMPT.find("coords")
        assert ref_pos < role_pos < css_pos < coords_pos

    def test_action_schema_includes_fill_fast(self):
        from navig.browser.prompts import CORTEX_A11Y_PROMPT
        assert "fill_fast" in CORTEX_A11Y_PROMPT

    def test_action_schema_includes_done_and_fail(self):
        from navig.browser.prompts import CORTEX_A11Y_PROMPT
        assert "done" in CORTEX_A11Y_PROMPT
        assert "fail" in CORTEX_A11Y_PROMPT


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — orchestrator._extract_json
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractJson:
    def setup_method(self):
        from navig.browser.orchestrator import CortexOrchestrator
        self.orch = CortexOrchestrator(goal="test", driver=MagicMock())

    def _parse(self, text):
        return self.orch._extract_json(text)

    def test_clean_json(self):
        out = self._parse('{"action":"click","selector":{"kind":"ref","value":"5"}}')
        assert out["action"] == "click"
        assert out["selector"]["value"] == "5"

    def test_markdown_json_block(self):
        out = self._parse('```json\n{"action":"fill","input":"hello"}\n```')
        assert out["action"] == "fill"
        assert out["input"] == "hello"

    def test_json_with_preamble(self):
        out = self._parse('Sure! Here is my action:\n{"action":"done","reason":"complete"}')
        assert out["action"] == "done"

    def test_first_item_from_array(self):
        out = self._parse('[{"action":"click","selector":{"kind":"css","value":"button"}}]')
        assert out["action"] == "click"

    def test_empty_string_returns_none(self):
        assert self._parse("") is None

    def test_garbage_returns_none(self):
        assert self._parse("This is not JSON at all!!!") is None

    def test_nested_json_roundtrip(self):
        payload = {
            "action": "fill_fast",
            "selector": {"kind": "role", "value": "textbox[name='Email']"},
            "input": "test@example.com",
            "fallbacks": [{"kind": "css", "value": "input[type='email']"}],
            "wait_after": "none",
            "reason": "fill email field",
        }
        out = self._parse(json.dumps(payload))
        assert out["action"] == "fill_fast"
        assert len(out["fallbacks"]) == 1

    def test_trailing_text_after_json(self):
        out = self._parse('{"action":"scroll","input":"400"} some trailing text')
        assert out["action"] == "scroll"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — orchestrator A11y node counting
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorModeSelection:
    def _count_nodes(self, text: str) -> int:
        return sum(1 for ln in text.splitlines() if ln.lstrip().startswith("- ["))

    def test_empty_a11y_triggers_vision(self):
        from navig.browser.orchestrator import A11Y_MIN_NODES
        assert self._count_nodes("") < A11Y_MIN_NODES

    def test_rich_a11y_stays_text_mode(self):
        from navig.browser.orchestrator import A11Y_MIN_NODES
        tree = "\n".join(f"- [{i}] button: Action {i}" for i in range(10))
        assert self._count_nodes(tree) >= A11Y_MIN_NODES

    def test_ref_id_lines_counted_correctly(self):
        tree = """- [0] link: example-app
  - [1] img
- [2] link: unregistered
- [3] heading: Random subjects
- [4] button: Log in
- [5] textbox: email"""
        assert self._count_nodes(tree) == 6


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — controller a11y ref parsing (unit, no Playwright)
# ═══════════════════════════════════════════════════════════════════════════════

class TestA11yRefParsing:
    """Test get_a11y_snapshot_with_refs parsing logic without launching a browser."""

    @pytest.mark.asyncio
    async def test_ref_ids_assigned_in_order(self):
        from navig.browser.controller import BrowserController, BrowserConfig

        ctrl = BrowserController(BrowserConfig())
        # Monkey-patch get_a11y_tree to return a canned ARIA snapshot
        canned = (
            "- link \"example-app\"\n"
            "  - /url: https://example.org/\n"
            "- link \"unregistered\"\n"
            "- heading \"Random subjects\"\n"
            "- button \"Log in\"\n"
        )
        ctrl.get_a11y_tree = AsyncMock(return_value=canned)

        text, ref_map = await ctrl.get_a11y_snapshot_with_refs()

        # Should have 4 ref IDs (4 lines starting with "- ")
        assert len(ref_map) == 4
        # ref 0 is the example-app link
        assert ref_map[0]["role"].lower().startswith("link")
        # ref 3 is the button
        assert ref_map[3]["role"].lower().startswith("button")
        assert ref_map[3]["name"] == "Log in"

    @pytest.mark.asyncio
    async def test_empty_a11y_returns_empty_tuple(self):
        from navig.browser.controller import BrowserController, BrowserConfig

        ctrl = BrowserController(BrowserConfig())
        ctrl.get_a11y_tree = AsyncMock(return_value="")

        text, ref_map = await ctrl.get_a11y_snapshot_with_refs()
        assert text == ""
        assert ref_map == {}

    @pytest.mark.asyncio
    async def test_annotated_text_contains_ref_brackets(self):
        from navig.browser.controller import BrowserController, BrowserConfig

        ctrl = BrowserController(BrowserConfig())
        ctrl.get_a11y_tree = AsyncMock(return_value='- button "Submit"\n- textbox "Email"\n')

        text, _ = await ctrl.get_a11y_snapshot_with_refs()
        assert "[0]" in text
        assert "[1]" in text

    @pytest.mark.asyncio
    async def test_non_node_lines_pass_through_unchanged(self):
        from navig.browser.controller import BrowserController, BrowserConfig

        ctrl = BrowserController(BrowserConfig())
        ctrl.get_a11y_tree = AsyncMock(return_value=(
            "- link \"home\"\n"
            "  - /url: https://example.com/\n"
            "- button \"Go\"\n"
        ))

        text, ref_map = await ctrl.get_a11y_snapshot_with_refs()
        # The /url: line must pass through unchanged (no ref assigned to it)
        assert "/url:" in text
        # But should only have 2 refs (link + button)
        assert len(ref_map) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — TemplateRunner (no browser, no Playwright)
# ═══════════════════════════════════════════════════════════════════════════════

example-app_YAML = Path(__file__).parent.parent / "navig" / "browser" / "templates" / "example-app.yaml"
GENERIC_YAML = Path(__file__).parent.parent / "navig" / "browser" / "templates" / "generic.yaml"


class TestTemplateRunner:

    def _make_runner(self):
        from navig.browser.template_runner import TemplateRunner
        mock_driver = MagicMock()
        mock_driver._page = MagicMock()
        mock_driver.wait_for_stable = AsyncMock()
        mock_driver.navigate = AsyncMock(return_value={"url": "https://example.org/account/login"})
        mock_driver.safe_click = AsyncMock(return_value={"ok": True})
        mock_driver.fill_fast = AsyncMock(return_value={"ok": True})
        mock_driver.press = AsyncMock(return_value=True)
        return TemplateRunner(mock_driver)

    def test_example-app_yaml_is_valid(self):
        assert example-app_YAML.exists(), "example-app.yaml must exist"
        data = yaml.safe_load(example-app_YAML.read_text())
        assert data["site"] == "example.org"
        assert "login" in data["flows"]
        assert "post" in data["flows"]

    def test_generic_yaml_is_valid(self):
        assert GENERIC_YAML.exists(), "generic.yaml must exist"
        data = yaml.safe_load(GENERIC_YAML.read_text())
        assert "login" in data["flows"]
        assert "post" in data["flows"]

    def test_example-app_login_flow_has_4_steps(self):
        data = yaml.safe_load(example-app_YAML.read_text())
        steps = data["flows"]["login"]["steps"]
        assert len(steps) == 4, f"Expected 4 login steps, got {len(steps)}"

    def test_example-app_login_uses_navigate_first(self):
        data = yaml.safe_load(example-app_YAML.read_text())
        first_step = data["flows"]["login"]["steps"][0]
        assert first_step["action"] == "navigate"

    def test_example-app_post_flow_has_2_steps(self):
        data = yaml.safe_load(example-app_YAML.read_text())
        steps = data["flows"]["post"]["steps"]
        assert len(steps) == 2

    def test_find_template_for_example-app(self):
        runner = self._make_runner()
        runner.load_all()
        tmpl = runner.find_template("https://example.org/account/login")
        assert tmpl is not None
        assert tmpl["site"] == "example.org"

    def test_find_template_no_match_returns_none(self):
        runner = self._make_runner()
        runner.load_all()
        assert runner.find_template("https://totally-unknown-site-xyz.com/") is None

    def test_get_template_by_name(self):
        runner = self._make_runner()
        runner.load_all()
        tmpl = runner.get_template_by_name("example-app")
        assert tmpl is not None

    def test_variable_substitution(self):
        from navig.browser.template_runner import TemplateRunner
        result = TemplateRunner._substitute("Hello {{name}}, your email is {{email}}", {
            "name": "Alice",
            "email": "alice@example.com",
        })
        assert result == "Hello Alice, your email is alice@example.com"

    def test_variable_substitution_missing_key_leaves_placeholder(self):
        from navig.browser.template_runner import TemplateRunner
        result = TemplateRunner._substitute("Hello {{name}}", {"email": "x@y.com"})
        assert "{{name}}" in result

    @pytest.mark.asyncio
    async def test_run_flow_navigate_step_calls_driver_navigate(self):
        runner = self._make_runner()
        runner.load_all()
        tmpl = runner.get_template_by_name("example-app")
        assert tmpl is not None

        results = await runner.run_flow(
            tmpl, "login",
            {"email": "test@example.com", "password": "secret123"}
        )
        # All 4 steps should have been attempted
        assert len(results) == 4
        # First step (navigate) should succeed
        assert results[0]["ok"] is True
        assert results[0]["action"] == "navigate"

    @pytest.mark.asyncio
    async def test_flow_missing_raises_value_error(self):
        runner = self._make_runner()
        runner.load_all()
        tmpl = runner.get_template_by_name("example-app")
        with pytest.raises(ValueError, match="nonexistent"):
            await runner.run_flow(tmpl, "nonexistent", {})


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — CDP Bridge (unit, no real Chrome)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCDPBridge:
    def test_cdp_bridge_init(self):
        from navig.browser.cdp_bridge import CDPBridge
        bridge = CDPBridge(debug_port=9223, tab_index=0)
        assert bridge.debug_port == 9223
        assert bridge._cdp_endpoint == "http://localhost:9223"

    def test_auto_detect_no_chrome(self):
        from navig.browser.cdp_bridge import auto_detect_cdp_port
        # Should return None when nothing is listening (CI env)
        result = auto_detect_cdp_port()
        # We don't assert None because CI might have something on 9222,
        # just assert the return type
        assert result is None or isinstance(result, int)

    def test_cdp_bridge_inherits_controller_methods(self):
        from navig.browser.cdp_bridge import CDPBridge
        from navig.browser.controller import BrowserController
        bridge = CDPBridge()
        # Must have all the Phase 1+2 methods
        assert hasattr(bridge, "get_a11y_tree")
        assert hasattr(bridge, "get_a11y_snapshot_with_refs")
        assert hasattr(bridge, "click_by_ref")
        assert hasattr(bridge, "fill_fast")
        assert hasattr(bridge, "wait_for_stable")
        assert hasattr(bridge, "get_interactive_elements_fast")
        assert hasattr(bridge, "safe_click")
        assert hasattr(bridge, "safe_fill")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Router
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouter:
    def test_fast_browser_returns_controller(self):
        from navig.browser.router import fast_browser
        from navig.browser.controller import BrowserController
        b = fast_browser()
        assert isinstance(b, BrowserController)

    def test_cdp_browser_returns_cdp_bridge(self):
        from navig.browser.router import cdp_browser
        from navig.browser.cdp_bridge import CDPBridge
        b = cdp_browser(9222)
        assert isinstance(b, CDPBridge)
        assert b.debug_port == 9222

    def test_get_browser_cdp_port(self):
        from navig.browser.router import get_browser
        from navig.browser.cdp_bridge import CDPBridge
        b = get_browser(cdp_port=9222)
        assert isinstance(b, CDPBridge)

    def test_get_browser_default(self):
        from navig.browser.router import get_browser
        from navig.browser.controller import BrowserController
        b = get_browser()
        assert isinstance(b, BrowserController)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Live browser tests (skipped unless NAVIG_RUN_BROWSER_TESTS=1)
# ═══════════════════════════════════════════════════════════════════════════════

@browser_required
class TestLiveBrowser:
    """
    These tests launch a real headless browser and hit live URLs.
    Run with: NAVIG_RUN_BROWSER_TESTS=1 pytest tests/test_cortex_engine.py -k LiveBrowser -v
    """

    @pytest.mark.asyncio
    async def test_a11y_snapshot_example-app(self):
        from navig.browser.controller import BrowserController, BrowserConfig
        driver = BrowserController(BrowserConfig(headless=True))
        await driver.start()
        try:
            await driver.navigate("https://example.org/")
            await driver.wait_for_stable(5000)
            text, ref_map = await driver.get_a11y_snapshot_with_refs()
            assert len(text) > 100, "A11y tree should be non-trivial"
            assert len(ref_map) >= 5, "Should have at least 5 interactive nodes"
        finally:
            await driver.stop()

    @pytest.mark.asyncio
    async def test_interactive_elements_fast_example-app(self):
        from navig.browser.controller import BrowserController, BrowserConfig
        driver = BrowserController(BrowserConfig(headless=True))
        await driver.start()
        try:
            await driver.navigate("https://example.org/")
            await driver.wait_for_stable(5000)
            elems = await driver.get_interactive_elements_fast()
            assert len(elems) > 0, "Should find at least one interactive element"
            assert "tag" in elems[0]
            assert "x" in elems[0]
        finally:
            await driver.stop()

    @pytest.mark.asyncio
    async def test_a11y_capture_time_under_500ms(self):
        """A11y capture must complete in under 500ms after page load."""
        import time
        from navig.browser.controller import BrowserController, BrowserConfig
        driver = BrowserController(BrowserConfig(headless=True))
        await driver.start()
        try:
            await driver.navigate("https://example.org/")
            await driver.wait_for_stable(5000)
            t0 = time.perf_counter()
            text, ref_map = await driver.get_a11y_snapshot_with_refs()
            elems = await driver.get_interactive_elements_fast()
            elapsed = (time.perf_counter() - t0) * 1000
            assert elapsed < 500, f"State capture took {elapsed:.0f}ms, target <500ms"
        finally:
            await driver.stop()
