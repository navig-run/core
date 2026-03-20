"""
navig.engine.pipeline — Composable tool-chaining pipeline.

A :class:`ToolPipeline` chains :class:`PipelineStep` instances so that the
output of step N is automatically available as input to step N+1.

Each step may:
- Specify a fixed ``args`` dict (merged over the accumulated context).
- Provide an ``input_key`` to expose the prior output under a named key.
- Provide a ``transform`` callable to reshape the prior output before passing it on.

Usage
-----
    from navig.tools.registry import ToolRegistry
    from navig.engine.pipeline import ToolPipeline, PipelineStep

    registry = ToolRegistry()
    # ... register tools ...

    pipe = ToolPipeline(registry)
    pipe.add(PipelineStep("web_fetch",   args={"url": "https://example.com"}))
    pipe.add(PipelineStep("summarize",   input_key="text"))
    pipe.add(PipelineStep("store_memory", input_key="content", args={"key": "summary"}))

    result = await pipe.run()
    print(result.final_output)
    print(result.succeeded)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step definition
# ---------------------------------------------------------------------------

Transform = Callable[[Any], Any]


@dataclass
class PipelineStep:
    """A single step in a :class:`ToolPipeline`.

    Parameters
    ----------
    tool_name:
        Name of the registered tool to invoke.
    args:
        Static arguments merged over the accumulated context dict.
        These take precedence over context keys.
    input_key:
        If set, the previous step's output is injected into the args dict
        under this key.  If unset, the raw prior output is not forwarded
        automatically (the step must be self-contained or pull from context).
    output_key:
        If set, the output of this step is stored in the context dict under
        this key and made available to subsequent steps.
        If unset, defaults to ``"_last"``.
    transform:
        Optional callable applied to the prior step's output *before* it is
        placed into the context / forwarded as ``input_key``.
    required:
        If True (default), a failed step aborts the pipeline.
        If False, the pipeline continues to the next step on failure.
    """

    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    input_key: Optional[str] = None
    output_key: Optional[str] = None
    transform: Optional[Transform] = None
    required: bool = True


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Result of a single pipeline step."""

    step_index: int
    tool_name: str
    success: bool
    output: Any
    error: Optional[str]
    elapsed_ms: float
    skipped: bool = False


@dataclass
class PipelineResult:
    """Aggregate result of a complete pipeline run."""

    steps: List[StepResult]
    context: Dict[str, Any]  # accumulated key/value store after all steps
    total_elapsed_ms: float
    aborted_at: Optional[int] = None  # step index where the pipeline aborted

    @property
    def succeeded(self) -> bool:
        return self.aborted_at is None

    @property
    def final_output(self) -> Any:
        return self.context.get("_last")

    def __repr__(self) -> str:  # pragma: no cover
        status = "ok" if self.succeeded else f"aborted@{self.aborted_at}"
        return (
            f"PipelineResult(steps={len(self.steps)} "
            f"status={status} "
            f"elapsed={self.total_elapsed_ms:.0f}ms)"
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class ToolPipeline:
    """Executes a sequence of tool steps, passing context between them.

    Parameters
    ----------
    registry:
        A :class:`navig.tools.registry.ToolRegistry` instance (or any object
        with an async ``run_tool(name, args, on_status)`` method).
    initial_context:
        Seed values for the context dict; available to steps as static args.
    """

    def __init__(
        self,
        registry: Any,
        *,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._registry = registry
        self._steps: List[PipelineStep] = []
        self._initial_context: Dict[str, Any] = initial_context or {}

    # ------------------------------------------------------------------

    def add(self, step: PipelineStep) -> "ToolPipeline":
        """Append a step and return *self* for fluent chaining."""
        self._steps.append(step)
        return self

    def __len__(self) -> int:
        return len(self._steps)

    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        initial_input: Any = None,
        on_step: Optional[Callable[[StepResult], None]] = None,
    ) -> PipelineResult:
        """Execute all steps sequentially.

        Parameters
        ----------
        initial_input:
            Seed value treated as if a virtual step 0 produced this output.
            Available to the first step via its ``input_key``.
        on_step:
            Optional sync callback invoked after each step completes (useful
            for streaming progress updates).
        """
        t_pipeline_start = time.monotonic()
        context: Dict[str, Any] = dict(self._initial_context)
        step_results: List[StepResult] = []
        prior_output: Any = initial_input

        for idx, step in enumerate(self._steps):
            args = dict(context)      # start with accumulated context
            args.update(step.args)    # step's own args take precedence

            # Inject prior output under input_key (with optional transform)
            if step.input_key and prior_output is not None:
                value = step.transform(prior_output) if step.transform else prior_output
                args[step.input_key] = value

            t_step = time.monotonic()
            status_log: List[str] = []

            def _on_status(msg: str) -> None:  # noqa: B023
                status_log.append(msg)

            try:
                tool_result = await self._registry.run_tool(
                    step.tool_name, args, on_status=_on_status
                )
                elapsed_ms = (time.monotonic() - t_step) * 1000

                sr = StepResult(
                    step_index=idx,
                    tool_name=step.tool_name,
                    success=tool_result.success,
                    output=tool_result.output,
                    error=tool_result.error,
                    elapsed_ms=elapsed_ms,
                )
                step_results.append(sr)

                if on_step:
                    try:
                        on_step(sr)
                    except Exception:  # observer failures never abort pipeline
                        pass

                if not tool_result.success and step.required:
                    logger.debug(
                        "ToolPipeline: required step %d (%s) failed — aborting",
                        idx,
                        step.tool_name,
                    )
                    return PipelineResult(
                        steps=step_results,
                        context=context,
                        total_elapsed_ms=(time.monotonic() - t_pipeline_start) * 1000,
                        aborted_at=idx,
                    )

                # Store output in context
                out_key = step.output_key or "_last"
                context[out_key] = tool_result.output
                context["_last"] = tool_result.output
                prior_output = tool_result.output

            except Exception as exc:
                elapsed_ms = (time.monotonic() - t_step) * 1000
                logger.exception(
                    "ToolPipeline: unexpected exception at step %d (%s)",
                    idx,
                    step.tool_name,
                )
                sr = StepResult(
                    step_index=idx,
                    tool_name=step.tool_name,
                    success=False,
                    output=None,
                    error=str(exc),
                    elapsed_ms=elapsed_ms,
                )
                step_results.append(sr)
                if on_step:
                    try:
                        on_step(sr)
                    except Exception:
                        pass

                if step.required:
                    return PipelineResult(
                        steps=step_results,
                        context=context,
                        total_elapsed_ms=(time.monotonic() - t_pipeline_start) * 1000,
                        aborted_at=idx,
                    )

        return PipelineResult(
            steps=step_results,
            context=context,
            total_elapsed_ms=(time.monotonic() - t_pipeline_start) * 1000,
        )
