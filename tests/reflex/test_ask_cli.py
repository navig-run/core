"""``navig ask`` additive routing surface: --plan / --commit / --profile / --json.

The pinned ``core/tests/cli/test_cli_ask_alias.py`` contract is untouched: the
legacy "Intent route" line (unmatched previews only), the "Plan preview only" gate and ``--plan --execute``
LLM path all still behave. These tests add the router surface on top, with the
ReflexRouter replaced by an injected one and ``ask_ai`` replaced by a spy.
"""

from __future__ import annotations

import json

import pytest


def _invoke(args: list[str], capsys) -> tuple[int, str, str]:
    from navig.cli import app

    exit_code = 0
    try:
        result = app(args, standalone_mode=False)
        if isinstance(result, int) and result != 0:
            exit_code = result
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.fixture()
def install_router(monkeypatch, router):
    """Make ``navig.cli._reflex_router()`` return the injected fake-backed router."""
    import navig.reflex.service as service

    monkeypatch.setattr(service, "ReflexRouter", lambda: router)
    return router


@pytest.fixture()
def ask_ai_spy(monkeypatch):
    calls: list[dict] = []

    def _fake_ask_ai(question, model, options):
        calls.append({"question": question, "model": model, "options": options})

    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", _fake_ask_ai)
    return calls


def test_plan_prints_tool_target_blast_and_token_without_executing(
    install_router, runner, capsys
) -> None:
    code, out, err = _invoke(["ask", "turn off the desk lamp", "--plan"], capsys)
    combined = out + err
    assert code == 0
    assert "intent route" not in combined.lower()
    assert "home.off" in combined
    assert "govee:desk-lamp" in combined
    assert "commit token" in combined.lower()
    assert "plan-" in combined
    assert "plan preview only" in combined.lower()
    assert runner.calls == []


def test_plan_json_is_one_pure_document_with_the_plan(
    install_router, runner, capsys
) -> None:
    code, out, err = _invoke(["ask", "turn off the desk lamp", "--plan", "--json"], capsys)
    assert code == 0
    # --json owns stdout: exactly one document, human narration on stderr only.
    assert "intent route" not in (out + err).lower()
    payload = json.loads(out)
    assert payload["schema_version"] == "1"
    assert payload["data"]["executed"] is False
    assert payload["plan"]["tool"] == "home.off"
    assert payload["plan"]["commit_token"].startswith("plan-")
    assert payload["plan"]["blast_radius"]["kind"] == "single"
    assert payload["plan"]["command"] == ["navig", "home", "off", "desk-lamp"]
    assert runner.calls == []


def test_commit_executes_once_and_a_replay_returns_the_same_payload(
    install_router, runner, capsys
) -> None:
    _code, out, _err = _invoke(["ask", "turn off the desk lamp", "--plan", "--json"], capsys)
    token = json.loads(out)["plan"]["commit_token"]

    code, first, _err = _invoke(
        ["ask", "turn off the desk lamp", "--commit", token, "--json"], capsys
    )
    assert code == 0
    assert json.loads(first)["data"]["executed"] is True
    assert len(runner.calls) == 1

    code, replay, _err = _invoke(
        ["ask", "turn off the desk lamp", "--commit", token, "--json"], capsys
    )
    assert code == 0
    assert json.loads(replay) == json.loads(first)
    assert len(runner.calls) == 1


def test_unknown_token_exits_2_with_the_next_command(install_router, capsys) -> None:
    code, out, err = _invoke(["ask", "anything", "--commit", "plan-deadbeef"], capsys)
    assert code == 2
    combined = out + err
    assert "unknown commit token" in combined
    assert "--plan" in combined


def test_token_only_commit_is_accepted_and_replays(install_router, runner, capsys) -> None:
    """The plugins' documented contract: `navig ask --commit <token> --json` — no question.

    Voice RouterClient, Alexa `Backend.route` and Alice `Backend.route` all drive commit
    that way; a required QUESTION made them fall back to speaking the token.
    """
    _code, plan_out, _err = _invoke(
        ["ask", "turn off the desk lamp", "--plan", "--json"], capsys
    )
    token = json.loads(plan_out)["plan"]["commit_token"]

    code, stdout, _err = _invoke(["ask", "--commit", token, "--json"], capsys)
    assert code == 0, stdout
    payload = json.loads(stdout)  # ONE pure JSON document, no narration on stdout
    assert payload["data"]["executed"] is True
    assert payload["ok"] is True
    assert len(runner.calls) == 1

    code, replay, _err = _invoke(["ask", "--commit", token, "--json"], capsys)
    assert code == 0
    assert json.loads(replay) == payload
    assert len(runner.calls) == 1, "a token-only replay must not run the command again"


def test_token_only_commit_without_json_still_commits(install_router, runner, capsys) -> None:
    _code, plan_out, _err = _invoke(
        ["ask", "turn off the desk lamp", "--plan", "--json"], capsys
    )
    token = json.loads(plan_out)["plan"]["commit_token"]
    code, out, err = _invoke(["ask", "--commit", token], capsys)
    assert code == 0
    assert "off" in (out + err).lower()
    assert len(runner.calls) == 1


def test_ask_without_a_question_or_commit_keeps_the_missing_argument_error(
    install_router, capsys
) -> None:
    """The utterance is optional ONLY for a token commit; otherwise the old error stands."""
    from navig.core.click_compat import click_submodule

    exceptions = click_submodule("exceptions")
    with pytest.raises(exceptions.UsageError) as info:
        _invoke(["ask"], capsys)
    assert info.type is exceptions.MissingParameter
    assert info.value.format_message() == "Missing argument 'QUESTION'."


def test_ask_commit_token_without_question_never_calls_the_assistant(
    install_router, ask_ai_spy, capsys
) -> None:
    _code, plan_out, _err = _invoke(
        ["ask", "turn off the desk lamp", "--plan", "--json"], capsys
    )
    token = json.loads(plan_out)["plan"]["commit_token"]
    code, _stdout, _err = _invoke(["ask", "--commit", token, "--json"], capsys)
    assert code == 0
    assert ask_ai_spy == []


def test_commit_and_plan_are_mutually_exclusive(install_router, capsys) -> None:
    code, out, err = _invoke(["ask", "x", "--commit", "plan-x", "--plan"], capsys)
    assert code == 2
    assert "mutually exclusive" in (out + err)


def test_commit_replays_an_expired_plan_with_exit_2(install_router, clock, capsys) -> None:
    from navig.reflex.plans import PLAN_TTL_SECONDS

    _code, out, _err = _invoke(["ask", "turn off the desk lamp", "--plan", "--json"], capsys)
    token = json.loads(out)["plan"]["commit_token"]
    clock.advance(PLAN_TTL_SECONDS + 10)
    code, out, err = _invoke(["ask", "x", "--commit", token], capsys)
    assert code == 2
    combined = out + err
    assert "expired" in combined.lower()
    assert "run --plan again" in combined


def test_plan_execute_runs_the_planned_action_without_the_assistant(
    install_router, runner, ask_ai_spy, capsys
) -> None:
    code, out, err = _invoke(
        ["ask", "turn off the desk lamp", "--plan", "--execute"], capsys
    )
    assert code == 0
    assert len(runner.calls) == 1
    assert ask_ai_spy == []
    assert "intent route" not in (out + err).lower()


def test_unmatched_utterance_falls_through_to_the_assistant(
    install_router, runner, ask_ai_spy, capsys
) -> None:
    code, out, err = _invoke(["ask", "restart nginx on prod"], capsys)
    assert code == 0
    combined = out + err
    assert "no registered action matched" in combined.lower()
    assert [call["question"] for call in ask_ai_spy] == ["restart nginx on prod"]
    assert runner.calls == []


def test_plan_on_an_unrecognised_utterance_keeps_the_pinned_preview(
    install_router, ask_ai_spy, capsys
) -> None:
    code, out, err = _invoke(["ask", "restart nginx on prod", "--plan"], capsys)
    combined = out + err
    assert code == 0
    assert "intent route" in combined.lower()
    assert "plan preview only" in combined.lower()
    assert ask_ai_spy == []


def test_unmatched_json_emits_one_document_and_calls_no_assistant(
    install_router, ask_ai_spy, capsys
) -> None:
    code, out, _err = _invoke(["ask", "restart nginx on prod", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["data"]["matched"] is False
    assert ask_ai_spy == []


def test_unknown_profile_exits_2(install_router, capsys) -> None:
    code, out, err = _invoke(["ask", "turn off the desk lamp", "--profile", "turbo"], capsys)
    assert code == 2
    assert "unknown profile" in (out + err)


def test_safe_profile_plans_instead_of_committing(install_router, runner, capsys) -> None:
    code, out, err = _invoke(
        ["ask", "turn off the desk lamp", "--profile", "safe"], capsys
    )
    assert code == 0
    combined = out + err
    assert "commit token" in combined.lower()
    assert runner.calls == []


def test_commit_failure_exits_1(make_router, make_runner, monkeypatch, capsys) -> None:
    failing = make_runner(fail=("home off",))
    router = make_router(runner=failing)
    import navig.reflex.service as service

    monkeypatch.setattr(service, "ReflexRouter", lambda: router)
    code, out, err = _invoke(["ask", "turn off the desk lamp"], capsys)
    assert code == 1
    assert "failed" in (out + err).lower()
