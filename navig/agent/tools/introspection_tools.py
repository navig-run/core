"""
navig.agent.tools.introspection_tools — read-only runtime inspection (Mission B 4c).

Thin, read-only model-facing wrappers over EXISTING CLI/core surfaces — no new
introspection service:

* ``plugin_inspect`` — health report for a plugin/package dir
  (``navig plugin inspect`` → :func:`navig.plugins.package.load_package`)
* ``module_list``    — registered modules overview or one module's details
  (``navig modules list|info`` → :func:`navig.modules.registry.get_registry`)

Exposure: toolset ``introspection`` — never on by default; reachable through
the ``full`` toolset (and by any session that requests the tag). Discovery
without inflating every request's schema. Mutations (install/remove/enable)
stay operator-authorized CLI paths — never model tools.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from navig.agent.tools.file_tools import _workshop_path
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

#: Cap for the module listing (a registry row per registered module).
_MAX_MODULE_ROWS = 300


class PluginInspectTool(BaseTool):
    """Inspect a plugin/package directory (read-only health report)."""

    name = "plugin_inspect"
    description = (
        "Inspect a plugin/package directory and return its health report as "
        "JSON (id, kind, summary, degraded components). Read-only — use the "
        "navig plugin CLI for install/remove operations."
    )
    owner_only = False
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Path to the plugin/package directory (relative paths resolve to the workspace)",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        raw = str(args.get("path") or "").strip()
        if not raw:
            return ToolResult(
                name=self.name, success=False, error="'path' arg is required"
            )
        path = _workshop_path(raw)
        try:
            from navig.plugins.package import load_package  # noqa: PLC0415

            pkg = load_package(str(path))
            report = {"summary": pkg.summary(), **pkg.health.to_dict()}
        except FileNotFoundError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"No plugin/package at {path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 — report, never raise into the loop
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Could not inspect {path}: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(report, default=str),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class ModuleListTool(BaseTool):
    """List registered modules, or show one module's details (read-only)."""

    name = "module_list"
    description = (
        "List NAVIG's registered modules (id, kind, label, tier lock, "
        "enabled/disabled) as JSON; pass 'module' for one module's full "
        "details. Read-only — enable/disable stays a CLI action."
    )
    owner_only = False
    parameters = [
        {
            "name": "module",
            "type": "string",
            "description": "Optional module id to inspect in detail",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        module_id = str(args.get("module") or "").strip()
        try:
            from navig.modules.registry import get_registry  # noqa: PLC0415

            reg = get_registry().discover()
            if module_id:
                m = reg.get(module_id)
                if m is None:
                    return ToolResult(
                        name=self.name,
                        success=False,
                        error=f"Unknown module {module_id!r}",
                        elapsed_ms=(time.monotonic() - t0) * 1000,
                    )
                rendered = next(
                    (
                        r
                        for r in reg.list_modules(include_dev=True)
                        if r["id"] == module_id
                    ),
                    None,
                )
                detail = {
                    "id": m.id,
                    "label": m.label,
                    "description": m.description,
                    "kind": getattr(m.kind, "value", str(m.kind)),
                    "category": m.category,
                    "source": m.source,
                    "capability": m.capability,
                    "min_tier": m.min_tier,
                    "locked": bool(rendered and rendered.get("locked")),
                    "surfaces": list(m.surfaces or []),
                    "requires": list(m.requires or []),
                    "enabled": reg.is_enabled(module_id),
                }
                return ToolResult(
                    name=self.name,
                    success=True,
                    output=json.dumps(detail, default=str),
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )

            rows = []
            for r in reg.list_modules(include_dev=True)[:_MAX_MODULE_ROWS]:
                rows.append(
                    {
                        "id": r.get("id"),
                        "kind": r.get("kind"),
                        "label": r.get("label") or r.get("id"),
                        "locked": r.get("locked"),
                    }
                )
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps(
                    {
                        "modules": rows,
                        "total": len(rows),
                        "truncated": len(rows) >= _MAX_MODULE_ROWS,
                    },
                    default=str,
                ),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 — report, never raise into the loop
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Could not read the module registry: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )


def register_introspection_tools() -> None:
    """Register plugin_inspect + module_list (toolset ``introspection``)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(PluginInspectTool(), toolset="introspection")
    _AGENT_REGISTRY.register(ModuleListTool(), toolset="introspection")
    logger.debug("Introspection tools registered: plugin_inspect, module_list")
