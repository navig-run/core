"""``browser_tool`` — a single, batchable CLI-like browser tool for the agent.

Ported from the navig-os agent browser UX (``session-tools-core`` +
``browser-pane-manager.ts``) onto navig-core's Playwright ``BrowserController``,
so core is the one canonical browser engine (Telegram + CLI consume it; navig-os
will delegate to it later).

The agent drives it with one ``command`` argument — a string (``;``-batchable) or
an array (one command, whitespace/semicolons preserved in args):

    navigate <url> | open <url>     go to a page
    back | forward | reload         history
    snapshot                        a11y tree with @eN refs (do this before click/fill)
    click @eN | click-at <x> <y>    click a ref, or raw pixel coords
    fill @eN <value>                type into a ref
    evaluate <js>                   run JS, return its value
    screenshot                      full-viewport JPEG (returned to the chat)
    screenshot-region <x> <y> <w> <h> | --ref @eN | --selector <css> [--padding N]
    extract | content               readable page text
    close                           end the browser session

Batches stop after a navigation-class command (navigate/open/back/forward/reload/
click/click-at) since page state changes — re-``snapshot`` after. Screenshots are
attached to the result and surfaced to Telegram automatically.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from navig.agent.tools.browser_session import (
    _close,
    get_or_open,
    run_on_browser_loop,
)
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_BATCH = 12
_PER_CMD_TIMEOUT = 30.0
_MAX_TEXT = 6_000

# Commands after which the rest of a batch is skipped (page state changed).
_NAV_CLASS = frozenset({"navigate", "open", "back", "forward", "reload", "click", "click-at"})
_VALID = _NAV_CLASS | {
    "snapshot", "fill", "evaluate", "screenshot", "screenshot-region",
    "extract", "content", "close",
}


# ── Parsing (pure, no browser) ──────────────────────────────────────────────


@dataclass
class ParsedCommand:
    verb: str
    rest: str = ""              # everything after the verb (for values/js)
    argv: list[str] = field(default_factory=list)  # rest.split()


def parse_commands(command: Any) -> list[ParsedCommand]:
    """Parse the ``command`` arg into a list of ParsedCommand.

    - list  → ONE command (argv preserved verbatim; no ``;`` splitting)
    - str   → split on top-level ``;`` into a batch of commands
    """
    if isinstance(command, list):
        parts = [str(x) for x in command]
        if not parts:
            return []
        verb = parts[0].strip().lower()
        argv = parts[1:]
        return [ParsedCommand(verb=verb, rest=" ".join(argv), argv=argv)]

    text = str(command or "").strip()
    out: list[ParsedCommand] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        bits = chunk.split(None, 1)
        verb = bits[0].strip().lower()
        rest = bits[1].strip() if len(bits) > 1 else ""
        out.append(ParsedCommand(verb=verb, rest=rest, argv=rest.split()))
    return out


def _ref_id(token: str) -> int | None:
    """'@e12' / 'e12' / '12' → 12; else None."""
    m = re.fullmatch(r"@?e?(\d+)", token.strip(), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _relabel_refs(annotated: str) -> str:
    """Rewrite the controller's leading ``[N]`` ref markers to the ``@eN`` form."""
    return re.sub(r"(?m)^(\s*)\[(\d+)\]", r"\1@e\2", annotated)


# ── Execution (runs ON the browser loop) ────────────────────────────────────


@dataclass
class _CmdResult:
    text: str
    screenshot: bytes | None = None
    url: str | None = None
    mutated: bool = False
    stop_batch: bool = False


async def _grab_screenshot(ctrl: Any) -> bytes | None:
    try:
        return base64.b64decode(await ctrl.screenshot_base64(quality=60))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser] screenshot failed: %s", exc)
        return None


async def _run_one(sess: Any, cmd: ParsedCommand) -> _CmdResult:
    ctrl = sess.controller
    v = cmd.verb

    if v in ("navigate", "open"):
        if not cmd.argv:
            return _CmdResult("navigate: missing <url>")
        url = cmd.argv[0]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        info = await ctrl.navigate(url)
        return _CmdResult(
            f"navigated → {info.get('url')} · {info.get('title')!r} (HTTP {info.get('status')})",
            url=info.get("url"), mutated=True, stop_batch=True,
        )

    if v == "back":
        await ctrl.go_back()
        return _CmdResult("went back", mutated=True, stop_batch=True)
    if v == "forward":
        await ctrl.go_forward()
        return _CmdResult("went forward", mutated=True, stop_batch=True)
    if v == "reload":
        await ctrl.reload()
        return _CmdResult("reloaded", mutated=True, stop_batch=True)

    if v == "snapshot":
        annotated, ref_map = await ctrl.get_a11y_snapshot_with_refs()
        sess.ref_map = ref_map or {}
        body = _relabel_refs(annotated or "").strip()
        if not body:
            return _CmdResult("snapshot: no a11y tree (canvas/PDF page?) — try screenshot")
        return _CmdResult("a11y snapshot (@eN = clickable refs):\n" + body[:_MAX_TEXT])

    if v == "click":
        if not cmd.argv or (ref := _ref_id(cmd.argv[0])) is None:
            return _CmdResult("click: expected a ref, e.g. `click @e12` (run `snapshot` first)")
        if not sess.ref_map:
            return _CmdResult("click: no refs yet — run `snapshot` before clicking")
        # Hard safe-mode gate (navig do without --yes): refuse outward-action clicks
        # (Send/Submit/Publish/Pay/Delete…) at the tool layer, not just via a prompt.
        _node = (sess.ref_map or {}).get(ref) or {}
        from navig.browser.safe_mode import blocked_reason
        _blocked = blocked_reason(f"{_node.get('name', '')} {_node.get('role', '')}")
        if _blocked:
            return _CmdResult(_blocked)
        res = await ctrl.click_by_ref(ref, sess.ref_map)
        if not res.get("ok"):
            hint = res.get("suggestion", "")
            return _CmdResult(f"click @e{ref} failed: {res.get('error')}{' — ' + hint if hint else ''}")
        return _CmdResult(f"clicked @e{ref}", mutated=True, stop_batch=True)

    if v == "click-at":
        if len(cmd.argv) < 2 or not cmd.argv[0].lstrip("-").isdigit():
            return _CmdResult("click-at: expected `<x> <y>` pixel coords")
        x, y = int(cmd.argv[0]), int(cmd.argv[1])
        await ctrl._page.mouse.click(x, y)
        return _CmdResult(f"clicked at ({x}, {y})", mutated=True, stop_batch=True)

    if v == "fill":
        if not cmd.argv or (ref := _ref_id(cmd.argv[0])) is None:
            return _CmdResult("fill: expected `fill @eN <value>` (run `snapshot` first)")
        value = cmd.rest.split(None, 1)[1] if len(cmd.rest.split(None, 1)) > 1 else ""
        node = (sess.ref_map or {}).get(ref)
        if not node:
            return _CmdResult(f"fill: no element with ref @e{ref} — re-`snapshot`")
        role, name = (node.get("role") or "").strip(), (node.get("name") or "").strip()
        loc = ctrl._page.get_by_role(role, name=name) if (role and name) else ctrl._page.get_by_role(role)
        await loc.first.fill(value)
        return _CmdResult(f"filled @e{ref} with {value!r}", mutated=True)

    if v == "evaluate":
        if not cmd.rest:
            return _CmdResult("evaluate: missing <js>")
        val = await ctrl.evaluate(cmd.rest)
        return _CmdResult(f"evaluate → {str(val)[:1000]}")

    if v == "screenshot":
        sb = await _grab_screenshot(ctrl)
        return _CmdResult("screenshot captured" if sb else "screenshot failed", screenshot=sb)

    if v == "screenshot-region":
        clip = await _resolve_region(ctrl, sess, cmd.argv)
        if clip is None:
            return _CmdResult("screenshot-region: give `<x> <y> <w> <h>` or `--ref @eN` or `--selector <css>`")
        raw = await ctrl._page.screenshot(clip=clip, type="jpeg", quality=60)
        return _CmdResult("region screenshot captured", screenshot=raw)

    if v in ("extract", "content"):
        txt = ""
        try:
            txt = await ctrl.get_text()
        except Exception:  # noqa: BLE001
            txt = ""
        if not (txt or "").strip():
            txt = await ctrl.get_content()
        return _CmdResult((txt or "").strip()[:_MAX_TEXT] or "(empty page)")

    if v == "close":
        return _CmdResult("__close__")

    return _CmdResult(f"unknown command {v!r} — valid: {', '.join(sorted(_VALID))}")


async def _resolve_region(ctrl: Any, sess: Any, argv: list[str]) -> dict | None:
    pad = 0
    if "--padding" in argv:
        i = argv.index("--padding")
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            pad = int(argv[i + 1])
    box: dict | None = None
    if "--ref" in argv:
        i = argv.index("--ref")
        ref = _ref_id(argv[i + 1]) if i + 1 < len(argv) else None
        node = (sess.ref_map or {}).get(ref) if ref is not None else None
        if node:
            role, name = (node.get("role") or "").strip(), (node.get("name") or "").strip()
            loc = ctrl._page.get_by_role(role, name=name) if (role and name) else ctrl._page.get_by_role(role)
            box = await loc.first.bounding_box()
    elif "--selector" in argv:
        i = argv.index("--selector")
        if i + 1 < len(argv):
            el = await ctrl._page.query_selector(argv[i + 1])
            box = await el.bounding_box() if el else None
    elif len(argv) >= 4 and all(a.lstrip("-").isdigit() for a in argv[:4]):
        x, y, w, h = (int(a) for a in argv[:4])
        box = {"x": x, "y": y, "width": w, "height": h}
    if not box:
        return None
    return {
        "x": max(0, box["x"] - pad),
        "y": max(0, box["y"] - pad),
        "width": box["width"] + 2 * pad,
        "height": box["height"] + 2 * pad,
    }


async def _execute(key: str, parsed: list[ParsedCommand]) -> dict:
    """Run a parsed batch against the session's controller. Runs on the browser loop."""
    import asyncio

    sess = await get_or_open(key)
    lines: list[str] = []
    shot: bytes | None = None
    page_url: str | None = None
    mutated = False

    for idx, cmd in enumerate(parsed):
        if cmd.verb == "close":
            # Close directly (we're already on the browser loop — never call the
            # sync close_session() here or it would deadlock on this same loop).
            await _close(key)
            lines.append("browser session closed")
            return {"text": "\n".join(lines), "screenshot": None, "url": None}
        try:
            r = await asyncio.wait_for(_run_one(sess, cmd), timeout=_PER_CMD_TIMEOUT)
        except asyncio.TimeoutError:
            lines.append(f"{cmd.verb}: timed out ({int(_PER_CMD_TIMEOUT)}s)")
            break
        except Exception as exc:  # noqa: BLE001 - per-command isolation
            lines.append(f"{cmd.verb}: error — {str(exc)[:200]}")
            break
        lines.append(r.text)
        if r.screenshot is not None:
            shot = r.screenshot
        if r.url:
            page_url = r.url
        if r.mutated:
            mutated = True
        if r.stop_batch and idx < len(parsed) - 1:
            lines.append(f"(batch stopped after '{cmd.verb}'; re-snapshot before more actions)")
            break

    if mutated and shot is None:
        shot = await _grab_screenshot(sess.controller)
    if page_url is None and getattr(sess.controller, "is_running", False):
        try:
            page_url = sess.controller._page.url
        except Exception:  # noqa: BLE001
            page_url = None
    return {"text": "\n".join(lines), "screenshot": shot, "url": page_url}


# ── The tool ────────────────────────────────────────────────────────────────


class BrowserTool(BaseTool):
    name = "browser_tool"
    owner_only = True
    description = (
        "Drive a real headless browser to read/act on live websites. ONE `command` "
        "arg — a string (batch with `;`) or array. Commands:\n"
        "  navigate <url> | back | forward | reload\n"
        "  snapshot            → a11y tree with @eN refs (ALWAYS snapshot before click/fill)\n"
        "  click @eN | click-at <x> <y> | fill @eN <value>\n"
        "  evaluate <js> | extract\n"
        "  screenshot | screenshot-region <x y w h | --ref @eN | --selector <css>> [--padding N]\n"
        "  close\n"
        "Batches stop after navigate/click (page changes) — re-snapshot then. Screenshots "
        "are shown to the user automatically. Typical flow: `navigate URL` → `snapshot` → "
        "`click @e5` → `extract`."
    )
    parameters = [
        {
            "name": "command",
            "type": "string",
            "required": True,
            "description": "Browser command, e.g. 'navigate https://news.ycombinator.com' or "
                           "'snapshot' or 'click @e12' or batched 'fill @e1 hi; click @e2'.",
        }
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        command = args.get("command")
        if command is None or (isinstance(command, str) and not command.strip()):
            return ToolResult(name=self.name, success=False, error="command arg required")

        parsed = parse_commands(command)
        if not parsed:
            return ToolResult(name=self.name, success=False, error="no command parsed")
        if len(parsed) > _MAX_BATCH:
            return ToolResult(
                name=self.name, success=False,
                error=f"too many batched commands ({len(parsed)} > {_MAX_BATCH})",
            )
        bad = [c.verb for c in parsed if c.verb not in _VALID]
        if bad:
            return ToolResult(
                name=self.name, success=False,
                error=f"unknown command(s): {', '.join(bad)}. Valid: {', '.join(sorted(_VALID))}",
            )

        key = str(args.get("_session_id") or "_default")
        await self._emit(on_status, "Browsing…", parsed[0].rest[:60] or parsed[0].verb, 30)

        try:
            result = run_on_browser_loop(_execute(key, parsed))
        except RuntimeError as exc:  # Playwright/Chromium missing
            return ToolResult(name=self.name, success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - never raise (BaseTool contract)
            logger.exception("browser_tool failed")
            return ToolResult(name=self.name, success=False, error=str(exc)[:300])

        output: dict[str, Any] = {"result": result["text"]}
        if result.get("url"):
            output["url"] = result["url"]
        if result.get("screenshot"):
            output["_screenshot"] = result["screenshot"]  # → Telegram photo seam
        return ToolResult(name=self.name, success=True, output=output)
