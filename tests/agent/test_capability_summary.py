"""
AgentToolRegistry.capability_summary() — the live, grouped summary of what the
agent can actually do, injected into the system prompt so it describes its real
breadth (browser, git, remote ops, memory, …) instead of improvising a narrow
"sysadmin + life-coach" list when asked.
"""

from __future__ import annotations

from navig.agent.agent_tool_registry import AgentToolEntry, AgentToolRegistry


def _reg(*toolsets: str) -> AgentToolRegistry:
    reg = AgentToolRegistry()
    for i, ts in enumerate(toolsets):
        # Register pre-built entries directly — capability_summary only reads
        # `toolset` + `check_fn`, so a minimal schema/tool_ref is fine.
        reg.register_entry(
            AgentToolEntry(
                name=f"tool_{i}",
                schema={"name": f"tool_{i}", "description": "d", "parameters": {}},
                tool_ref=None,  # type: ignore[arg-type] — unused by the summary
                toolset=ts,
            )
        )
    return reg


def test_empty_registry_returns_empty():
    assert AgentToolRegistry().capability_summary() == ""


def test_groups_by_toolset_with_friendly_labels():
    summary = _reg("browser", "git", "remote").capability_summary()
    assert "Browse & operate real websites" in summary   # browser label
    assert "Version control" in summary                   # git label
    assert "servers over SSH" in summary                  # remote label
    # one bullet per toolset (deduped), not per tool
    assert summary.count("\n") == 2                        # 3 lines → 2 newlines


def test_dedupes_multiple_tools_in_one_toolset():
    # two browser tools → a single "Browse …" bullet
    summary = _reg("browser", "browser").capability_summary()
    assert summary.count("Browse & operate real websites") == 1


def test_unknown_toolset_falls_back_to_titlecased_name():
    # A toolset with no friendly label still surfaces (never silently dropped).
    summary = _reg("quantum_ops").capability_summary()
    assert "Quantum Ops" in summary


def test_toolset_filter_scopes_the_summary():
    reg = _reg("browser", "git")
    only_browser = reg.capability_summary(toolsets=["browser"])
    assert "Browse & operate real websites" in only_browser
    assert "Version control" not in only_browser


def test_compact_summary_is_one_terse_comma_line():
    reg = _reg("browser", "git", "memory")
    compact = reg.capability_summary(compact=True)
    assert "\n" not in compact                       # single line
    assert "browse & operate websites" in compact    # short label, not the verbose one
    assert "git & code review" in compact
    assert "long-term memory" in compact
    assert compact.count(",") == 2                    # 3 domains → 2 separators
    # markedly shorter than the verbose form
    assert len(compact) < len(reg.capability_summary())


def test_compact_unknown_toolset_uses_lower_name():
    compact = _reg("quantum_ops").capability_summary(compact=True)
    assert "quantum ops" in compact


# ── Capability/identity questions must reach the FULL prompt ─────────


class TestCapabilityQuestionRouting:
    def test_regex_matches_identity_and_capability_questions(self):
        from navig.agent.conv.agent import _CAPABILITY_QUESTION_RE

        for q in [
            "who are you", "What can you do?", "what do you do",
            "tell me about yourself", "introduce yourself", "your capabilities",
            "what are you capable of", "what kind of tasks can you handle",
            "what can you help with",
        ]:
            assert _CAPABILITY_QUESTION_RE.search(q), q

    def test_regex_ignores_ordinary_task_requests(self):
        from navig.agent.conv.agent import _CAPABILITY_QUESTION_RE

        for q in [
            "deploy the app", "can you help me fix this bug", "who won the game",
            "what time is it", "restart the server", "summarize this file",
        ]:
            assert not _CAPABILITY_QUESTION_RE.search(q), q

    def test_english_capability_question_gets_full_prompt(self):
        from navig.agent.conv.agent import ConversationalAgent
        from navig.agent.tools import register_all_tools

        register_all_tools()
        agent = ConversationalAgent(ai_client=None, soul_content="You are NAVIG.")

        # A short greeting stays on the slim path (no full ## section header)…
        greeting = agent._build_system_prompt("hey", minimal=True)
        assert "## What You Can Do" not in greeting

        # …but an English capability question is promoted to the FULL prompt with
        # the verbose inventory (richest answer).
        capq = agent._build_system_prompt("what can you do", minimal=True)
        assert "## What You Can Do" in capq
        assert "Browse & operate real websites" in capq

    def test_short_message_any_language_still_knows_breadth(self):
        # Language-agnostic baseline: even a short non-English "what can you do?"
        # (which the English regex can't detect) rides the compact capability line
        # in the minimal prompt — so it never improvises a narrow list.
        from navig.agent.conv.agent import ConversationalAgent
        from navig.agent.tools import register_all_tools

        register_all_tools()
        agent = ConversationalAgent(ai_client=None, soul_content="You are NAVIG.")

        for msg in ["was kannst du", "qué puedes hacer", "hey"]:
            p = agent._build_system_prompt(msg, minimal=True)
            assert "You have real tools" in p              # the compact breadth line
            assert "browse & operate websites" in p        # a real domain (terse form)
            assert "only if asked what you can do" in p     # guarded so greetings stay greetings
