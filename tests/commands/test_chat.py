"""``navig chat`` — it never once reached the model.

``run_ai_chat`` called ``asyncio.run(ai.chat(text, conversation))``. Three things are
wrong with that line and all of them are invisible until it executes:

  * ``AIAssistant`` has no ``chat`` — the method is ``ask``;
  * ``ask`` is **synchronous**, so ``asyncio.run`` would reject it anyway;
  * ``ask`` takes a *context dict* (client OS, active server), not a list of messages.

The whole body sits under ``except Exception as e: ch.error(f"AI chat error: {e}")``, so
every invocation printed ``AI chat error: 'AIAssistant' object has no attribute 'chat'``
and exited 0. The module had no tests, which is why a completely dead command survived.

The assistant is patched with ``create_autospec``: a plain MagicMock would answer to
``.chat()`` just as happily as ``.ask()`` and keep this bug green.
"""

from __future__ import annotations

import builtins
from unittest.mock import create_autospec, patch

import pytest
import typer

from navig.ai import AIAssistant
from navig.commands.chat import _chat_context, run_ai_chat


@pytest.fixture
def assistant():
    fake = create_autospec(AIAssistant, instance=True)
    fake.ask.return_value = "42% of your disk is free."
    with patch("navig.ai.AIAssistant", return_value=fake):
        yield fake


def test_single_query_reaches_the_model_and_prints_the_answer(assistant, capsys) -> None:
    run_ai_chat("how full is my disk?", single_query=True)

    assistant.ask.assert_called_once()
    assert "42% of your disk is free." in capsys.readouterr().out


def test_single_query_passes_the_question_and_a_context_dict(assistant) -> None:
    """ask(question, context) — the second argument was a message list before."""
    run_ai_chat("hello", single_query=True)

    question, context = assistant.ask.call_args[0]
    assert question == "hello"
    assert isinstance(context, dict)


def test_no_error_banner_is_printed_on_the_happy_path(assistant, capsys) -> None:
    """The old failure mode was a printed 'AI chat error: …', never an exception."""
    run_ai_chat("hello", single_query=True)

    assert "AI chat error" not in capsys.readouterr().out


def test_interactive_session_asks_once_per_line_then_exits(assistant, monkeypatch, capsys) -> None:
    replies = iter(["what is up?", "exit"])
    monkeypatch.setattr(builtins, "input", lambda *_a: next(replies))

    run_ai_chat()

    assert assistant.ask.call_count == 1
    assert "Goodbye" in capsys.readouterr().out


def test_interactive_session_skips_blank_input(assistant, monkeypatch) -> None:
    replies = iter(["", "   ", "quit"])
    monkeypatch.setattr(builtins, "input", lambda *_a: next(replies))

    run_ai_chat()

    assistant.ask.assert_not_called()


def test_an_initial_query_is_answered_before_the_prompt_loop(assistant, monkeypatch, capsys) -> None:
    monkeypatch.setattr(builtins, "input", lambda *_a: "exit")

    run_ai_chat("opening question")

    assert assistant.ask.call_args[0][0] == "opening question"
    assert "42% of your disk is free." in capsys.readouterr().out


def test_ctrl_c_leaves_the_session_cleanly(assistant, monkeypatch, capsys) -> None:
    def _interrupt(*_a):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", _interrupt)

    run_ai_chat()  # must not propagate

    assert "Goodbye" in capsys.readouterr().out


def test_eof_ends_the_session(assistant, monkeypatch) -> None:
    def _eof(*_a):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _eof)

    run_ai_chat()  # must not propagate


def test_a_model_failure_is_reported_not_swallowed(assistant, capsys) -> None:
    """Reported on screen AND in the exit code.

    `navig ask "…" && <next>` used to run the next step after the model never
    answered: the error was printed and the command still exited 0.
    """
    assistant.ask.side_effect = RuntimeError("no provider configured")

    with pytest.raises(typer.Exit) as excinfo:
        run_ai_chat("hi", single_query=True)

    assert excinfo.value.exit_code == 1
    assert "no provider configured" in capsys.readouterr().out


def test_context_carries_the_client_platform() -> None:
    """Without this the model answers a Windows operator with bash commands."""
    context = _chat_context()

    assert context["client_os"]
    assert context["directory"]


def test_assistant_still_exposes_ask_and_still_lacks_chat() -> None:
    """Pin the premise so the fix cannot quietly rot back."""
    assert hasattr(AIAssistant, "ask")
    assert not hasattr(AIAssistant, "chat")
