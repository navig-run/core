"""
Tests for navig.commands.email
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.commands.email import email_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_message(subject="Hello", sender="a@b.com", preview="body", important=False):
    msg = MagicMock()
    msg.id = "1"
    msg.subject = subject
    msg.sender = sender
    msg.date = datetime(2024, 1, 15, 10, 30)
    msg.preview = preview
    msg.is_important = important
    return msg


class _MockConfigManager:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def _load_global_config(self):
        return self._cfg


def _patch_config(cfg: dict):
    """Patch navig.config.get_config_manager to return cfg."""
    return patch(
        "navig.config.get_config_manager",
        return_value=_MockConfigManager(cfg),
    )


# ---------------------------------------------------------------------------
# navig email search
# ---------------------------------------------------------------------------

class TestSearchEmails:
    def test_exits_zero(self):
        assert runner.invoke(email_app, ["search", "invoice"]).exit_code == 0

    def test_echoes_query(self):
        result = runner.invoke(email_app, ["search", "invoice"])
        assert "invoice" in result.output.lower()

    def test_coming_soon_message(self):
        result = runner.invoke(email_app, ["search", "test"])
        assert "soon" in result.output.lower()


# ---------------------------------------------------------------------------
# navig email sync
# ---------------------------------------------------------------------------

class TestSyncEmail:
    def test_exits_zero(self):
        assert runner.invoke(email_app, ["sync"]).exit_code == 0

    def test_prints_sync_word(self):
        assert "sync" in runner.invoke(email_app, ["sync"]).output.lower()


# ---------------------------------------------------------------------------
# navig email list — asyncio.run mocked
# ---------------------------------------------------------------------------

class TestListEmails:
    def test_shows_subject_in_output(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [_make_mock_message("Invoice")]
            result = runner.invoke(email_app, ["list"])
        assert result.exit_code == 0
        assert "Invoice" in result.output

    def test_shows_sender_in_output(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [_make_mock_message(sender="boss@co.com")]
            result = runner.invoke(email_app, ["list"])
        assert "boss@co.com" in result.output

    def test_no_messages_exits_zero(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = []
            result = runner.invoke(email_app, ["list"])
        assert result.exit_code == 0

    def test_no_messages_shows_info(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = []
            result = runner.invoke(email_app, ["list"])
        combined = result.output.lower()
        assert "no" in combined or "found" in combined

    def test_important_flag_shows_star(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [_make_mock_message(important=True)]
            result = runner.invoke(email_app, ["list"])
        assert "⭐" in result.output

    def test_preview_long_gets_ellipsis(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [_make_mock_message(preview="x" * 200)]
            result = runner.invoke(email_app, ["list"])
        assert "..." in result.output

    def test_limit_flag_accepted(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = []
            result = runner.invoke(email_app, ["list", "--limit", "5"])
        assert result.exit_code == 0

    def test_multiple_messages_all_printed(self):
        msgs = [_make_mock_message(f"Msg{i}") for i in range(3)]
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = msgs
            result = runner.invoke(email_app, ["list"])
        for i in range(3):
            assert f"Msg{i}" in result.output


# ---------------------------------------------------------------------------
# navig email list --json
# ---------------------------------------------------------------------------

class TestListEmailsJson:
    def test_output_is_valid_json(self):
        msg = _make_mock_message("Test", "me@example.com", "hi", important=True)
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [msg]
            result = runner.invoke(email_app, ["list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["subject"] == "Test"
        assert data[0]["is_important"] is True

    def test_json_empty_list(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = []
            result = runner.invoke(email_app, ["list", "--json"])
        assert json.loads(result.output) == []

    def test_json_contains_required_fields(self):
        msg = _make_mock_message()
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [msg]
            result = runner.invoke(email_app, ["list", "--json"])
        item = json.loads(result.output)[0]
        assert {"id", "subject", "sender", "date", "preview", "is_important"} <= set(item)

    def test_json_date_is_iso_format(self):
        msg = _make_mock_message()
        with patch("navig.commands.email.asyncio.run") as m:
            m.return_value = [msg]
            result = runner.invoke(email_app, ["list", "--json"])
        item = json.loads(result.output)[0]
        # Should be parseable as ISO datetime
        datetime.fromisoformat(item["date"])


# ---------------------------------------------------------------------------
# navig email send
# ---------------------------------------------------------------------------

class TestSendEmail:
    def test_requires_to_option(self):
        result = runner.invoke(email_app, ["send", "--subject", "s", "--body", "b"])
        assert result.exit_code != 0

    def test_requires_subject_option(self):
        result = runner.invoke(email_app, ["send", "--to", "a@b.com", "--body", "b"])
        assert result.exit_code != 0

    def test_exits_zero_with_mocked_run(self):
        with patch("navig.commands.email.asyncio.run") as m:
            m.side_effect = lambda coro: None
            result = runner.invoke(
                email_app,
                ["send", "--to", "x@y.com", "--subject", "Hello", "--body", "Hi"],
            )
        assert result.exit_code == 0

    def test_not_configured_prints_error(self):
        """When email is not enabled, send_email outputs an error message."""
        cm = MagicMock()
        cm._load_global_config.return_value = {}  # no proactive.email entry
        with patch("navig.config.get_config_manager", return_value=cm):
            result = runner.invoke(
                email_app,
                ["send", "--to", "x@y.com", "--subject", "Subj", "--body", "Body"],
            )
        assert result.exit_code == 0
        assert (
            "not configured" in result.output.lower()
            or "configure" in result.output.lower()
        )
