"""
Template Runner — Zero-LLM site flows

Loads YAML action templates from navig/browser/templates/ and executes
deterministic step sequences against a BrowserController.

Usage:
    runner = TemplateRunner(driver)
    runner.load_all()
    tmpl = runner.find_template("https://example.org/")
    results = await runner.run_flow(tmpl, "login", {"email": "...", "password": "..."})
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("navig.browser.template_runner")

try:
    import yaml

    _YAML_OK = True
except ImportError:
    _YAML_OK = False
    logger.warning(
        "PyYAML not installed; template runner disabled. Run: pip install pyyaml"
    )


class TemplateRunner:
    """Executes site-specific action templates with zero LLM calls."""

    TEMPLATES_DIR = Path(__file__).parent / "templates"

    def __init__(self, driver: Any):
        self.driver = driver
        self._templates: list[dict] = []  # ordered list of loaded templates

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all *.yaml templates from the templates directory.
        Returns the number of templates loaded."""
        if not _YAML_OK:
            return 0
        count = 0
        for f in sorted(self.TEMPLATES_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "match" not in data:
                    logger.warning("[Templates] Skipping invalid template: %s", f.name)
                    continue
                priority = data.get("priority", 10)
                self._templates.append({**data, "_priority": priority, "_file": f.name})
                count += 1
                logger.debug("[Templates] Loaded %s (match=%s)", f.name, data["match"])
            except Exception as exc:
                logger.warning("[Templates] Failed to load %s: %s", f.name, exc)
        # Pre-sort by priority descending so find_template can break early
        self._templates.sort(key=lambda t: t.get("_priority", 10), reverse=True)
        logger.info("[Templates] Loaded %d templates", count)
        return count

    def find_template(self, url: str) -> Optional[dict]:
        """Find the best matching template for a URL.
        Returns None if no site-specific template matches."""
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()

        for tmpl in self._templates:  # already sorted by priority desc
            match_key = tmpl.get("match", "")
            if match_key == "*":
                continue  # Skip wildcard in auto-detection; prefer explicit matches
            if match_key and (match_key in host or host.endswith(match_key)):
                return tmpl

        return None  # No site-specific template

    def get_template_by_name(self, name: str) -> Optional[dict]:
        """Retrieve a template by site name or file name."""
        for tmpl in self._templates:
            if (
                tmpl.get("site") == name
                or tmpl.get("_file", "").replace(".yaml", "") == name
            ):
                return tmpl
        return None

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run_flow(
        self,
        template: dict,
        flow_name: str,
        variables: dict[str, str],
    ) -> list[dict]:
        """Execute a named flow from a template.

        Substitutes {{variable}} placeholders in input values.
        On step failure, tries fallback selectors before giving up.
        Returns list of step result dicts.
        """
        flows = template.get("flows", {})
        if flow_name not in flows:
            raise ValueError(
                f"Flow '{flow_name}' not found in template '{template.get('site')}'. "
                f"Available: {list(flows.keys())}"
            )

        flow = flows[flow_name]
        steps = flow.get("steps", [])
        results = []

        for i, step in enumerate(steps):
            # Handle composite flows (compose: other_flow)
            if "compose" in step:
                logger.info("[Templates] Composing sub-flow: %s", step["compose"])
                sub_vars = {k: variables.get(k, "") for k in step.get("variables", [])}
                sub_results = await self.run_flow(template, step["compose"], sub_vars)
                results.extend(sub_results)
                continue

            result = await self._execute_step(step, variables)
            results.append(result)
            logger.info(
                "[Templates] Step %d/%d: %s → %s",
                i + 1,
                len(steps),
                step.get("action"),
                "✓" if result["ok"] else f"✗ {result.get('error')}",
            )

            if not result["ok"]:
                logger.warning("[Templates] Step failed. Caller may want AI fallback.")
                # Don't abort — let caller decide if it wants an AI loop for this step

            # Wait for page to settle
            wait = step.get("wait_after", "stable")
            if wait == "navigate":
                await self.driver.wait_for_stable(timeout_ms=5000)
            elif wait != "none":
                await self.driver.wait_for_stable(timeout_ms=2000)

        return results

    async def _execute_step(self, step: dict, variables: dict) -> dict:
        """Execute a single template step with fallback selector chain."""
        action = step.get("action", "")
        raw_input = step.get("input", "")
        input_val = self._substitute(str(raw_input), variables)

        primary = step.get("selector", {})
        fallbacks = step.get("fallbacks", [])
        candidates = [primary] + fallbacks

        last_err = None
        for candidate in candidates:
            kind = candidate.get("kind", "css")
            val = candidate.get("value", "")
            ok, err = await self._dispatch(action, kind, val, input_val)
            if ok:
                return {
                    "ok": True,
                    "action": action,
                    "selector": candidate,
                    "input": input_val,
                }
            last_err = err
            logger.debug("[Templates] ✗ %s:%s → %s", kind, val, err)

        return {"ok": False, "action": action, "error": last_err}

    async def _dispatch(
        self, action: str, kind: str, val: str, input_val: str
    ) -> tuple:
        """Send a single action to the driver. Returns (ok, error_str)."""
        try:
            page = self.driver._page
            timeout = 5000

            # ── navigate (go directly to URL) ─────────────────────────────
            if action == "navigate":
                target = input_val or val
                await self.driver.navigate(str(target))
                return True, None

            # ── role ──────────────────────────────────────────────────────
            if kind == "role":
                m = re.match(r"(\w+)\[name\*?=['\"]?([^'\"]+)['\"]?\]", val)
                if m:
                    locator = page.get_by_role(
                        m.group(1), name=re.compile(m.group(2), re.I)
                    )
                else:
                    locator = page.get_by_role(val)

                if action == "click":
                    await locator.first.click(timeout=timeout)
                elif action in ("fill", "fill_fast"):
                    await locator.first.fill(input_val, timeout=timeout)
                elif action == "press":
                    await locator.first.press(input_val or "Enter", timeout=timeout)
                return True, None

            # ── css (default / fallback) ──────────────────────────────────
            if action == "click":
                res = await self.driver.safe_click(val, timeout=timeout)
                return res["ok"], res.get("detail") or res.get("error")

            if action in ("fill", "fill_fast"):
                res = await self.driver.fill_fast(val, input_val, timeout=timeout)
                return res["ok"], res.get("error")

            if action == "press":
                await self.driver.press(val, input_val or "Enter", timeout=timeout)
                return True, None

            return False, f"Unknown action: {action}"

        except Exception as exc:
            return False, str(exc)[:200]

    @staticmethod
    def _substitute(text: str, variables: dict) -> str:
        """Replace {{key}} placeholders with variable values."""
        for k, v in variables.items():
            text = text.replace(f"{{{{{k}}}}}", v)
        return text
