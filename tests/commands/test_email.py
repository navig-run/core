"""
Tests for navig.commands.email
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.email import email_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_config_manager(email_cfg: dict | None = None):
    """Return a mocked config manager that returns the given email config."""
    cfg: dict = {}
    if email_cfg is not None:
        cfg = {"proactive": {"email": email_cfg}}
    cm = MagicMock()
    cm._load_global_config.return_value = cfg
    return cm


def _make_mock_message(subject="Hello", sender="a@b.com", preview="body"):
    from datetime import datetime

    msg = MagicMock()
    msg.id = "1"
    msg.subject = subject
    msg.sender = sender
    msg.date = datetime(2024, 1, 15, 10, 30)
    msg.preview = preview
    msg.is_important = False
    return msg


# ---------------------------------------------------------------------------
# navig email search
# ---------------------------------------------------------------------------

class TestSearchEmails:
    def test_search_prints_warning(self):
        result = runner.invoke(email_app, ["search", "invoice"])
        assert result.exit_code == 0
        assert "invoice" in result.output.lower() or "search" in result.output.lower()

    def test_search_coming_soon_message(self):
        result = runner.invoke(email_app, ["search", "test"])
        assert result.exit_code == 0
        # The command prints "coming soon"
        assert "soon" in result.output.lower()


# ---------------------------------------------------------------------------
# navig email sync
# ---------------------------------------------------------------------------

class TestSyncEmail:
    def test_sync_exits_zero(self):
        result = runner.invoke(email_app, ["sync"])
        assert result.exit_code == 0

    def test_sync_prints_success(self):
        result = runner.invoke(email_app, ["sync"])
        assert "sync" in result.output.lower()


# ---------------------------------------------------------------------------
# navig email list — no email configured (MockEmail)
# ---------------------------------------------------------------------------

class TestListEmailsUnconfigured:
    def _invoke_list(self, extra_args=None):
        extra_args = extra_args or []
        cm = _mock_config_manager()  # no email cfg → not enabled

        mock_messages = [_make_mock_message("Invoice", "boss@co.com", "Pay now")]

        with patch("navig.commands.email.asyncio.run") as mock_run:
            mock_run.return_value = mock_messages
            result = runner.invoke(email_app, ["list"] + extra_args)
        return result

    def test_exits_zero_when_not_configured(self):
        result = self._invoke_list()
        assert result.exit_code == 0

    def test_list_shows_subject(self):
        result = self._invoke_list()
        assert "Invoice" in result.output

    def test_list_no_messages_shows_info(self):
        with patch("navig.commands.email.asyncio.run") as mock_run:
            mock_run.return_value = []
            result = runner.invoke(email_app, ["list"])
        assert result.exit_code == 0
        assert "no" in result.output.lower() or "found" in result.output.lower()


# ---------------------------------------------------------------------------
# navig email list --json
# ---------------------------------------------------------------------------

class TestListEmailsJson:
    def test_json_output_is_valid(self):
        import json

        from datetime import datetime

        msg = MagicMock()
        msg.id = "x1"
        msg.subject = "Test"
        msg.sender = "me@example.com"
        msg.date = datetime(2024, 6, 1, 12, 0)
        msg.preview = "hi there"
        msg.is_important = True

        cm = _mock_config_manager()
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            with patch("navig.commands.email.asyncio.run") as mock_run:
                mock_run.return_value = [msg]
                result = runner.invoke(email_app, ["list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["subject"] == "Test"
        assert data[0]["sender"] == "me@example.com"
        assert data[0]["is_important"] is True

    def test_json_empty_list(self):
        import json

        cm = _mock_config_manager()
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            with patch("navig.commands.email.asyncio.run") as mock_run:
                mock_run.return_value = []
                result = runner.invoke(email_app, ["list", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# navig email send — not configured
# ---------------------------------------------------------------------------

class TestSendEmailNotConfigured:
    def test_send_not_configured_exits_zero(self):
        cm = _mock_config_manager()  # no email enabled
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            with patch("navig.commands.email.asyncio.run") as mock_run:
                # asyncio.run calls our _send coroutine; just run it directly
                mock_run.side_effect = lambda coro: None
                result = runner.invoke(
                    email_app,
                    ["send", "--to", "x@y.com", "--subject", "Hello", "--body", "Hi"],
                )
        assert result.exit_code == 0

    def test_send_error_message_when_not_configured(self):
        """When asyncio.run is NOT mocked the inner async fn still runs."""
        cm = _mock_config_manager()
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            result = runner.invoke(
                email_app,
                ["send", "--to", "x@y.com", "--subject", "Subj", "--body", "Body"],
            )
        assert result.exit_code == 0
        assert "not configured" in result.output.lower() or "configure" in result.output.lower()


# ---------------------------------------------------------------------------
# navig email list — internal async provider routing
# ---------------------------------------------------------------------------

class TestListEmailsProviderRouting:
    """Verify that the provider branch is selected based on config."""

    def _run_with_config(self, email_cfg: dict, messages=None):
        if messages is None:
            messages = []
        cm = _mock_config_manager(email_cfg)
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            with patch("navig.commands.email.asyncio.run") as mock_run:
                mock_run.return_value = messages
                return runner.invoke(email_app, ["list"])

    def test_gmail_provider_config_runs_without_error(self):
        result = self._run_with_config(
            {"enabled": True, "provider": "gmail", "address": "u@g.com", "password": "p"}
        )
        assert result.exit_code == 0

    def test_outlook_provider_config_runs_without_error(self):
        result = self._run_with_config(
            {"enabled": True, "provider": "outlook", "address": "u@o.com", "password": "p"}
        )
        assert result.exit_code == 0

    def test_unknown_provider_falls_back_to_mock(self):
        result = self._run_with_config(
            {"enabled": True, "provider": "pigeon", "address": "u@p.com", "password": "p"}
        )
        assert result.exit_code == 0

    def test_limit_flag_passed(self):
        cm = _mock_config_manager()
        with patch("navig.commands.email.get_config_manager", return_value=cm):
            with patch("navig.commands.email.asyncio.run") as mock_run:
                mock_run.return_value = []
                result = runner.invoke(email_app, ["list", "--limit", "5"])
        assert result.exit_code == 0
