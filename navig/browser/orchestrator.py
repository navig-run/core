"""
Cortex Orchestrator: The Hybrid Brain Loop (Phase 1 + 2)

Architecture:
  Each step triages into one of three modes:
    TEMPLATE  — zero LLM calls (site-specific YAML flows)          [handled by cortex.py]
    A11Y      — text-only LLM using accessibility tree + ref IDs   ← default
    VISION    — multimodal LLM using screenshot                    ← fallback when a11y sparse

Performance targets:
  - A11y step:    ~1s (50ms capture + 0.5s LLM text call)
  - Vision step:  ~4s (500ms capture + 3s multimodal LLM call)
  - No fixed sleeps — wait_for_stable() instead.
"""

import asyncio
import json
import logging
import re
from typing import Any

from navig.browser.prompts import CORTEX_A11Y_PROMPT, CORTEX_VISION_PROMPT
from navig.llm_generate import run_llm

logger = logging.getLogger("navig.browser.orchestrator")

# Minimum a11y nodes required to use text-only (a11y) mode.
# Below this threshold we fall back to vision.
A11Y_MIN_NODES = 5


class CortexOrchestrator:
    """
    The Hybrid Brain Loop.
    Drives a browser engine using the cheapest/fastest possible mode per step.
    """

    def __init__(self, goal: str, driver: Any = None):
        """
        Args:
            goal:   The high-level intent (e.g. "Post a message on example-app")
            driver: BrowserController (or any compatible async driver).
        """
        self.goal = goal
        self.driver = driver
        self.history: list[dict[str, Any]] = []
        # ref_map is rebuilt each step; stored here so cortex.py can use click_by_ref
        self._ref_map: dict[int, dict] = {}

    async def decide_next_action(
        self,
        url: str,
        last_step_result: dict[str, Any] | None = None,
        force_vision: bool = False,
    ) -> dict[str, Any]:
        """
        Capture current page state, route to the correct LLM mode,
        and return the next action as a parsed dict.

        Returns a dict with AT LEAST:
          {"action": str, "selector": {...}, ...}

        On unrecoverable failure returns:
          {"action": "error", "error": str}
        """
        # ── 1. Capture page state in parallel ────────────────────────────────
        a11y_text, ref_map, elements = await self._capture_state()
        self._ref_map = ref_map

        # Count annotated node lines added by get_a11y_snapshot_with_refs
        a11y_node_count = sum(1 for ln in a11y_text.splitlines() if ln.lstrip().startswith("- ["))
        use_vision = force_vision or (a11y_node_count < A11Y_MIN_NODES)
        mode = "vision" if use_vision else "a11y"

        logger.info("[Cortex] mode=%s a11y_nodes=%d url=%s", mode, a11y_node_count, url)

        # Capture screenshot only if vision mode
        screenshot_b64: str | None = None
        if use_vision:
            try:
                screenshot_b64 = await self.driver.screenshot_base64(quality=50)
            except Exception as exc:
                logger.warning("[Cortex] screenshot failed: %s", exc)

        # ── 2. Build context for LLM ──────────────────────────────────────────
        state_ctx: dict[str, Any] = {
            "goal": self.goal,
            "url": url,
            "mode": mode,
        }
        if last_step_result:
            state_ctx["last_step_result"] = last_step_result
        if elements:
            state_ctx["interactive_elements"] = elements[:40]

        # ── 3. Assemble messages ──────────────────────────────────────────────
        if use_vision:
            messages = self._build_vision_messages(state_ctx, a11y_text, screenshot_b64)
            system_prompt = CORTEX_VISION_PROMPT
        else:
            messages = self._build_a11y_messages(state_ctx, a11y_text)
            system_prompt = CORTEX_A11Y_PROMPT

        # ── 4. Call LLM ───────────────────────────────────────────────────────
        logger.info("[Cortex] Requesting action for goal: '%s'", self.goal)

        # Retry up to 2 times on rate-limit (429) errors
        last_exc = None
        for attempt in range(3):
            try:
                llm_result = run_llm(
                    messages=messages,
                    temperature=0.1,
                    max_tokens=512,
                    model_override="google:gemini-2.5-flash",
                    fallback_models=[],
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                err_str = str(exc)
                # Parse retry delay from Gemini 429 message if available
                retry_delay = 35
                m = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str)
                if m:
                    retry_delay = min(int(float(m.group(1))) + 2, 120)  # cap at 2 min

                if "429" in err_str and attempt < 2:
                    logger.warning(
                        "[Cortex] Rate limited (attempt %d/3). Waiting %ds...",
                        attempt + 1,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    break

        if last_exc:
            logger.error("[Cortex] LLM call raised: %s", last_exc)
            return {"action": "error", "error": f"LLM exception: {last_exc}"}

        content = (llm_result.content or "").strip()

        if not content:
            reason = getattr(llm_result, "finish_reason", "") or ""
            logger.error("[Cortex] Empty LLM response. finish_reason=%s", reason)
            return {"action": "error", "error": f"Empty LLM response: {reason}"}

        # ── 5. Parse JSON ─────────────────────────────────────────────────────
        parsed = self._extract_json(content)
        if not parsed:
            logger.error("[Cortex] Raw response (parse failed):\n%s", content)
            return {
                "action": "error",
                "error": "Failed to parse JSON from LLM",
                "raw_response": content[:500],
            }

        logger.info(
            "[Cortex] action=%s selector=%s",
            parsed.get("action"),
            (parsed.get("selector") or {}).get("value"),
        )
        self.history.append({"mode": mode, "url": url, "action": parsed})
        return parsed

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _capture_state(self) -> tuple[str, dict, list]:
        """Capture a11y snapshot + interactive elements in parallel."""
        a11y_task = asyncio.create_task(self.driver.get_a11y_snapshot_with_refs())
        elems_task = asyncio.create_task(self.driver.get_interactive_elements_fast())
        results = await asyncio.gather(a11y_task, elems_task, return_exceptions=True)
        a11y_result = results[0]
        elems_result = results[1]
        if isinstance(a11y_result, Exception):
            logger.warning("[Cortex] a11y capture failed: %s", a11y_result)
            a11y_result = ("", {})
        if isinstance(elems_result, Exception):
            logger.warning("[Cortex] elements capture failed: %s", elems_result)
            elems_result = []
        (a11y_text, ref_map), elements = a11y_result, elems_result
        return a11y_text, ref_map, elements

    def _build_a11y_messages(self, ctx: dict, a11y_text: str) -> list:
        user_text = (
            f"Current State:\n{json.dumps(ctx, indent=2)}\n\n"
            f"Accessibility Tree (use ref IDs in your selector):\n{a11y_text}\n\n"
            f"Produce the next JSON action."
        )
        return [
            {"role": "system", "content": CORTEX_A11Y_PROMPT},
            {"role": "user", "content": user_text},
        ]

    def _build_vision_messages(self, ctx: dict, a11y_text: str, screenshot_b64: str | None) -> list:
        text_part = {
            "type": "text",
            "text": (
                f"Current State:\n{json.dumps(ctx, indent=2)}\n\n"
                f"Partial A11y Tree (may be sparse):\n{a11y_text or '(empty)'}\n\n"
                f"Produce the next JSON action."
            ),
        }
        content = [text_part]
        if screenshot_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                }
            )
        return [
            {"role": "system", "content": CORTEX_VISION_PROMPT},
            {"role": "user", "content": content},
        ]

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        """Robustly extract JSON from an LLM response."""
        text = text.strip()

        def _parse(t: str) -> dict | None:
            try:
                obj = json.loads(t)
                if isinstance(obj, list) and obj:
                    return obj[0] if isinstance(obj[0], dict) else None
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass  # malformed JSON; skip line
            return None

        # 1. Markdown code blocks
        if "```json" in text:
            for part in text.split("```json")[1:]:
                res = _parse(part.split("```")[0].strip())
                if res:
                    return res

        if "```" in text:
            parts = text.split("```")
            for i, part in enumerate(parts):
                if i % 2 != 0:
                    res = _parse(part.strip())
                    if res:
                        return res

        # 2. First { ... last }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            res = _parse(text[start : end + 1])
            if res:
                return res

        # 3. First [ ... last ]
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            res = _parse(text[start : end + 1])
            if res:
                return res

        # 4. Whole string
        return _parse(text)
