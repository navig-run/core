"""
Code Tool Pack - code_sandbox.

Wraps navig.tools.sandbox for the ToolRouter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def register_tools(registry: ToolRegistry) -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="code_sandbox",
            domain=ToolDomain.CODE,
            description="Execute code in a sandboxed Docker container.",
            safety=SafetyLevel.DANGEROUS,
            module_path="navig.tools.sandbox",
            handler_name="execute",
            parameters_schema={
                "code": {
                    "type": "string",
                    "required": True,
                    "description": "Code to execute",
                },
                "language": {
                    "type": "string",
                    "default": "python",
                    "description": "Programming language",
                },
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": "Execution timeout (seconds)",
                },
            },
            tags=["code", "execute", "sandbox", "docker"],
        )
    )
