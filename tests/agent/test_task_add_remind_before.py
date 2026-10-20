"""The agent can save a task WITH reminders, and is offered the tool when it matters.

Regression: a chat reply said "Rendez-vous noté" while nothing was saved — the chat
agent was never offered the `tasks` toolset, so it had no way to save anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navig.agent.conv import agent as conv_agent
from navig.agent.tools import pim_tools
from navig.pim import reminders
from navig.store.board import BoardStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BoardStore:
    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(pim_tools, "_store", lambda: st)
    monkeypatch.setattr(reminders, "operator_delivery", lambda: None)
    return st


def test_remind_before_is_stored(store: BoardStore) -> None:
    result = asyncio.run(
        pim_tools.TaskAddTool().run(
            {"title": "Dentist oct 26 10:30", "remind_before": ["3d", "1d", "1H"]}
        )
    )
    assert result.success, result.error
    (todo,) = store.list_todos()
    assert list(todo["remind_before"]) == ["3d", "1d", "1h"]


def test_an_unreadable_lead_is_refused_and_nothing_is_saved(store: BoardStore) -> None:
    result = asyncio.run(
        pim_tools.TaskAddTool().run({"title": "Dentist oct 26 10:30", "remind_before": ["soon"]})
    )
    assert not result.success
    assert "soon" in (result.error or "")
    assert store.list_todos() == []


@pytest.mark.parametrize(
    "message",
    [
        "Rappelle-moi 3 jours avant mon rendez-vous chez le dentiste",
        "напомни мне про встречу",
        "remind me about the dentist",
        "Ce rendez-vous a lieu le lundi 26 octobre 2026 à 10h30",
        # Incident 2026-10-13: a word-number lead with no clock time. Caught by
        # the widened verb stems ("добав"); find_when alone would miss it.
        "добавь за три дня, что бы я отменил подписку до этой даты",
    ],
)
def test_reminder_messages_get_the_task_tools(message: str) -> None:
    assert "tasks" in conv_agent.default_turn_toolsets(message)


def test_a_dated_message_without_a_remind_verb_gets_tasks_and_memory(
    monkeypatch,
) -> None:
    """The photo-OCR shape: a date in the context, no remind verb anywhere.
    find_when (clock time required) offered no tools here — the model could
    only ask the user for the date it could already read."""
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda **_: [])
    message = (
        "[Photo attached — OCR]\n"
        "This workspace's Creator Plan is set to $22 per month and will renew "
        "on October 28."
    )
    toolsets = conv_agent.default_turn_toolsets(message)
    assert "tasks" in toolsets
    # A durable fact (plan + renewal date) is being recorded — the turn must
    # also offer memory_write, which chat mode hints never add on their own.
    assert "memory" in toolsets


def test_plain_chat_and_bare_add_do_not(monkeypatch) -> None:
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda **_: [])
    for message in (
        "hello, how are you?",
        "add water to the soup",  # bare EN "add" is deliberately not a stem
    ):
        toolsets = conv_agent.default_turn_toolsets(message)
        assert "tasks" not in toolsets, message
        assert "memory" not in toolsets, message


def test_tasks_guidance_block_is_conditional_and_names_the_tools() -> None:
    block = conv_agent._tasks_guidance_block(["core", "tasks", "memory"])
    assert "task_add" in block
    assert "memory_write" in block
    assert "Photo attached — OCR" in block  # also the prompt-injection fence
    assert conv_agent._tasks_guidance_block(["core"]) == ""
    assert conv_agent._tasks_guidance_block([]) == ""
