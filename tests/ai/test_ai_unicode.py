"""Tests for robust Unicode handling in ask_ai (navig/commands/ai.py).

Ensures that non-UTF-8 bytes in tasklist output (e.g. OEM code page cp850/cp1252
on Windows) do not crash the subprocess readerthread with UnicodeDecodeError.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import navig.ai as ai_core
import navig.commands.ai as ai_mod
import navig.config as cfg_mod
import pytest

pytestmark = pytest.mark.integration


def test_ask_ai_handles_non_utf8_tasklist_output():
    """ask_ai must not raise UnicodeDecodeError when tasklist emits non-UTF-8 bytes.

    Simulates a Windows locale where process names contain byte 0xFF (e.g. the
    character 'ÿ' in cp1252) which is an invalid UTF-8 start byte.
    """
    # Build a fake CompletedProcess whose stdout contains 0xFF — invalid UTF-8.
    non_utf8_bytes = b'"python.exe","1234","Console","1","20,\xff00 K"\r\n'
    fake_proc = subprocess.CompletedProcess(
        args=["tasklist", "/FO", "CSV", "/NH"],
        returncode=0,
        stdout=non_utf8_bytes,
        stderr=b"",
    )

    captured_context: dict = {}

    fake_cfg = MagicMock()
    fake_cfg.get_active_server.return_value = "local"
    fake_cfg.load_server_config.return_value = {
        "is_local": True,
        "host": "localhost",
    }
    fake_assistant = MagicMock()
    fake_assistant.ask.side_effect = lambda q, ctx, model_override=None, **kwargs: (
        captured_context.update(ctx) or "mocked answer"
    )

    with (
        patch("os.name", "nt"),
        patch("subprocess.run", return_value=fake_proc),
        patch.object(cfg_mod, "get_config_manager", return_value=fake_cfg),
        patch.object(ai_core, "AIAssistant", return_value=fake_assistant),
    ):
        # Should not raise UnicodeDecodeError
        try:
            ai_mod.ask_ai("test question", None, {"yes": True})
        except SystemExit:
            pass  # Exit from CLI context is acceptable


def test_ask_ai_non_utf8_tasklist_bytes_decoded_gracefully():
    """Directly verify the byte-decoding logic introduced in ask_ai.

    Creates a CompletedProcess with stdout that contains the byte 0xFF (the
    Windows-1252 encoding for 'ÿ') and confirms that ask_ai does not crash.
    """
    # Byte 0xFF is valid in cp1252 ('ÿ') but illegal as a UTF-8 start byte.
    bad_bytes = b'"SomeApp\xff.exe","999","Console","1","10,240 K"\r\n'
    fake_proc = subprocess.CompletedProcess(
        args=["tasklist", "/FO", "CSV", "/NH"],
        returncode=0,
        stdout=bad_bytes,
        stderr=b"",
    )

    fake_cfg = MagicMock()
    fake_cfg.get_active_server.return_value = "local"
    fake_cfg.load_server_config.return_value = {
        "is_local": True,
        "host": "localhost",
    }
    fake_assistant = MagicMock()
    fake_assistant.ask.return_value = "mocked answer"

    with (
        patch("os.name", "nt"),
        patch("subprocess.run", return_value=fake_proc),
        patch.object(cfg_mod, "get_config_manager", return_value=fake_cfg),
        patch.object(ai_core, "AIAssistant", return_value=fake_assistant),
    ):
        # Must not raise; SystemExit from CLI is acceptable
        try:
            ai_mod.ask_ai("what processes are running?", None, {"yes": True})
        except SystemExit:
            pass
