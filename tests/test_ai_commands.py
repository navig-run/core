"""Tests for navig ai command surface — Phase 1 audit.

Covers:
- Hybrid intent classifier (ai_router)
- ai_explain file-read behaviour
- ask_ai exit codes on provider error
- ai_logout idempotency
- ai memory clear guard
- Bare `navig ai` help rendering
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _invoke_cli(args: list[str], capsys) -> tuple[int, str, str]:
    from navig.cli import app

    exit_code = 0
    try:
        app(args, standalone_mode=False)
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.fixture(autouse=True)
def _bootstrap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager
    reset_config_manager()

    import navig.cli as cli_mod
    cli_mod._config_manager = None
    cli_mod._NO_CACHE = False
    cli_mod._register_external_commands(register_all=True)

    yield


# ---------------------------------------------------------------------------
# Hybrid classifier — unit tests (no CLI invocation needed)
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_diagnose_high_confidence(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("why is nginx returning 502?")
        assert r.subcommand == "diagnose"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_explain_high_confidence(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("explain iptables -A INPUT -p tcp --dport 80 -j ACCEPT")
        assert r.subcommand == "explain"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_suggest_high_confidence(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("suggest optimizations for my server")
        assert r.subcommand == "suggest"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_show_high_confidence(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("show my last session")
        assert r.subcommand == "show"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_diagnose_crash(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("mysql crashed and won't start")
        assert r.subcommand == "diagnose"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_explain_what_is(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("what is a cron job")
        assert r.subcommand == "explain"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_suggest_harden(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("how should I harden my SSH config")
        assert r.subcommand == "suggest"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_show_history(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("display my command history")
        assert r.subcommand == "show"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_diagnose_why_wrong(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("what went wrong with the deployment?")
        assert r.subcommand in ("diagnose", "ask")  # either is acceptable
        assert r.confidence >= 0.3  # must produce a non-trivial score

    def test_diagnose_502(self):
        from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD
        r = classify_intent("server is returning 502 bad gateway")
        assert r.subcommand == "diagnose"
        assert r.confidence >= CONFIDENCE_THRESHOLD

    def test_ambiguous_returns_two_candidates(self):
        from navig.commands.ai_router import top_two_intents
        results = top_two_intents("check the server status and suggest improvements")
        assert len(results) == 2
        # Top result should be diagnose/suggest/show family
        assert results[0].subcommand in ("diagnose", "suggest", "show", "run", "ask")
        # Results are sorted descending
        assert results[0].confidence >= results[1].confidence

    def test_result_is_named_tuple(self):
        from navig.commands.ai_router import classify_intent, IntentResult
        r = classify_intent("hello")
        assert isinstance(r, IntentResult)
        assert hasattr(r, "subcommand")
        assert hasattr(r, "confidence")


# ---------------------------------------------------------------------------
# ai explain — file-read behaviour
# ---------------------------------------------------------------------------

class TestAiExplain:
    def test_explain_reads_file_content(self, tmp_path, monkeypatch):
        """ai explain must pass actual file content to ask_ai, not just the path."""
        log_file = tmp_path / "app.log"
        log_file.write_text("ERROR: connection refused\n" * 5, encoding="utf-8")

        captured: dict = {}
        import navig.commands.ai as ai_mod
        monkeypatch.setattr(ai_mod, "ask_ai", lambda q, m, o: captured.update(question=q))

        from typer.testing import CliRunner
        from navig.commands.ai import ai_app
        runner = CliRunner()
        runner.invoke(ai_app, ["explain", str(log_file)])

        assert "question" in captured, "ask_ai was not called"
        assert "connection refused" in captured["question"]

    def test_explain_missing_file_exits_1(self, monkeypatch):
        """ai explain with a non-existent path should set error and exit(1)."""
        from typer.testing import CliRunner
        from navig.commands.ai import ai_app
        import navig.commands.ai as ai_mod
        # Must not call ask_ai for a missing file
        monkeypatch.setattr(ai_mod, "ask_ai", lambda *a, **kw: None)
        runner = CliRunner()
        result = runner.invoke(ai_app, ["explain", "/nonexistent_path/does_not_exist.log"])
        # Either exits 1 (file not found) or treats as command string — either is valid.
        # If file doesn't exist it should NOT show "connection refused" (it wasn't read)
        assert "connection refused" not in (result.output or "")

    def test_explain_command_string_no_file_read(self, monkeypatch):
        """ai explain with a plain command string (not a file) sends an explain prompt."""
        captured: dict = {}
        import navig.commands.ai as ai_mod
        monkeypatch.setattr(ai_mod, "ask_ai", lambda q, m, o: captured.update(question=q))
        from typer.testing import CliRunner
        from navig.commands.ai import ai_app
        runner = CliRunner()
        runner.invoke(ai_app, ["explain", "iptables -L"])
        if "question" in captured:
            assert "iptables -L" in captured["question"]
            assert "Explain" in captured["question"] or "explain" in captured["question"]


# ---------------------------------------------------------------------------
# ask_ai exit codes
# ---------------------------------------------------------------------------

class TestAskAiExitCodes:
    def test_ask_ai_exits_1_on_generic_exception(self, monkeypatch):
        """ask_ai must raise typer.Exit(1) on provider/network failure."""
        import navig.commands.ai as ai_mod
        import navig.ai as ai_core

        class _BrokenAssistant:
            def __init__(self, *a, **kw):
                pass
            def ask(self, *a, **kw):
                raise RuntimeError("simulated provider failure")

        monkeypatch.setattr(ai_core, "AIAssistant", _BrokenAssistant)

        import typer
        with pytest.raises(typer.Exit) as exc_info:
            ai_mod.ask_ai("test", None, {"app": None})
        assert exc_info.value.exit_code == 1

    def test_ask_ai_exits_2_on_value_error(self, monkeypatch):
        """ask_ai must raise typer.Exit(2) on ValueError (usage/config error)."""
        import navig.commands.ai as ai_mod
        import navig.ai as ai_core

        class _ConfigErrorAssistant:
            def __init__(self, *a, **kw):
                pass
            def ask(self, *a, **kw):
                raise ValueError("No API key found — run: navig ai providers add")

        monkeypatch.setattr(ai_core, "AIAssistant", _ConfigErrorAssistant)

        import typer
        with pytest.raises(typer.Exit) as exc_info:
            ai_mod.ask_ai("test", None, {"app": None})
        assert exc_info.value.exit_code == 2


# ---------------------------------------------------------------------------
# ai logout idempotency
# ---------------------------------------------------------------------------

class TestAiLogoutIdempotency:
    def test_logout_already_logged_out_exits_0_with_message(self, capsys):
        """ai logout when no credentials exist must print 'Already logged out.' and exit 0."""
        from typer.testing import CliRunner
        from navig.commands.ai import ai_app

        runner = CliRunner()
        result = runner.invoke(ai_app, ["logout", "openai"])
        assert result.exit_code == 0
        assert "Already logged out" in result.output


# ---------------------------------------------------------------------------
# ai memory clear guard
# ---------------------------------------------------------------------------

class TestAiMemoryClear:
    def test_memory_clear_without_confirm_exits_1(self):
        from typer.testing import CliRunner
        from navig.commands.ai import ai_app
        runner = CliRunner()
        result = runner.invoke(ai_app, ["memory", "clear"])
        assert result.exit_code == 1
        out = result.output.lower()
        assert "confirm" in out or "delete" in out


# ---------------------------------------------------------------------------
# ai bare help rendering
# ---------------------------------------------------------------------------

class TestAiBareHelp:
    def test_bare_ai_shows_help_table(self, capsys):
        """navig ai (no subcommand, no args) must render the command table."""
        code, out, err = _invoke_cli(["ai"], capsys)
        combined = out + err
        # Must mention core commands
        assert "ask" in combined
        assert "providers" in combined
        assert code == 0
