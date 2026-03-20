"""
navig.engine — Execution pipeline, command queuing, and observable hooks.

Components
----------
hooks     — Publish/subscribe execution event system (before/after every tool run).
queue     — Lane-based async command queue with cancellation and timeout.
pipeline  — Composable tool chain that pipes ToolResult.output between steps.
"""

from .hooks import ExecutionEvent, ExecutionHooks, HookPhase
from .pipeline import PipelineResult, PipelineStep, ToolPipeline
from .queue import CommandQueue, LaneClearedError, TaskHandle

__all__ = [
    # Hooks
    "ExecutionHooks",
    "ExecutionEvent",
    "HookPhase",
    # Queue
    "CommandQueue",
    "LaneClearedError",
    "TaskHandle",
    # Pipeline
    "ToolPipeline",
    "PipelineStep",
    "PipelineResult",
]
