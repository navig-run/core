"""Phase 5 (Mission B): workspace instructions in the ReAct system prompt.

Pins the four contract points: which files and how many (≤3, precedence),
the 10 KB cap with an honest marker, injection AFTER soul/guardrails on the
full path only, and session-stability (the block must not churn the cache).
"""

from __future__ import annotations

from pathlib import Path

import navig.agent.conv.agent as conv_mod
from navig.agent.conv.agent import workspace_instructions_block


class TestHelper:
    def test_empty_workspace_returns_empty(self, tmp_path):
        assert workspace_instructions_block(tmp_path) == ""

    def test_agents_md_inlined_with_wrapper(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            "# Workspace rules\nAlways answer in tables.", encoding="utf-8"
        )
        block = workspace_instructions_block(tmp_path)
        assert '<workspace_file path="AGENTS.md">' in block
        assert "Always answer in tables." in block
        assert f'directory="{tmp_path.resolve()}"' in block
        assert block.startswith("\n\n<workspace_instructions")

    def test_precedence_and_file_cap(self, tmp_path):
        for name in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "NAVIG.md"):
            path = tmp_path / name
            path.parent.mkdir(exist_ok=True, parents=True) if name.startswith(".") else None
            path.write_text(f"content of {name}", encoding="utf-8")
        block = workspace_instructions_block(tmp_path)
        assert 'path="AGENTS.md"' in block
        assert 'path="CLAUDE.md"' in block
        assert 'path=".claude/CLAUDE.md"' in block
        assert 'path="NAVIG.md"' not in block, "4th file must fall past the ≤3 cap"

    def test_size_cap_truncates_honestly(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            "x" * (conv_mod._WORKSPACE_INSTRUCTION_MAX_BYTES + 500), encoding="utf-8"
        )
        block = workspace_instructions_block(tmp_path)
        assert "[truncated at 10,000 bytes]" in block
        # cap + marker + wrapper — never the whole 10.5 KB body
        assert len(block) < conv_mod._WORKSPACE_INSTRUCTION_MAX_BYTES + 600

    def test_never_raises_on_unreadable(self, tmp_path, monkeypatch):
        (tmp_path / "AGENTS.md").write_text("hi", encoding="utf-8")

        def _boom(*a, **k):
            raise OSError("denied")

        monkeypatch.setattr(Path, "read_bytes", _boom)
        assert workspace_instructions_block(tmp_path) == ""

    def test_empty_file_skipped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("   \n", encoding="utf-8")
        assert workspace_instructions_block(tmp_path) == ""


class TestPromptIntegration:
    def _agent(self):
        from navig.agent.conv import ConversationalAgent

        return ConversationalAgent(ai_client=None, soul_content="You are NAVIG-test.")

    def test_injected_after_soul_on_full_prompt(self, tmp_path, monkeypatch):
        (tmp_path / "AGENTS.md").write_text("WORKSPACE-RULE-42", encoding="utf-8")
        monkeypatch.setattr(
            conv_mod,
            "workspace_instructions_block",
            lambda workspace=None: (
                f'\n\n<workspace_instructions directory="{tmp_path}">'
                f'\n<workspace_file path="AGENTS.md">\nWORKSPACE-RULE-42\n</workspace_file>'
                "\n</workspace_instructions>"
            ),
        )
        agent = self._agent()
        prompt = agent._build_system_prompt("do the thing with words enough to be full")
        assert "WORKSPACE-RULE-42" in prompt
        # Trust order: soul content precedes the workspace block.
        assert prompt.index("You are NAVIG-test.") < prompt.index(
            "<workspace_instructions"
        )

    def test_minimal_prompt_skips_the_block(self, tmp_path, monkeypatch):
        calls: list[int] = []

        def _spy(workspace=None):
            calls.append(1)
            return "\n\n<workspace_instructions>SHOULD-NOT-APPEAR</workspace_instructions>"

        monkeypatch.setattr(conv_mod, "workspace_instructions_block", _spy)
        agent = self._agent()
        prompt = agent._build_system_prompt("hey", minimal=True)
        assert "SHOULD-NOT-APPEAR" not in prompt
        assert calls == [], "minimal path must not even build the block"

    def test_session_stable_across_calls(self, tmp_path, monkeypatch):
        (tmp_path / "CLAUDE.md").write_text("stable rule", encoding="utf-8")
        monkeypatch.setattr(
            "navig.spaces.active.get_active_working_dir", lambda *a, **k: tmp_path
        )
        first = workspace_instructions_block()
        second = workspace_instructions_block()
        assert first == second
        assert "stable rule" in first
