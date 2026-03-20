"""
NAVIG Browser Task Orchestrator

Python-side bridge between the AI planner and the Go browser executor (router.go).
Handles the full task lifecycle including:
  - Sending task specs to the Go daemon via HTTP
  - Detecting NeedsHuman signals (captcha, 2fa, blocked)
  - Routing HitL signals to the CommsRouter (Matrix → Telegram → SMS)
  - Resuming tasks after human input
  - Auto-learning from completed tasks (knowledge graph)

Usage:
    from navig.integrations.browser_orchestrator import run_browser_task

    result = await run_browser_task({
        "intent": "login",
        "target": {"url": "https://github.com"},
        "routing": {"profile": "work", "engine": "auto"},
        "steps": [
            {"goto": {"url": "https://github.com/login"}},
            {"vault_fill": {
                "credential_id": "github_work",
                "username_selector": "#login_field",
                "password_selector": "#password",
                "submit_selector": "input[type=submit]"
            }},
            {"wait": {"kind": "dom_ready"}},
            {"get_dom": {"save_to_artifact": True}},
        ]
    })
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

_DAEMON_BASE = "http://127.0.0.1:7421"
_TASK_ENDPOINT = f"{_DAEMON_BASE}/api/v1/browser/task"
_TIMEOUT = 120  # seconds per task run


async def run_browser_task(
    task_spec: Dict[str, Any],
    *,
    max_hitl_retries: int = 3,
    on_progress=None,  # optional async callback(event: str, data: dict)
) -> Dict[str, Any]:
    """
    Execute a browser task via the Go daemon, handling HitL interrupts.

    When the daemon returns NeedsHuman:
      1. Routes to CommsRouter (Matrix → Telegram → SMS fallback)
      2. Waits for human input
      3. Injects the answer as an extra step (fill 2FA field / resume after CAPTCHA)
      4. Retries the task from the interrupted point

    Returns the final TaskRunResponse dict.
    Raises RuntimeError if the task permanently fails.
    """
    from navig.integrations.comms_router import get_comms_router

    comms = get_comms_router()
    task_id = task_spec.get("taskId") or str(uuid.uuid4())[:8]
    task_spec.setdefault("taskId", task_id)

    for attempt in range(max_hitl_retries + 1):
        if attempt > 0:
            logger.info("Browser task %s: retry %d/%d", task_id, attempt, max_hitl_retries)

        # POST task to Go daemon
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TASK_ENDPOINT, json=task_spec)
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Browser daemon unreachable: {exc}") from exc

        needs_human = result.get("needsHuman", "")
        final_url = result.get("finalUrl", "")
        title = result.get("title", "")
        screenshot = None
        if result.get("artifacts", {}).get("screenshotPaths"):
            screenshot = result["artifacts"]["screenshotPaths"][-1]

        if on_progress:
            await on_progress("step_complete", {"attempt": attempt, "needs_human": needs_human})

        # ── No human needed: done ─────────────────────────────────────────────
        if not needs_human:
            logger.info("Browser task %s: complete (%s)", task_id, title)
            await comms.send_task_complete(
                task_spec.get("intent", title or task_id),
                success=True,
                screenshot_path=screenshot,
            )
            return result

        # ── Human in the loop ─────────────────────────────────────────────────
        context = final_url or title or task_spec.get("target", {}).get("url", "")
        human_reply = await comms.handle_browser_pause(
            signal=needs_human,
            context=context,
            screenshot_path=screenshot,
        )

        if needs_human == "2fa" and human_reply:
            # Inject 2FA code as fill steps before resubmitting
            _inject_2fa_steps(task_spec, human_reply)
            continue  # retry with code injected

        if needs_human == "captcha":
            reply_lower = human_reply.lower() if human_reply else ""
            if "continue" in reply_lower:
                # User says they solved it — just retry as-is
                continue
            else:
                # Skip or abort
                break

        # blocked or unknown signal
        break

    # Task failed after all retries
    await comms.send_task_complete(
        task_spec.get("intent", task_id),
        success=False,
        screenshot_path=screenshot if 'screenshot' in dir() else None,
    )
    return result


def _inject_2fa_steps(task_spec: Dict[str, Any], code: str) -> None:
    """
    Best-effort 2FA code injection: add a fill step for common 2FA selectors
    before the existing steps list. The existing steps stay intact so the
    navigation they accomplished (logged in, redirected) isn't repeated.
    
    In production this would be smarter (DOM tree inspection), but this
    handles the 90% case for major services (GitHub, Google, etc).
    """
    common_2fa_selectors = [
        "input[name='otp']",
        "input[name='code']",
        "input[name='totp']",
        "input[name='mfa_code']",
        "input[autocomplete='one-time-code']",
        "#app_totp",
        "#totp",
        "[data-testid='otp-input']",
    ]

    inject = []
    for sel in common_2fa_selectors:
        inject.append({"fill": {"target": sel, "value": code}})
    # Try to submit
    inject.append({"click": {"target": "button[type=submit], input[type=submit], [data-action*=verify]"}})
    inject.append({"wait": {"kind": "dom_ready"}})

    # Prepend inject steps (after any goto step at position 0)
    steps = task_spec.get("steps", [])
    if steps and "goto" in steps[0]:
        task_spec["steps"] = [steps[0]] + inject + steps[1:]
    else:
        task_spec["steps"] = inject + steps
