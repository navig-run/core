"""
navig cortex — Hybrid Brain Loop CLI
======================================

Usage
-----
  # Default (auto mode: a11y first, vision fallback)
  navig cortex run "Post a message on example-app"

  # Force vision mode
  navig cortex run "..." --url https://example.com --vision

  # A11y-only, never send screenshot
  navig cortex run "..." --no-vision

  # Limit steps
  navig cortex run "..." --steps 10

  # Headless
  navig cortex run "..." --headless
"""

import asyncio
import re

import typer

from navig import console_helper as ch
from navig.browser.orchestrator import CortexOrchestrator
from navig.browser.router import get_browser

cortex_app = typer.Typer(
    name="cortex",
    help="🧠 Execute an autonomous goal using the Hybrid Visual Brain Loop (Cortex).",
    invoke_without_command=False,
)


@cortex_app.command("run")
def run_cortex(
    goal: str = typer.Argument(
        ..., help="Goal for the agent (e.g. 'Post a message on example-app')"
    ),
    start_url: str = typer.Option("https://google.com", "--url", help="Starting URL"),
    max_steps: int = typer.Option(15, "--steps", help="Maximum loop iterations"),
    headless: bool = typer.Option(False, "--headless", help="Run browser headlessly"),
    force_vision: bool = typer.Option(
        False, "--vision", help="Force vision (screenshot) mode every step"
    ),
    no_vision: bool = typer.Option(
        False, "--no-vision", help="Never use vision; a11y only"
    ),
    template_name: str | None = typer.Option(
        None, "--template", help="Force a specific template (e.g. 'example-app')"
    ),
    no_template: bool = typer.Option(
        False, "--no-template", help="Skip template auto-detection; always use AI loop"
    ),
    email: str | None = typer.Option(
        None, "--email", help="Email/username for template login flow"
    ),
    password: str | None = typer.Option(
        None, "--password", help="Password for template login flow"
    ),
    post_text: str | None = typer.Option(
        None, "--post", help="Text to post for template post flow"
    ),
    cdp_port: int | None = typer.Option(
        None,
        "--cdp-port",
        help="Attach to existing Chrome at this CDP port instead of launching a new browser",
    ),
):
    """Start the Cortex Hybrid Brain Loop on a specific goal."""
    ch.header(f"🧠 NAVIG Cortex\nGoal: {goal}")

    async def _run():
        from navig.browser.controller import BrowserConfig
        from navig.browser.template_runner import TemplateRunner

        cfg = BrowserConfig(headless=headless, timeout_ms=30000)
        driver = get_browser(stealth=False, cdp_port=cdp_port, browser_config=cfg)
        await driver.start()

        # ── Template fast path (zero LLM calls) ───────────────────────────
        if not no_template:
            runner = TemplateRunner(driver)
            runner.load_all()

            tmpl = None
            if template_name:
                tmpl = runner.get_template_by_name(template_name)
                if not tmpl:
                    ch.warning(
                        f"Template '{template_name}' not found. Falling back to AI loop."
                    )
            else:
                tmpl = runner.find_template(start_url)

            if tmpl:
                ch.success(
                    f"📋 Template match: {tmpl.get('site')} ({tmpl.get('_file')})"
                )
                ch.info(f"Navigating to {start_url} ...")
                await driver.navigate(start_url)
                await driver.wait_for_stable(timeout_ms=3000)

                # Build variable map from CLI args + goal parsing
                variables: dict = {}
                if email:
                    variables["email"] = email
                if password:
                    variables["password"] = password
                if post_text:
                    variables["text"] = post_text

                flows = tmpl.get("flows", {})

                # Run login if credentials provided and flow exists
                if "login" in flows and email and password:
                    ch.info("Running template: login flow")
                    results = await runner.run_flow(tmpl, "login", variables)
                    for r in results:
                        icon = "✓" if r["ok"] else "✗"
                        ch.success(
                            f"  {icon} {r['action']} → {r.get('selector', {}).get('value', '')[:50]}"
                        )
                        if not r["ok"]:
                            ch.warning(f"    Error: {r.get('error')}")

                # Run post if text provided and flow exists
                if "post" in flows and post_text:
                    ch.info("Running template: post flow")
                    results = await runner.run_flow(tmpl, "post", variables)
                    for r in results:
                        icon = "✓" if r["ok"] else "✗"
                        ch.success(f"  {icon} {r['action']}")

                await driver.stop()
                ch.dim("Session closed.")
                return  # Done — no AI loop needed

        # ── AI Loop (fallback or explicit) ────────────────────────────────
        orchestrator = CortexOrchestrator(goal=goal, driver=driver)
        last_step_result: dict | None = None

        try:
            ch.info(f"Navigating to {start_url} ...")
            await driver.navigate(start_url)
            await driver.wait_for_stable(timeout_ms=3000)

            for step in range(max_steps):
                ch.dim(f"--- Step {step + 1}/{max_steps} ---")

                current_url = await driver.get_url()

                # ── Brain decides next action ─────────────────────────────
                ch.dim("🧠 Thinking...")
                try:
                    action_json = await orchestrator.decide_next_action(
                        url=current_url,
                        last_step_result=last_step_result,
                        force_vision=force_vision and not no_vision,
                    )
                except Exception as exc:
                    ch.error(f"Orchestrator error: {exc}")
                    break

                if not action_json:
                    ch.error("No action produced.")
                    break

                action_type = action_json.get("action", "")
                selector_obj = action_json.get("selector") or {}
                selector_kind = selector_obj.get("kind", "")
                selector_val = selector_obj.get("value", "")
                input_val = action_json.get("input", "")
                fallbacks = action_json.get("fallbacks", [])
                wait_after = action_json.get("wait_after", "stable")
                reason = action_json.get("reason", "")

                ch.success(f"⚡ {action_type} | {selector_kind}:{selector_val}")
                if reason:
                    ch.info(f"↳ {reason}")

                # ── Terminal actions ──────────────────────────────────────
                if action_type == "done":
                    ch.success("✅ Goal achieved.")
                    break
                if action_type in ("fail", "error"):
                    ch.error(f"❌ {action_json.get('error') or reason}")
                    break
                if action_type == "more_info":
                    ch.warning("Cortex requested more context. Halting.")
                    break

                # ── Execute action with fallback chain ───────────────────
                prev_url = current_url
                ok, err_msg = await _execute_action(
                    driver,
                    orchestrator,
                    action_type,
                    selector_obj,
                    selector_kind,
                    selector_val,
                    input_val,
                    fallbacks,
                )

                url_after = await driver.get_url()

                last_step_result = {
                    "ok": ok,
                    "action_taken": action_type,
                    "selector_used": selector_obj if ok else None,
                    "error": err_msg if not ok else None,
                    "url_changed": url_after != prev_url,
                    "notes": None,
                }

                if not ok:
                    ch.error(f"Action failed: {err_msg}")

                # ── Page stabilisation (no fixed sleep) ───────────────────
                if wait_after == "navigate":
                    await driver.wait_for_stable(timeout_ms=5000)
                elif wait_after != "none":
                    await driver.wait_for_stable(timeout_ms=2000)

        finally:
            await driver.stop()
            ch.dim("Session closed.")

    asyncio.run(_run())


# ── Action executor with fallback selector chain ──────────────────────────────


def _parse_coords(val: str):
    """Extract (x, y) from strings like '753,230' or '100 200' or bounding boxes."""
    nums = re.findall(r"-?\d+\.?\d*", str(val))
    if len(nums) == 4:
        return (
            (float(nums[0]) + float(nums[2])) / 2,
            (float(nums[1]) + float(nums[3])) / 2,
        )
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    return None


async def _execute_action(
    driver,
    orchestrator: CortexOrchestrator,
    action_type: str,
    selector_obj: dict,
    selector_kind: str,
    selector_val: str,
    input_val,
    fallbacks: list,
) -> tuple:
    """
    Try the primary selector, then each fallback in order.
    Returns (ok: bool, error_message: str | None).
    """
    # Build ordered candidate list: primary + fallbacks
    candidates = [selector_obj] + [f for f in (fallbacks or [])]

    last_err = None
    for candidate in candidates:
        kind = candidate.get("kind", "")
        val = candidate.get("value", "")

        ok, err = await _dispatch(
            driver, orchestrator, action_type, kind, val, input_val
        )
        if ok:
            return True, None
        last_err = err
        ch.warning(f"  ↳ selector {kind}:{val} failed — {err}. Trying fallback...")

    return False, last_err


async def _dispatch(
    driver,
    orchestrator: CortexOrchestrator,
    action_type: str,
    kind: str,
    val: str,
    input_val,
) -> tuple:
    """Dispatch a single action to the driver. Returns (ok, error_str)."""
    try:
        page = driver._page
        timeout = 5000

        # ── ref (a11y reference ID) ───────────────────────────────────
        if kind == "ref":
            try:
                ref_id = int(float(val))  # handles "5" and "5.0" from LLM
            except (ValueError, TypeError):
                return False, f"Invalid ref id: {val!r}"
            ref_map = orchestrator._ref_map

            if action_type == "click":
                res = await driver.click_by_ref(ref_id, ref_map, timeout=timeout)
                return res["ok"], res.get("error")

            if action_type in ("fill", "fill_fast"):
                node = ref_map.get(ref_id)
                if not node:
                    return False, f"No ref {ref_id}"
                role = node.get("role", "textbox")
                name = (node.get("name") or "").strip()
                if name:
                    locator = page.get_by_role(role, name=name)
                else:
                    locator = page.get_by_role(role).first
                await locator.fill(str(input_val), timeout=timeout)
                return True, None

        # ── role ──────────────────────────────────────────────────────
        if kind == "role":
            # Parse "button[name='Log in']"  →  role=button, name=Log in
            m = re.match(r"(\w+)\[name=['\"]?([^'\"]+)['\"]?\]", val)
            if m:
                role_name, elem_name = m.group(1), m.group(2)
                locator = page.get_by_role(role_name, name=elem_name)
            else:
                locator = page.get_by_role(val)

            if action_type == "click":
                await locator.click(timeout=timeout)
            elif action_type in ("fill", "fill_fast"):
                await locator.fill(str(input_val), timeout=timeout)
            elif action_type == "press":
                key = input_val if isinstance(input_val, str) else "Enter"
                await locator.press(key, timeout=timeout)
            return True, None

        # ── coords ────────────────────────────────────────────────────
        if kind == "coords":
            coords = _parse_coords(val)
            if not coords:
                return False, f"Cannot parse coords from '{val}'"
            x, y = coords
            ch.info(f"  coords→ ({x:.0f}, {y:.0f})")

            if action_type == "click":
                await page.mouse.click(x, y)
            elif action_type in ("fill", "fill_fast", "type"):
                await page.mouse.click(x, y)
                text = str(input_val)
                await page.keyboard.type(text, delay=30)
            elif action_type == "press":
                key = input_val if isinstance(input_val, str) else "Enter"
                await page.mouse.click(x, y)
                await page.keyboard.press(key)
            return True, None

        # ── css / xpath (default) ─────────────────────────────────────
        if action_type == "click":
            res = await driver.safe_click(val, timeout=timeout)
            return res["ok"], res.get("detail")

        if action_type == "fill_fast":
            res = await driver.fill_fast(val, str(input_val), timeout=timeout)
            return res["ok"], res.get("error")

        if action_type in ("fill", "type"):
            res = await driver.safe_fill(val, str(input_val), timeout=timeout)
            return res["ok"], res.get("detail")

        if action_type == "press":
            key = input_val if isinstance(input_val, str) and input_val else "Enter"
            # Focus the element first (if a selector was given), then press the key
            if val and val.strip():
                try:
                    await page.locator(val).focus(timeout=timeout)
                except Exception:
                    pass  # best-effort focus; key still dispatched globally
            await page.keyboard.press(key)
            return True, None

        if action_type == "scroll":
            try:
                delta = int(float(input_val)) if input_val else 400
            except (ValueError, TypeError):
                delta = 400
            await page.evaluate(f"window.scrollBy(0, {delta})")
            return True, None

        if action_type == "navigate":
            target_url = input_val or val
            await driver.navigate(str(target_url))
            return True, None

        if action_type == "wait":
            await driver.wait_for_stable(timeout_ms=2000)
            return True, None

        return False, f"Unknown action: {action_type}"

    except Exception as exc:
        return False, str(exc)[:200]
