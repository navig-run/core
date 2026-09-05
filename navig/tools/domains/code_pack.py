"""
Code Tool Pack - code_sandbox.

Wraps navig.tools.sandbox for the ToolRouter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def register_tools(registry: ToolRegistry) -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta, ToolStatus

    # This tool has never had a handler. It declared
    # `module_path="navig.tools.sandbox", handler_name="execute"` — and that
    # module exposes `sandboxed_execute(command, config=None, **kwargs)`, not
    # `execute`. `get_handler()` caught the AttributeError, flipped the status
    # to ERROR and returned None, so the LLM was offered `code_sandbox` (plus
    # its three aliases: sandbox / exec / run_code), spent a turn calling it and
    # got "No handler loaded".
    #
    # Renaming the string is NOT the fix: the real function takes a shell
    # `command`, while this tool's schema promises `code` + `language`, so
    # wiring them together means writing the code to a file inside the container
    # and choosing an interpreter — a new code-execution path on a DANGEROUS
    # tool, not a typo repair. Until that adapter exists the honest state is
    # UNAVAILABLE: `is_available()` is False, so the tool is filtered out of
    # `list_tools(available_only=True)` and the LLM prompt, and a direct call
    # returns "Tool unavailable: <reason>" instead of a mystery load failure.
    registry.register(
        ToolMeta(
            name="code_sandbox",
            domain=ToolDomain.CODE,
            description="Execute code in a sandboxed Docker container.",
            safety=SafetyLevel.DANGEROUS,
            status=ToolStatus.UNAVAILABLE,
            status_message=(
                "no handler wired: navig.tools.sandbox exposes "
                "sandboxed_execute(command), which does not implement this tool's "
                "code/language schema"
            ),
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
