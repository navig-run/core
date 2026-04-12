"""
Assistant Hooks for Command Execution

Provides pre/post execution hooks for the proactive assistant system.
"""

import time
from typing import Any

from navig import console_helper as ch


def _resolve_assistant(ctx_obj: dict[str, Any]):
    """Return the ProactiveAssistant instance, or None if unavailable.

    The middleware stores a *callable* under ``ctx_obj["get_assistant"]`` that
    waits (up to a short timeout) for the background load thread and then
    returns the instance or None.  Using the callable is the correct access
    pattern; falling back to the legacy ``ctx_obj["assistant"]`` key handles
    any direct assignments made by older code paths.
    """
    get_fn = ctx_obj.get("get_assistant")
    if callable(get_fn):
        return get_fn(timeout=0.2)
    # Legacy fallback: some code paths may store the instance directly.
    return ctx_obj.get("assistant")


def pre_execution_check(ctx_obj: dict[str, Any], command: str, args: dict[str, Any]) -> bool:
    """
    Check for pre-execution warnings before running a command.

    Args:
        ctx_obj: Context object with assistant and flags
        command: Command name (e.g., 'delete', 'sql', 'restart')
        args: Command arguments

    Returns:
        True if should proceed, False if user cancelled
    """
    if not ctx_obj.get("assistant_enabled"):
        return True

    assistant = _resolve_assistant(ctx_obj)
    if not assistant:
        return True

    # Skip warnings if --yes flag is set
    # void: --yes bypasses all safety checks. use with extreme caution.
    if ctx_obj.get("yes"):
        return True

    # Check for pre-execution warnings
    try:
        should_proceed, warnings = assistant.proactive_display.check_pre_execution_warnings(
            command=command, args=args, context=ctx_obj
        )

        if warnings:
            # Display warnings
            for warning in warnings:
                ch.warning(f"⚠️  {warning}")

            # If confirmation required and not auto-confirmed
            if not should_proceed and not ctx_obj.get("yes"):
                ch.warning("\n⚠️  This operation requires confirmation")
                confirm = input("Type 'yes' to proceed: ")
                return confirm.lower() == "yes"

        return should_proceed

    except Exception as e:
        # Don't block execution if assistant fails
        # void: the AI is helpful until it breaks. then we fall back to human judgment.
        if ctx_obj.get("verbose"):
            ch.dim(f"Assistant warning check failed: {e}")
        return True


def post_execution_log(
    ctx_obj: dict[str, Any],
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    duration: float = 0.0,
):
    """
    Log command execution and analyze errors if needed.

    Args:
        ctx_obj: Context object with assistant
        command: Command that was executed
        exit_code: Exit code (0 = success)
        stdout: Standard output
        stderr: Standard error
        duration: Execution time in seconds
    """
    if not ctx_obj.get("assistant_enabled"):
        return

    assistant = _resolve_assistant(ctx_obj)
    if not assistant:
        return

    try:
        # Log command execution
        assistant.auto_detection.log_command_execution(
            command=command,
            exit_code=exit_code,
            stderr=stderr,
            stdout=stdout,
            duration=duration,
            context={
                "dry_run": ctx_obj.get("dry_run", False),
                "verbose": ctx_obj.get("verbose", False),
            },
        )

        # If command failed and auto-analysis is enabled
        if exit_code != 0 and assistant.should_auto_analyze():
            analyze_and_suggest_solutions(ctx_obj, command, exit_code, stderr)

    except Exception as e:
        # Don't crash if logging fails
        if ctx_obj.get("verbose"):
            ch.dim(f"Assistant logging failed: {e}")


def analyze_and_suggest_solutions(
    ctx_obj: dict[str, Any], command: str, exit_code: int, error_message: str
):
    """
    Analyze error and display suggested solutions.

    Args:
        ctx_obj: Context object with assistant
        command: Failed command
        exit_code: Exit code
        error_message: Error message
    """
    if not ctx_obj.get("assistant_enabled"):
        return

    assistant = _resolve_assistant(ctx_obj)
    if not assistant:
        return

    try:
        # Get solutions from error resolution module
        solutions = assistant.error_resolution.analyze_error(
            command=command,
            exit_code=exit_code,
            error_message=error_message,
            context=ctx_obj,
        )

        if solutions:
            ch.info("\n🔧 Suggested Solutions:")
            assistant.error_resolution.display_solutions(solutions)
        else:
            # Fallback: provide generic helpful hint
            ch.info("\n💡 Tip: Check the error message above for details")
            ch.info("💡 Tip: Use 'navig assistant analyze' for system analysis")

    except Exception as e:
        # Don't crash if analysis fails
        if ctx_obj.get("verbose"):
            ch.dim(f"Assistant error analysis failed: {e}")


class CommandTimer:
    """Context manager for timing command execution."""

    def __init__(self):
        self.start_time = None
        self.duration = 0.0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = time.time() - self.start_time
        return False
