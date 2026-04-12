from typer.testing import CliRunner

from navig.commands.telegram import telegram_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_telegram_send_command_success(monkeypatch):
    called = {}

    def _fake_send(**kwargs):
        called.update(kwargs)
        return 123456

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        telegram_app,
        ["send", "123456", "--message", "hello from test"],
    )

    assert result.exit_code == 0
    assert "Message sent to 123456" in result.output
    assert called["target"] == "123456"
    assert called["message"] == "hello from test"


def test_telegram_send_command_resolve_only(monkeypatch):
    def _fake_send(**kwargs):
        assert kwargs["resolve_only"] is True
        return 98765

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        telegram_app,
        ["send", "@user", "--message", "x", "--resolve-only"],
    )

    assert result.exit_code == 0
    assert "Resolved: @user -> 98765" in result.output
