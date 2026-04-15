"""
Hook executor — runs hook scripts as subprocesses and interprets exit codes.

Exit-code semantics (matching Claude Code's protocol):
  0            Silent success — nothing injected.
  2            Inject stderr into model context.  On PRE_TOOL_USE this
               *blocks* the tool call.
  other        Surface stderr to the user only — no model injection.

Stdout is always captured but currently unused (reserved for future structured
responses, e.g. ``{"hookSpecificOutput": {"retry": true}}``).

SSRF guard: HTTP/HTTPS hook commands are validated before execution if their
first token looks like a URL.  Private IP ranges are blocked.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import shlex
import subprocess
import urllib.parse
from typing import TYPE_CHECKING

from .events import HookContext, HookEvent, HookResult
from .registry import HookDefinition, HookRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger("navig.hooks.executor")

# Module-level constants
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_private_url(raw: str) -> bool:
    """Return True if *raw* is an HTTP/HTTPS URL pointing at a private/loopback address."""
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # domain name — allow; DNS resolution is not performed here
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


class HookExecutor:
    """Runs all hooks for a given context and aggregates results."""

    def __init__(self, registry: HookRegistry) -> None:
        self._registry = registry

    def run(self, ctx: HookContext) -> HookResult:
        """Execute all hooks matching *ctx* and return a merged result.

        Never raises — exceptions are swallowed and logged.
        """
        result = HookResult()
        try:
            hooks = self._registry.get_hooks_for_event(ctx.event, ctx.tool_name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("hooks.executor: registry lookup failed: %s", exc)
            return result

        for defn in hooks:
            try:
                self._run_one(ctx, defn, result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("hooks.executor: unhandled error in hook '%s': %s", defn.command, exc)

        return result

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _run_one(self, ctx: HookContext, defn: HookDefinition, result: HookResult) -> None:
        """Run a single hook definition and update *result* in place."""
        # SSRF guard: reject HTTP hooks targeting private networks unless explicitly allowed
        if not self._registry.allow_network:
            tokens = shlex.split(defn.command) if defn.command else []
            candidate = tokens[0] if tokens else ""
            if candidate.startswith(("http://", "https://")):
                logger.warning(
                    "hooks.executor: network hooks disabled — refusing '%s'", defn.command
                )
                return
        elif _is_private_url(defn.command.split()[0] if defn.command else ""):
            logger.warning(
                "hooks.executor: SSRF guard blocked private-IP hook '%s'", defn.command
            )
            return

        stdin_payload = ctx.to_json()
        logger.debug(
            "hooks.executor: running %s for event=%s tool=%s",
            defn.command,
            ctx.event.value,
            ctx.tool_name,
        )

        try:
            proc = subprocess.run(  # noqa: S602
                defn.command,
                shell=True,
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=defn.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "hooks.executor: hook '%s' timed out after %ds",
                defn.command,
                defn.timeout_seconds,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("hooks.executor: failed to run '%s': %s", defn.command, exc)
            return

        result.executed = True
        exit_code = proc.returncode
        stderr_text = (proc.stderr or "").strip()

        # Check for structured retry signal in stdout
        try:
            stdout_json = json.loads(proc.stdout or "")
            hook_output = stdout_json.get("hookSpecificOutput", {})
            if hook_output.get("retry"):
                result.retry = True
        except (json.JSONDecodeError, AttributeError):
            pass

        if exit_code == 0:
            # Silent success
            return

        if exit_code == 2:
            # Inject stderr into model context; block tool on PRE_TOOL_USE
            if ctx.event == HookEvent.PRE_TOOL_USE:
                result.block = True
            if stderr_text:
                result.message = (
                    f"{result.message}\n{stderr_text}" if result.message else stderr_text
                )
            logger.info(
                "hooks.executor: hook '%s' returned exit 2 (block=%s): %s",
                defn.command,
                result.block,
                stderr_text[:200],
            )
        else:
            # Surface to user but don't inject into model
            if stderr_text:
                logger.warning(
                    "hooks.executor: hook '%s' exited %d: %s",
                    defn.command,
                    exit_code,
                    stderr_text[:200],
                )
