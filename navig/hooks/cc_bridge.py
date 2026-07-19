"""
Claude Code hooks → NAVIG hooks bridge.

A Claude Code plugin ships `hooks/hooks.json` in CC's schema::

    { "PreToolUse": [ { "matcher": "Bash",
                        "hooks": [ { "type": "command", "command": "./guard.sh",
                                     "timeout": 5 } ] } ],
      "PostToolUse": [ ... ] }

NAVIG's `HookRegistry` uses a flat `HookDefinition(event, command, tool_filter,
timeout_seconds)` list. This module translates the former into the latter so a CC
plugin's hooks fire through NAVIG's existing `HookExecutor` — whose stdin payload
(`HookContext.to_json`: `tool_name` / `tool_input` / `session_id`) is already
CC-compatible, and whose exit-code gating (2 = block on PreToolUse) matches CC.

Only `type: "command"` hooks and events NAVIG understands are translated; anything
else is skipped (logged), never fatal — degraded-never-blocks-boot.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .events import HookEvent
from .registry import _DEFAULT_HOOK_TIMEOUT_SECONDS, HookDefinition

logger = logging.getLogger("navig.hooks.cc_bridge")

# CC event name → NAVIG HookEvent (only the ones NAVIG can fire today).
_EVENT_ALIASES: dict[str, HookEvent] = {e.value: e for e in HookEvent}
_EVENT_ALIASES.update({e.name: e for e in HookEvent})


def _split_matcher(matcher: Any) -> list[str]:
    """CC matcher → NAVIG tool_filter(s).

    `"*"`/`""`/None → `[""]` (all tools). `"Edit|Write"` (CC regex alternation) →
    `["edit", "write"]`. A plain name → its lowercased glob. `matches_tool` uses
    fnmatch (case-insensitive), so `*`/`?` pass through.
    """
    if matcher is None:
        return [""]
    text = str(matcher).strip()
    if text in ("", "*", ".*"):
        return [""]
    parts = [p.strip().lower() for p in text.split("|") if p.strip()]
    # Normalise the common regex `.*` → glob `*`.
    return [p.replace(".*", "*") for p in parts] or [""]


def translate_cc_hooks(
    data: Any,
    *,
    default_timeout: int = _DEFAULT_HOOK_TIMEOUT_SECONDS,
    source: str = "",
) -> list[HookDefinition]:
    """Translate a CC `hooks.json` payload into NAVIG `HookDefinition`s.

    Accepts either the event-map directly or `{"hooks": {event-map}}`. Never
    raises — malformed entries are skipped with a warning.
    """
    if not isinstance(data, dict):
        return []
    events = data.get("hooks", data)
    if not isinstance(events, dict):
        return []

    out: list[HookDefinition] = []
    for event_name, groups in events.items():
        event = _EVENT_ALIASES.get(str(event_name))
        if event is None:
            logger.debug("cc_bridge: event %r not supported by NAVIG — skipping", event_name)
            continue
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher", "")
            for hook in group.get("hooks", []) or []:
                if not isinstance(hook, dict):
                    continue
                if str(hook.get("type", "command")) != "command":
                    logger.debug("cc_bridge: non-command hook skipped (source=%s)", source)
                    continue
                command = str(hook.get("command", "")).strip()
                if not command:
                    continue
                try:
                    timeout = int(hook.get("timeout", default_timeout))
                except (TypeError, ValueError):
                    timeout = default_timeout
                command = os.path.expanduser(command)
                desc = f"plugin:{source}" if source else "plugin hook"
                for tool_filter in _split_matcher(matcher):
                    out.append(
                        HookDefinition(
                            event=event,
                            command=command,
                            tool_filter=tool_filter,
                            timeout_seconds=timeout,
                            description=desc,
                        )
                    )
    return out


def load_plugin_hook_definitions(
    *, default_timeout: int = _DEFAULT_HOOK_TIMEOUT_SECONDS
) -> list[HookDefinition]:
    """Discover + translate every installed plugin's `hooks/hooks.json`.

    Uses the plugin-host seam (`installed_plugin_roots`). Best-effort — a bad or
    missing file for one plugin never affects the others or blocks hook loading.
    """
    import json

    defs: list[HookDefinition] = []
    try:
        from navig.plugins.package import installed_plugin_roots
    except Exception:  # noqa: BLE001
        return defs

    for root in installed_plugin_roots():
        for hp in (root / "hooks" / "hooks.json", root / "hooks.json"):
            if not hp.exists():
                continue
            try:
                data = json.loads(hp.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("cc_bridge: bad hooks.json in %s: %s", root.name, exc)
                break
            defs.extend(translate_cc_hooks(data, default_timeout=default_timeout, source=root.name))
            break
    return defs
