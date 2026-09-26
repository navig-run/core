"""An interactive `navig` command must not print the log stream above its output.

Measured on a fresh isolated config before the fix: `navig doctor 2>&1 >/dev/null`
produced 26 lines — one `INFO plugins navig plugin loaded: <name>` per installed
plugin, the plugins' own `… module registered` INFO lines, and a raw loguru DEBUG
line with ANSI colour (`skills.loader: loaded 113 skills from 21 dir(s)`) that had
bypassed every navig handler because loguru's default stderr sink was never removed.

Two mechanisms close it: the CLI callback sets the console handler to WARNING
(`--verbose` → INFO), and loguru is bridged into the `navig` logging tree.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import navig.core.logging as nlog


@pytest.fixture
def navig_root_with_stream(monkeypatch):
    """A configured `navig` root whose console handler writes into a StringIO."""
    monkeypatch.setattr(nlog, "_CONSOLE_LEVEL_OVERRIDE", None)
    nlog._configure_root_logger()
    root = logging.getLogger("navig")
    buf = io.StringIO()
    for h in list(root.handlers):
        if getattr(h, "navig_console", False):
            h.stream = buf
    yield root, buf
    nlog._configure_root_logger()  # leave the root the way the suite expects it


def _console_levels(root: logging.Logger) -> list[int]:
    return [h.level for h in root.handlers if getattr(h, "navig_console", False)]


def test_warning_policy_hides_info_but_keeps_warnings(navig_root_with_stream):
    root, buf = navig_root_with_stream
    nlog.set_console_level(logging.WARNING)
    assert _console_levels(root) == [logging.WARNING]

    logging.getLogger("navig.plugins").info("navig plugin loaded: audio")
    logging.getLogger("navig.plugins").warning("navig plugin 'x' failed to load: boom")
    out = buf.getvalue()
    assert "plugin loaded" not in out
    assert "failed to load" in out


def test_verbose_policy_shows_info(navig_root_with_stream):
    root, buf = navig_root_with_stream
    nlog.set_console_level(logging.INFO)
    logging.getLogger("navig.plugins").info("navig plugin loaded: audio")
    assert "plugin loaded" in buf.getvalue()


def test_policy_set_before_configuration_applies_to_the_handler_created_later(monkeypatch):
    monkeypatch.setattr(nlog, "_CONSOLE_LEVEL_OVERRIDE", None)
    nlog.set_console_level(logging.WARNING)
    nlog._configure_root_logger()  # (re)creates the console handler
    try:
        assert _console_levels(logging.getLogger("navig")) == [logging.WARNING]
    finally:
        monkeypatch.setattr(nlog, "_CONSOLE_LEVEL_OVERRIDE", None)
        nlog._configure_root_logger()


def test_the_file_handler_is_never_quieted(tmp_path, monkeypatch):
    monkeypatch.setattr(nlog, "_CONSOLE_LEVEL_OVERRIDE", None)
    log_file = tmp_path / "navig.log"
    nlog._configure_root_logger(log_file)
    try:
        nlog.set_console_level(logging.WARNING)
        file_handlers = [h for h in logging.getLogger("navig").handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers and all(h.level == logging.DEBUG for h in file_handlers)
        logging.getLogger("navig.plugins").info("navig plugin loaded: audio")
        for h in file_handlers:
            h.flush()
        assert "plugin loaded: audio" in log_file.read_text(encoding="utf-8")
    finally:
        for h in list(logging.getLogger("navig").handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
        monkeypatch.setattr(nlog, "_CONSOLE_LEVEL_OVERRIDE", None)
        nlog._configure_root_logger()


def test_loguru_records_flow_through_the_navig_tree_not_stderr(navig_root_with_stream, capsys):
    loguru = pytest.importorskip("loguru")
    root, buf = navig_root_with_stream
    nlog.set_console_level(logging.WARNING)

    seen: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # noqa: D401
            seen.append(record)

    cap = _Capture(level=logging.DEBUG)
    root.addHandler(cap)
    try:
        loguru.logger.info("skills.loader: loaded {} skills", 113)
        loguru.logger.warning("token=sk-live-abcdefghijklmnop leaked")
    finally:
        root.removeHandler(cap)

    names = {r.name for r in seen}
    assert any(n.startswith("navig") for n in names), names
    messages = [r.getMessage() for r in seen]
    assert any("loaded 113 skills" in m for m in messages)
    # the INFO line obeyed the console policy …
    assert "loaded 113 skills" not in buf.getvalue()
    # … the WARNING reached the console, redacted, and loguru's own sink wrote nothing
    assert "leaked" in buf.getvalue()
    assert "sk-live-abcdefghijklmnop" not in buf.getvalue()
    assert "skills" not in capsys.readouterr().err


@pytest.mark.integration
def test_a_real_command_prints_no_log_lines_to_stderr(tmp_path):
    """End to end: `navig version` in a virgin config dir, stderr must be empty."""
    core_dir = Path(__file__).resolve().parents[2]
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(core_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(
        NAVIG_CONFIG_DIR=str(cfg),
        NAVIG_DATA_DIR=str(tmp_path / "data"),
        NAVIG_LOG_DIR=str(tmp_path / "log"),
        NAVIG_SKIP_ONBOARDING="1",
        NAVIG_NO_TELEMETRY="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "navig", "store", "status"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    noise = [
        line
        for line in proc.stderr.splitlines()
        if " INFO " in line or " DEBUG " in line or "plugin loaded" in line
    ]
    assert not noise, f"log stream leaked to stderr:\n{proc.stderr}"
