"""The system prompt must be byte-identical for the life of a session.

This is a regression guard, not a style preference. The Anthropic cache
breakpoint sits on the *system* message (``agent/prompt_caching.py``
``_apply_system_and_3``) and the cached prefix spans ``tools → system``, so one
mutating byte in the system block discards the ~45 registered tool schemas too.
At the repo's own price table (``usage_tracker.PRICE_TABLE``) that is
``cache_write`` 1.25x against ``cache_read`` 0.1x — a 12.5x swing on the largest
part of every request.

``## Session Context`` used to open with ``System time: %H:%M %Z``, which capped
the cache lifetime at about sixty seconds, and carried a one-shot "Fresh session"
line that guaranteed a miss on turn 2. Both now ride the user turn.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from navig.agent.conv.soul import SoulContext, SoulLoader


@pytest.fixture
def loader():
    return SoulLoader()


@pytest.fixture
def ctx():
    return SoulContext(
        condensed="I am a test identity.",
        source="workspace",
        revision="deadbeef",
        guardrails="## Operating Rules\n1. Radical truth.",
    )


def _build(loader: SoulLoader, ctx: SoulContext, awareness: str = "") -> str:
    return loader.build_system_prompt(
        soul=ctx.condensed,
        lang_instruction="Reply in English.",
        awareness=awareness,
        capabilities="- Run commands",
        guardrails=ctx.guardrails,
    )


class TestByteStability:
    def test_repeated_builds_are_identical(self, loader, ctx):
        builds = [_build(loader, ctx) for _ in range(5)]
        assert len(set(builds)) == 1, "system prompt is not deterministic"

    def test_stable_across_simulated_clock_advance(self, loader, ctx, monkeypatch):
        """A 90-minute jump must not change a single byte.

        Rounding the clock to a coarser unit was the tempting fix; it only makes
        invalidation rare and unpredictable — a session straddling :59 → :00 still
        eats a full prefix rewrite, and "mostly stable" cannot be asserted.
        """
        import navig.agent.conv.agent as agentmod

        base = datetime(2026, 8, 5, 10, 30).astimezone()
        first = _build(loader, ctx)

        class _FrozenClock(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D102
                return base + timedelta(minutes=90)

        monkeypatch.setattr(agentmod, "datetime", _FrozenClock)
        assert _build(loader, ctx) == first

    def test_concurrent_builds_are_identical(self, loader, ctx):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _build(loader, ctx), range(20)))
        assert len(set(results)) == 1

    def test_prefix_hash_is_reproducible(self, loader, ctx):
        a = hashlib.sha256(_build(loader, ctx).encode()).hexdigest()
        b = hashlib.sha256(_build(loader, ctx).encode()).hexdigest()
        assert a == b


class TestVolatileContentIsNotInSystem:
    """The clock and the freshness one-shot belong to the user turn."""

    @pytest.fixture
    def agent(self):
        from navig.agent.conv.agent import ConversationalAgent

        return ConversationalAgent(soul_content="Test soul.")

    def test_system_prompt_has_no_clock(self, agent):
        prompt = agent._build_system_prompt("tell me about the deploy")
        assert "System time" not in prompt

    def test_turn_context_has_the_clock(self, agent):
        assert "System time" in agent._build_turn_context()

    def test_session_context_has_no_clock(self, agent):
        agent.set_user_identity(user_id="1", username="ops")
        session = agent._build_session_context()
        assert "System time" not in session
        assert "ops" in session

    def test_freshness_line_is_not_in_system_prompt(self, agent):
        """It is a one-shot WITH a side effect (mark_freshness_consumed).

        Memoising a system prompt that contains it would freeze "fresh session"
        on for the life of the process.
        """
        assert "Fresh session" not in agent._build_system_prompt("hello there friend")

    def test_two_system_prompt_builds_match(self, agent):
        msg = "walk me through the migration plan"
        assert agent._build_system_prompt(msg) == agent._build_system_prompt(msg)


class TestVolatileContentRidesTheUserTurn:
    """Source-level guards for the wiring, not just the builders.

    The builder tests above prove the assembled prompt is stable *given* stable
    inputs. These prove the turn actually feeds it stable inputs — the mistake
    that shipped was not a bad builder, it was the clock being handed to one.
    """

    @pytest.fixture(scope="class")
    def agent_ast(self):
        import ast
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2] / "navig" / "agent" / "conv" / "agent.py"
        )
        return ast.parse(path.read_text(encoding="utf-8"))

    def _fn(self, tree, name: str):
        import ast

        return next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        )

    def test_system_prompt_builder_never_calls_the_volatile_half(self, agent_ast):
        import ast

        fn = self._fn(agent_ast, "_build_system_prompt")
        calls = [
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "_build_turn_context" not in calls, (
            "_build_system_prompt pulls in the volatile block — that is exactly the "
            "60-second cache ceiling this split removed"
        )
        assert "_build_session_context" in calls, "the stable half must still be used"

    def test_run_agentic_prepends_the_clock_to_the_user_turn(self, agent_ast):
        import ast

        fn = self._fn(agent_ast, "run_agentic")
        calls = [
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "_build_turn_context" in calls

    def test_now_header_is_used_for_the_turn_block(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2] / "navig" / "agent" / "conv" / "agent.py"
        ).read_text(encoding="utf-8")
        assert "## Now" in src


class TestSectionOrder:
    def test_guardrails_precede_identity(self, loader, ctx):
        prompt = _build(loader, ctx)
        assert prompt.index("## Operating Rules") < prompt.index("## Who You Are")

    def test_guardrails_are_first(self, loader, ctx):
        assert _build(loader, ctx).startswith("## Operating Rules")

    def test_session_context_is_last(self, loader, ctx):
        prompt = _build(loader, ctx, awareness="You are talking to ops.")
        assert prompt.index("## Session Context") > prompt.index("## How to Talk")

    def test_sections_helper_matches_joined_prompt(self, loader, ctx):
        sections = loader.system_prompt_sections(
            ctx.condensed,
            lang_instruction="Reply in English.",
            awareness="",
            capabilities="- Run commands",
            guardrails=ctx.guardrails,
        )
        assert "\n\n".join(b for _h, b in sections) == _build(loader, ctx)
