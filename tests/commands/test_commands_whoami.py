"""Tests for navig/commands/whoami.py."""

import pytest
from unittest.mock import MagicMock, patch, call

from navig.commands.whoami import run_whoami


# ---------------------------------------------------------------------------
# No entity path
# ---------------------------------------------------------------------------

def test_no_entity_prints_message():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        console_mock = MagicMock()
        with patch("navig.commands.whoami.get_console", return_value=console_mock):
            run_whoami()
    console_mock.print.assert_called_once()


def test_no_entity_message_contains_onboard_or_no_entity():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        console_mock = MagicMock()
        with patch("navig.commands.whoami.get_console", return_value=console_mock):
            run_whoami()
    call_arg = console_mock.print.call_args[0][0]
    assert "onboard" in call_arg.lower() or "No entity" in call_arg


def test_no_entity_does_not_call_derive_entity():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        with patch("navig.commands.whoami.get_console", return_value=MagicMock()):
            with patch("navig.identity.entity.derive_entity") as mock_derive:
                run_whoami()
    mock_derive.assert_not_called()


def test_no_entity_does_not_call_render_sigil_card():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        with patch("navig.commands.whoami.get_console", return_value=MagicMock()):
            with patch("navig.identity.renderer.render_sigil_card") as mock_render:
                run_whoami()
    mock_render.assert_not_called()


def test_no_entity_returns_none():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        with patch("navig.commands.whoami.get_console", return_value=MagicMock()):
            result = run_whoami()
    assert result is None


def test_no_entity_empty_dict_treated_as_falsy_or_raises():
    """Empty dict may or may not be treated as falsy — just no unhandled crash."""
    with patch("navig.identity.sigil_store.load_entity", return_value={}):
        with patch("navig.commands.whoami.get_console", return_value=MagicMock()):
            with patch("navig.identity.entity.derive_entity", return_value=MagicMock()):
                with patch("navig.identity.renderer.render_sigil_card"):
                    try:
                        run_whoami()
                    except (KeyError, TypeError):
                        pass  # Empty dict without "seed" key is expected to fail


# ---------------------------------------------------------------------------
# Entity found path
# ---------------------------------------------------------------------------

def test_with_entity_calls_load_entity():
    fake_data = {"seed": "abc123"}
    fake_entity = MagicMock()
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data) as mock_load:
        with patch("navig.identity.entity.derive_entity", return_value=fake_entity):
            with patch("navig.identity.renderer.render_sigil_card"):
                run_whoami()
    mock_load.assert_called_once()


def test_with_entity_calls_derive_entity_with_seed():
    fake_data = {"seed": "abc123"}
    fake_entity = MagicMock()
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data):
        with patch("navig.identity.entity.derive_entity", return_value=fake_entity) as mock_derive:
            with patch("navig.identity.renderer.render_sigil_card"):
                run_whoami()
    mock_derive.assert_called_once_with("abc123")


def test_with_entity_calls_render_sigil_card():
    fake_data = {"seed": "myseed"}
    fake_entity = MagicMock()
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data):
        with patch("navig.identity.entity.derive_entity", return_value=fake_entity):
            with patch("navig.identity.renderer.render_sigil_card") as mock_render:
                run_whoami()
    mock_render.assert_called_once_with(fake_entity)


def test_with_entity_render_receives_derived_entity():
    fake_data = {"seed": "xyz"}
    derived = MagicMock(name="derived_entity")
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data):
        with patch("navig.identity.entity.derive_entity", return_value=derived):
            with patch("navig.identity.renderer.render_sigil_card") as mock_render:
                run_whoami()
    assert mock_render.call_args[0][0] is derived


def test_with_entity_does_not_print_no_entity_message():
    fake_data = {"seed": "xyz"}
    console_mock = MagicMock()
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data):
        with patch("navig.commands.whoami.get_console", return_value=console_mock):
            with patch("navig.identity.entity.derive_entity", return_value=MagicMock()):
                with patch("navig.identity.renderer.render_sigil_card"):
                    run_whoami()
    for c in console_mock.print.call_args_list:
        arg = c[0][0] if c[0] else ""
        assert "No entity" not in str(arg)


def test_with_entity_returns_none():
    fake_data = {"seed": "returnseed"}
    with patch("navig.identity.sigil_store.load_entity", return_value=fake_data):
        with patch("navig.identity.entity.derive_entity", return_value=MagicMock()):
            with patch("navig.identity.renderer.render_sigil_card"):
                result = run_whoami()
    assert result is None


def test_with_entity_different_seeds_passed_through():
    for seed_val in ["seed-alpha", "seed-beta", "0x4F2A"]:
        with patch("navig.identity.sigil_store.load_entity", return_value={"seed": seed_val}):
            with patch("navig.identity.entity.derive_entity", return_value=MagicMock()) as mock_d:
                with patch("navig.identity.renderer.render_sigil_card"):
                    run_whoami()
        mock_d.assert_called_once_with(seed_val)


# ---------------------------------------------------------------------------
# Function is importable and callable
# ---------------------------------------------------------------------------

def test_run_whoami_is_callable():
    assert callable(run_whoami)


def test_run_whoami_accepts_no_args():
    with patch("navig.identity.sigil_store.load_entity", return_value=None):
        with patch("navig.commands.whoami.get_console", return_value=MagicMock()):
            run_whoami()  # should not raise
