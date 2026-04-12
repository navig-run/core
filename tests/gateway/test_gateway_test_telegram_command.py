import json

from typer.testing import CliRunner

from navig.commands.gateway import gateway_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_gateway_test_telegram_success(monkeypatch):
    def _fake_send(**kwargs):
        return 42

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        gateway_app,
        ["test", "telegram", "--target", "42", "--message", "ping"],
    )

    assert result.exit_code == 0
    assert "✓ telegram" in result.output


def test_gateway_test_telegram_failure(monkeypatch):
    def _fake_send(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        gateway_app,
        ["test", "telegram", "--target", "42", "--message", "ping"],
    )

    assert result.exit_code == 0
    assert "✗ telegram" in result.output
    assert "boom" in result.output


def test_gateway_test_telegram_failure_strict(monkeypatch):
    def _fake_send(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        gateway_app,
        ["test", "telegram", "--target", "42", "--message", "ping", "--strict"],
    )

    assert result.exit_code == 1


def test_gateway_test_telegram_json(monkeypatch):
    def _fake_send(**kwargs):
        return 42

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)

    result = runner.invoke(
        gateway_app,
        ["test", "telegram", "--target", "42", "--message", "ping", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["ok"] is True
    assert payload["summary"]["failed"] == 0


def test_gateway_test_all_json_includes_all_channels(monkeypatch):
    def _fake_send(**kwargs):
        return 42

    def _fake_matrix_test(**kwargs):
        return None

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)
    monkeypatch.setattr("navig.commands.matrix.send", _fake_matrix_test)

    result = runner.invoke(
        gateway_app,
        ["test", "all", "--target", "42", "--message", "ping", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["channels_tested"] == 4
    names = [entry["channel"] for entry in payload["results"]]
    assert names == ["telegram", "matrix", "discord", "email"]


def test_gateway_test_all_uppercase_alias(monkeypatch):
    def _fake_send(**kwargs):
        return 42

    def _fake_matrix_test(**kwargs):
        return None

    monkeypatch.setattr("navig.commands.telegram.telegram_send", _fake_send)
    monkeypatch.setattr("navig.commands.matrix.send", _fake_matrix_test)

    result = runner.invoke(
        gateway_app,
        ["test", "ALL", "--target", "42", "--message", "ping", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["channels_tested"] == 4
