"""The decision model's shadow hooks are still wired where each comparison must happen.

`navig decider` compares Clef with logic that lives in Telegram, the router, the verifier and
the executor. A refactor that drops one of those one-line hooks breaks nothing visible, but the
comparison silently stops: the agreement rate goes stale, and a use could be promoted to live on
evidence that stopped being collected. These are AST checks over those files, so they run
on every change (sourceGuardArgs). Editing any of the hooked files cannot be detected by module
name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "navig"


# ── wiring: each hook sits where its comparison has to happen ──


def _calls_in(path: Path, func: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            out = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name):
                    out.add(f"{sub.func.value.id}.{sub.func.attr}")
            return out
    raise AssertionError(f"{func} not found in {path}")


@pytest.mark.parametrize(
    ("rel", "func", "call"),
    [
        ("gateway/channels/telegram.py", "_dispatch_by_mode", "message_brief.note"),
        ("gateway/channels/telegram_commands.py", "_handle_natural_language_request", "message_brief.note"),
        ("gateway/channels/telegram_commands.py", "_handle_nl_pending_reply", "judges.confirm_reply"),
        ("gateway/channel_router.py", "_handle_message", "judges.confirm_reply"),
        ("gateway/channels/telegram.py", "_maybe_auto_continue", "judges.continuation_state"),
        ("llm/routing/router.py", "run", "judges.reply_quality"),
        ("agent/verifier.py", "_ask", "judges.action_safety"),
        ("agent/conv/executor.py", "execute", "judges.goal_achieved"),
    ],
)
def test_hook_is_wired(rel, func, call):
    assert call in _calls_in(CORE / rel, func), f"{call} is no longer called from {rel}::{func}"


def test_the_brief_is_started_on_the_message_path():
    src = (CORE / "gateway/channels/telegram.py").read_text(encoding="utf-8")
    assert "message_brief.start(chat_id, message_id, text)" in src
