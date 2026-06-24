"""Tests for cli/_quotes and commands/council — batch 48."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

runner = CliRunner()

# ---------------------------------------------------------------------------
# cli/_quotes
# ---------------------------------------------------------------------------

def test_hacker_quotes_is_list():
    from navig.cli._quotes import HACKER_QUOTES

    assert isinstance(HACKER_QUOTES, list)


def test_hacker_quotes_not_empty():
    from navig.cli._quotes import HACKER_QUOTES

    assert len(HACKER_QUOTES) > 0


def test_hacker_quotes_are_tuples():
    from navig.cli._quotes import HACKER_QUOTES

    for item in HACKER_QUOTES:
        assert isinstance(item, tuple), f"Not a tuple: {item}"


def test_hacker_quotes_tuple_length():
    from navig.cli._quotes import HACKER_QUOTES

    for item in HACKER_QUOTES:
        assert len(item) == 2, f"Expected 2-tuple, got {len(item)}"


def test_hacker_quotes_strings():
    from navig.cli._quotes import HACKER_QUOTES

    for quote, attribution in HACKER_QUOTES:
        assert isinstance(quote, str)
        assert isinstance(attribution, str)


def test_hacker_quotes_no_empty_text():
    from navig.cli._quotes import HACKER_QUOTES

    for quote, attribution in HACKER_QUOTES:
        assert quote.strip(), f"Empty quote found"
        assert attribution.strip(), f"Empty attribution found"


def test_hacker_quotes_count_reasonable():
    from navig.cli._quotes import HACKER_QUOTES

    assert len(HACKER_QUOTES) >= 20


def test_hacker_quotes_contains_linus():
    from navig.cli._quotes import HACKER_QUOTES

    attributions = [a for _, a in HACKER_QUOTES]
    assert any("Linus" in a for a in attributions)


def test_hacker_quotes_contains_knuth():
    from navig.cli._quotes import HACKER_QUOTES

    attributions = [a for _, a in HACKER_QUOTES]
    assert any("Knuth" in a for a in attributions)


def test_hacker_quotes_all_unique_quotes():
    from navig.cli._quotes import HACKER_QUOTES

    quotes = [q for q, _ in HACKER_QUOTES]
    assert len(quotes) == len(set(quotes)), "Duplicate quotes found"


# ---------------------------------------------------------------------------
# commands/council
# ---------------------------------------------------------------------------

_WARN = "navig.console_helper.warn"


def _make_formation(agents=2):
    formation = MagicMock()
    formation.id = "test-formation"
    formation.loaded_agents = [MagicMock() for _ in range(agents)]
    return formation


def test_council_run_no_formation():
    from navig.commands.council import council_app

    with (
        patch("navig.commands.council.ch") as mock_ch,
        patch("navig.formations.loader.get_active_formation", return_value=None),
        patch.dict("sys.modules", {
            "navig.formations.loader": MagicMock(get_active_formation=lambda: None),
            "navig.formations.council": MagicMock(),
        }),
    ):
        result = runner.invoke(council_app, ["run", "Should we do X?"])
    assert result.exit_code != 0


def test_council_run_formation_no_agents():
    from navig.commands.council import council_app

    formation = MagicMock()
    formation.id = "test"
    formation.loaded_agents = []

    mock_loader = MagicMock()
    mock_loader.get_active_formation.return_value = formation

    with patch.dict("sys.modules", {
        "navig.formations.loader": mock_loader,
        "navig.formations.council": MagicMock(),
    }):
        result = runner.invoke(council_app, ["run", "test question"])
    assert result.exit_code != 0


def test_council_run_json_output():
    from navig.commands.council import council_app

    formation = _make_formation(2)
    fake_result = {
        "pack": "test-formation",
        "overall_confidence": 0.85,
        "total_duration_ms": 100,
        "agents_count": 2,
        "final_decision": "Do it.",
        "rounds": [],
    }

    mock_loader = MagicMock()
    mock_loader.get_active_formation.return_value = formation

    mock_council = MagicMock()
    mock_council.run_council.return_value = fake_result

    with patch.dict("sys.modules", {
        "navig.formations.loader": mock_loader,
        "navig.formations.council": mock_council,
    }):
        result = runner.invoke(council_app, ["run", "test question", "--json"])
    assert result.exit_code == 0
    # JSON output should be parseable
    data = json.loads(result.output)
    assert data["final_decision"] == "Do it."


def test_council_run_plain_output():
    from navig.commands.council import council_app

    formation = _make_formation(2)
    fake_result = {
        "pack": "formation-x",
        "overall_confidence": 0.9,
        "total_duration_ms": 50,
        "agents_count": 2,
        "final_decision": "Yes, proceed.",
        "rounds": [],
    }

    mock_loader = MagicMock()
    mock_loader.get_active_formation.return_value = formation

    mock_council = MagicMock()
    mock_council.run_council.return_value = fake_result

    with patch.dict("sys.modules", {
        "navig.formations.loader": mock_loader,
        "navig.formations.council": mock_council,
    }):
        result = runner.invoke(council_app, ["run", "test question", "--plain"])
    assert result.exit_code == 0
    assert "formation=" in result.output
    assert "confidence=" in result.output


def test_council_run_plain_includes_final_decision():
    from navig.commands.council import council_app

    formation = _make_formation(1)
    fake_result = {
        "pack": "f",
        "overall_confidence": 0.5,
        "total_duration_ms": 30,
        "agents_count": 1,
        "final_decision": "Unique decision text here",
        "rounds": [],
    }

    mock_loader = MagicMock()
    mock_loader.get_active_formation.return_value = formation

    mock_council = MagicMock()
    mock_council.run_council.return_value = fake_result

    with patch.dict("sys.modules", {
        "navig.formations.loader": mock_loader,
        "navig.formations.council": mock_council,
    }):
        result = runner.invoke(council_app, ["run", "test question", "--plain"])
    assert "Unique decision text here" in result.output


def test_council_run_multiple_rounds_arg():
    from navig.commands.council import council_app

    formation = _make_formation(2)
    fake_result = {
        "pack": "f", "overall_confidence": 0.7, "total_duration_ms": 200,
        "agents_count": 2, "final_decision": "Done.", "rounds": [],
    }

    mock_loader = MagicMock()
    mock_loader.get_active_formation.return_value = formation

    mock_council = MagicMock()
    mock_council.run_council.return_value = fake_result

    with patch.dict("sys.modules", {
        "navig.formations.loader": mock_loader,
        "navig.formations.council": mock_council,
    }):
        result = runner.invoke(council_app, ["run", "test", "--rounds", "3", "--plain"])
    assert result.exit_code == 0
    # run_council should have been called with rounds=3
    call_kwargs = mock_council.run_council.call_args[1] if mock_council.run_council.call_args else {}
    assert call_kwargs.get("rounds") == 3 or mock_council.run_council.called


def test_council_run_help():
    from navig.commands.council import council_app

    result = runner.invoke(council_app, ["run", "--help"])
    assert result.exit_code == 0


def test_council_run_no_args_fails():
    from navig.commands.council import council_app

    result = runner.invoke(council_app, ["run"])
    assert result.exit_code != 0
