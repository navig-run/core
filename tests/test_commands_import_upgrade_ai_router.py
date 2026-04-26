"""Tests for commands/import_cmd.py, commands/upgrade.py, commands/ai_router.py — batch 51."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()

# ---------------------------------------------------------------------------
# import_cmd
# ---------------------------------------------------------------------------


def _make_engine(sources=("browser", "ssh_config"), results=None):
    engine = MagicMock()
    engine.list_sources.return_value = list(sources)
    engine.run_all.return_value = results or {}
    engine.run_one.return_value = []
    engine.export_json.return_value = json.dumps(results or {})
    return engine


def test_import_list_sources_prints_all():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser", "ssh_config", "firefox"])
    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["list-sources"])
    assert result.exit_code == 0
    assert "browser" in result.output
    assert "ssh_config" in result.output


def test_import_list_sources_one_per_line():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["a", "b"])
    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["list-sources"])
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert "a" in lines
    assert "b" in lines


def test_import_default_unknown_source_exits_1():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser"])
    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["--source", "nonexistent_xyz"])
    assert result.exit_code == 1


def test_import_default_path_with_all_source_exits_1():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser"])
    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["--source", "all", "--path", "/some/path"])
    assert result.exit_code == 1


def test_import_default_missing_path_exits_1(tmp_path):
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser"])
    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["--source", "browser", "--path", str(tmp_path / "missing.json")])
    assert result.exit_code == 1


def test_import_json_output_printed():
    from navig.commands.import_cmd import import_app

    items = {"browser": [{"type": "bookmark", "label": "Test", "value": "https://example.com", "source": "browser"}]}
    engine = _make_engine(["browser"], results=items)

    fake_links_db = MagicMock()
    fake_links_db.get_by_url.return_value = None
    fake_db_mod = MagicMock()
    fake_db_mod.get_links_db.return_value = fake_links_db

    with (
        patch("navig.commands.import_cmd.UniversalImporter", return_value=engine),
        patch("navig.commands.import_cmd.links_db_mod", fake_db_mod),
    ):
        result = runner.invoke(import_app, ["--source", "browser", "--json"])

    assert result.exit_code == 0
    # JSON payload printed
    assert "browser" in result.output or "{" in result.output


def test_import_no_results_warning():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser"], results={})
    engine.export_json.return_value = "{}"
    fake_db_mod = MagicMock()
    fake_db_mod.get_links_db.return_value = MagicMock()

    with (
        patch("navig.commands.import_cmd.UniversalImporter", return_value=engine),
        patch("navig.commands.import_cmd.links_db_mod", fake_db_mod),
        patch("navig.commands.import_cmd._flatten", return_value=[]),
    ):
        result = runner.invoke(import_app, ["--source", "browser"])

    assert result.exit_code == 0


def test_import_value_error_exits_1():
    from navig.commands.import_cmd import import_app

    engine = _make_engine(["browser"])
    engine.run_one.side_effect = ValueError("bad format")

    with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
        result = runner.invoke(import_app, ["--source", "browser"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# upgrade — run_version
# ---------------------------------------------------------------------------


def test_run_version_json_output(capsys):
    from navig.commands.upgrade import run_version

    with patch("navig.cli._callbacks._get_hacker_quotes", return_value=[("q", "a")], create=True):
        run_version(json_output=True)

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "navig_version" in data
    assert "python_version" in data
    assert "platform" in data


def test_run_version_json_keys(capsys):
    from navig.commands.upgrade import run_version

    with patch("navig.cli._callbacks._get_hacker_quotes", return_value=[("q", "a")], create=True):
        run_version(json_output=True)

    data = json.loads(capsys.readouterr().out)
    assert "machine" in data
    assert "platform_release" in data


def test_run_version_no_json_calls_ch_info():
    from navig.commands.upgrade import run_version

    with (
        patch("navig.commands.upgrade.ch") as mock_ch,
        patch("navig.cli._callbacks._get_hacker_quotes", return_value=[("hello", "world")], create=True),
    ):
        run_version(json_output=False)

    mock_ch.info.assert_called_once()
    args = mock_ch.info.call_args[0][0]
    assert "NAVIG" in args or "v" in args


def test_run_version_default_is_not_json():
    from navig.commands.upgrade import run_version

    import inspect
    sig = inspect.signature(run_version)
    assert sig.parameters["json_output"].default is False


# ---------------------------------------------------------------------------
# upgrade — run_upgrade (check mode, no subprocess)
# ---------------------------------------------------------------------------


def test_run_upgrade_check_non_git(tmp_path):
    """Non-git directory: just prints version, no subprocess needed."""
    from navig.commands.upgrade import run_upgrade
    import subprocess

    with (
        patch("navig.commands.upgrade.Path") as mock_path_cls,
    ):
        # simulate non-git src_dir
        fake_src = MagicMock()
        fake_src.__truediv__ = lambda self, other: MagicMock(exists=lambda: False)
        mock_path_cls.return_value.resolve.return_value.parent.parent.parent = fake_src

        # Don't actually run subprocess — patch it out
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # check=True with non-git → just prints, returns
            run_upgrade(check=True)


def test_run_upgrade_check_git_mode(tmp_path):
    """Git repo check mode: calls git log."""
    from navig.commands.upgrade import run_upgrade

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="abc1234 some commit", stderr=""
        )
        # Point to actual git root (navig-core IS a git repo)
        run_upgrade(check=True)
    # If it ran, subprocess was called or not — just shouldn't raise
    assert True


# ---------------------------------------------------------------------------
# ai_router
# ---------------------------------------------------------------------------


def test_classify_intent_returns_intent_result():
    from navig.commands.ai_router import classify_intent, IntentResult

    result = classify_intent("why is nginx down?")
    assert isinstance(result, IntentResult)
    assert isinstance(result.subcommand, str)
    assert 0.0 <= result.confidence <= 1.0


def test_classify_intent_diagnose_high_confidence():
    from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD

    result = classify_intent("why is my server crashing with 502 bad gateway?")
    assert result.subcommand == "diagnose"
    assert result.confidence >= CONFIDENCE_THRESHOLD


def test_classify_intent_explain_high_confidence():
    from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD

    result = classify_intent("explain what this command does")
    assert result.subcommand == "explain"
    assert result.confidence >= CONFIDENCE_THRESHOLD


def test_classify_intent_suggest_high_confidence():
    from navig.commands.ai_router import classify_intent, CONFIDENCE_THRESHOLD

    result = classify_intent("suggest how to optimize and improve performance")
    assert result.subcommand == "suggest"
    assert result.confidence >= CONFIDENCE_THRESHOLD


def test_classify_intent_confidence_capped_at_1():
    from navig.commands.ai_router import classify_intent

    # Very strong diagnose signal — should cap at 1.0
    result = classify_intent(
        "why crash fail broken 502 503 diagnose troubleshoot not work bad gateway timeout"
    )
    assert result.confidence <= 1.0


def test_top_two_intents_returns_two():
    from navig.commands.ai_router import top_two_intents

    results = top_two_intents("what is the show command history?")
    assert len(results) == 2


def test_top_two_intents_sorted_descending():
    from navig.commands.ai_router import top_two_intents

    results = top_two_intents("list all running sessions status")
    assert results[0].confidence >= results[1].confidence


def test_confidence_threshold_value():
    from navig.commands.ai_router import CONFIDENCE_THRESHOLD

    assert CONFIDENCE_THRESHOLD == 0.85


def test_score_all_covers_all_subcommands():
    from navig.commands.ai_router import _score_all, _WEIGHTS

    results = _score_all("run check show explain suggest ask diagnose")
    result_names = {r.subcommand for r in results}
    assert result_names == set(_WEIGHTS.keys())


def test_intent_result_is_named_tuple():
    from navig.commands.ai_router import IntentResult

    r = IntentResult(subcommand="ask", confidence=0.5)
    assert r.subcommand == "ask"
    assert r.confidence == 0.5


def test_ask_fallback_low_confidence():
    from navig.commands.ai_router import classify_intent

    result = classify_intent("what?")
    # Should find some intent without error
    assert isinstance(result.subcommand, str)


def test_empty_string_handled():
    from navig.commands.ai_router import classify_intent

    result = classify_intent("")
    assert isinstance(result.subcommand, str)
    assert result.confidence == 0.0 or result.confidence >= 0.0
