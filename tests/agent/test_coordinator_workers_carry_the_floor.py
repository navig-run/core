"""A coordinator worker executes TOOLS, so it must run through a floored agent path.

`CoordinatorAgent._run_worker_conversation` is the only hop between a formation /
delegation plan and a live tool-using child agent. It runs each worker as
`ConversationalAgent.run_agentic(...)`, whose `_build_system_prompt` resolves the soul
through `SoulLoader.build_prompt` / `_soul_context`, and **both of those emit
`guardrail_block()`**. That is the entire reason a formation worker inherits the safety
floor — the worker's own identity string ("You are {name} ({role}).") arrives as the
USER message, carrying no rules of its own.

Nothing asserted that chain. It is four hops, entirely implicit, and the one existing
test that touches this function **monkeypatches it out**
(`tests/formations/test_formation_coordinator.py` replaces `_run_worker_conversation`
with a fake), so the real body was executed by no test at all. Swapping it for a direct
LLM call — an obvious "make specialists cheaper" refactor, and the docstring even notes
the single-turn alternative it deliberately rejected — would silently drop the floor from
every worker while every test stayed green.

This pins the hop, not the floor: `test_guardrail_floor_every_surface.py` and
`test_guardrail_floor.py` already prove the agent path emits the floor. What was missing
is that the coordinator still *uses* that path.

Related: `navig/formations/coordinator.py` composes the worker's `task_description`. It
was audited alongside #1119 and needs no floor of its own precisely because of this
chain — the identity it writes is a user turn, not a system prompt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[2]
_COORDINATOR = _CORE / "navig" / "agent" / "coordinator.py"

#: Entry points that talk to a model directly, with no soul/guardrail assembly.
_RAW_LLM_CALLS = {
    "llm_generate",
    "chat_completion",
    "create_completion",
    "complete_stream",
    "generate_text",
}


def _worker_executor() -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(_COORDINATOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_run_worker_conversation":
                return node
    pytest.fail(
        f"_run_worker_conversation is gone from {_COORDINATOR.name} — the worker "
        "execution path was restructured and this guard is now checking nothing. "
        "Re-point it at whatever runs a coordinator worker."
    )


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def test_a_worker_runs_through_the_agent_path() -> None:
    """The hop that gives every formation worker its guardrail floor."""
    called = _called_names(_worker_executor())
    assert "run_agentic" in called, (
        "CoordinatorAgent._run_worker_conversation no longer calls run_agentic. Workers "
        "execute TOOLS under the operator's identity, and run_agentic is what assembles "
        "their system prompt through SoulLoader.build_prompt / _soul_context — the only "
        "reason they carry the guardrail floor. Whatever replaced it must emit the floor "
        "itself (see navig/agent/conv/guardrails.py), or every worker now runs with no "
        f"boundaries at all. Calls found: {sorted(n for n in called if n)}"
    )


def test_a_worker_does_not_bypass_it_with_a_raw_llm_call() -> None:
    """`run_agentic` present is not enough if a cheaper path was added beside it."""
    raw = _called_names(_worker_executor()) & _RAW_LLM_CALLS
    assert not raw, (
        f"_run_worker_conversation calls {sorted(raw)} directly. A raw model call skips "
        "the soul/guardrail assembly, so a worker taking that branch acts on the "
        "operator's behalf with no floor. Route it through run_agentic, or prefix the "
        "prompt with guardrail_floor_minimal()."
    )


def test_the_guard_reads_the_real_function() -> None:
    """Anti-vacuity: an empty parse would make both checks above pass forever."""
    fn = _worker_executor()
    assert isinstance(fn, ast.AsyncFunctionDef), (
        "_run_worker_conversation is no longer async — it awaits a child agent, so this "
        "likely means the body was replaced; re-read it before trusting this guard."
    )
    called = _called_names(fn)
    assert len(called) >= 3, (
        f"only {len(called)} calls found in _run_worker_conversation — the AST scan is "
        "reading the wrong node, so these assertions prove nothing."
    )
