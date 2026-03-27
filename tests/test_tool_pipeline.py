"""
Tests for navig.engine.pipeline — ToolPipeline composable chaining.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pytest

from navig.engine.pipeline import PipelineStep, ToolPipeline

# ---------------------------------------------------------------------------
# Minimal mock registry
# ---------------------------------------------------------------------------


@dataclass
class _MockResult:
    name: str
    success: bool
    output: Any
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    status_events: List[str] = None

    def __post_init__(self):
        if self.status_events is None:
            self.status_events = []


class _MockRegistry:
    def __init__(self, mapping: Dict[str, Any]):
        """mapping: {tool_name: value_or_callable}"""
        self._mapping = mapping

    async def run_tool(
        self,
        name: str,
        args: Dict[str, Any],
        on_status: Optional[Callable] = None,
    ) -> _MockResult:
        if name not in self._mapping:
            return _MockResult(
                name=name, success=False, output=None, error="tool not found"
            )
        v = self._mapping[name]
        if callable(v):
            result = v(args)
        else:
            result = v
        if isinstance(result, _MockResult):
            return result
        return _MockResult(name=name, success=True, output=result)


# ---------------------------------------------------------------------------
# Basic chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_step_pipeline():
    reg = _MockRegistry({"echo": "hello"})
    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("echo"))
    result = await pipe.run()
    assert result.succeeded
    assert result.final_output == "hello"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_two_step_pipeline_passes_output():
    def upper(args):
        return args.get("text", "").upper()

    reg = _MockRegistry(
        {
            "step1": "hello world",
            "step2": upper,
        }
    )

    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("step1", output_key="text"))
    pipe.add(PipelineStep("step2", input_key="text"))

    result = await pipe.run()
    assert result.succeeded
    assert result.final_output == "HELLO WORLD"


@pytest.mark.asyncio
async def test_pipeline_with_initial_input():
    def shout(args):
        return args.get("text", "") + "!"

    reg = _MockRegistry({"shout": shout})
    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("shout", input_key="text"))
    result = await pipe.run(initial_input="hello")
    assert result.final_output == "hello!"


# ---------------------------------------------------------------------------
# Abort on required failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_failure_aborts_pipeline():
    reg = _MockRegistry(
        {
            "fail": _MockResult(name="fail", success=False, output=None, error="oops"),
            "after": "should not run",
        }
    )

    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("fail", required=True))
    pipe.add(PipelineStep("after"))

    result = await pipe.run()
    assert not result.succeeded
    assert result.aborted_at == 0
    assert len(result.steps) == 1  # second step never ran


@pytest.mark.asyncio
async def test_optional_failure_continues_pipeline():
    reg = _MockRegistry(
        {
            "fail": _MockResult(
                name="fail", success=False, output=None, error="skip me"
            ),
            "after": "continued",
        }
    )

    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("fail", required=False))
    pipe.add(PipelineStep("after"))

    result = await pipe.run()
    assert result.succeeded
    assert result.final_output == "continued"
    assert len(result.steps) == 2


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_applied_before_injection():
    def double(v):
        return v * 2

    def use_n(args):
        return args.get("n", 0) + 1

    reg = _MockRegistry(
        {
            "produce": 5,
            "consume": use_n,
        }
    )

    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("produce", output_key="n"))
    pipe.add(PipelineStep("consume", input_key="n", transform=double))

    result = await pipe.run()
    # transform(5) = 10; consume(n=10) → 10+1 = 11
    assert result.final_output == 11


# ---------------------------------------------------------------------------
# on_step callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_step_callback_fires():
    reg = _MockRegistry({"a": 1, "b": 2})
    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("a"))
    pipe.add(PipelineStep("b"))

    seen = []
    result = await pipe.run(on_step=lambda sr: seen.append(sr.tool_name))
    assert seen == ["a", "b"]


# ---------------------------------------------------------------------------
# Context / initial_context seeding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_context_available_as_args():
    def use_ctx(args):
        return args.get("seed", "missing")

    reg = _MockRegistry({"ctx_tool": use_ctx})
    pipe = ToolPipeline(reg, initial_context={"seed": "planted"})
    pipe.add(PipelineStep("ctx_tool"))
    result = await pipe.run()
    assert result.final_output == "planted"


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_reports_failure():
    reg = _MockRegistry({})
    pipe = ToolPipeline(reg)
    pipe.add(PipelineStep("ghost", required=True))
    result = await pipe.run()
    assert not result.succeeded
