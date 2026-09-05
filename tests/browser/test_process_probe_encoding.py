"""`is_running()` must survive console output that is not valid UTF-8.

On Windows ``tasklist`` writes the **OEM** code page (866 on the operator's machine, per
``chcp``) while ``subprocess(text=True)`` decodes with the **ANSI** one (cp1251). Measured
against the real tool: a single filtered query returned 6710 bytes of which **85 were
non-ASCII**, from the localized header row.

That mismatch is harmless for an ASCII needle like ``chrome.exe`` — cp866, cp1251 and utf-8
all agree below 128. What is NOT harmless is the obvious "fix": adding
``encoding="utf-8"`` to match the rest of the codebase. Measured on the same bytes::

    cp1251  decodes OK        chrome.exe found: True
    cp866   decodes OK        chrome.exe found: True
    utf-8   RAISES UnicodeDecodeError at byte 150

and ``is_running`` wraps the call in ``except Exception: return False``. So a strict decode
does not surface as an error — it silently becomes **"the process is not running"**, which
is the opposite of the truth and the answer that decides whether callers touch files a live
browser has locked.

The fix is to not decode at all: an image name is ASCII, so the comparison is done on
bytes and the question of which code page it is never arises. This test pins that.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from navig.browser import targets

# The shape of real `tasklist` output on a Russian-locale Windows: a localized header in
# the OEM code page, then the ASCII process row. `\xe1\xa2` is valid cp866 and cp1251 and
# is NOT valid UTF-8 — decoding this strictly is what raises.
_OEM_TASKLIST = b"\xc8\xec\xff \xe1\xa2\xe0\xa0\xa7\xa0\r\n=====\r\nchrome.exe   1234 Console\r\n"


@pytest.fixture
def _no_psutil(monkeypatch):
    """Force the subprocess fallback branch, which is the one under test."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _fail(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil disabled for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fail)


def test_non_utf8_console_output_still_finds_the_process(monkeypatch, _no_psutil):
    """The decisive case: bytes that utf-8 cannot decode must not read as 'not running'."""
    monkeypatch.setattr(sys, "platform", "win32")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=_OEM_TASKLIST, stderr=b"")

    monkeypatch.setattr(targets.subprocess, "run", fake_run)

    assert targets.is_running("chrome") is True, (
        "a live process was reported as not running — the probe decoded output it should "
        "not have decoded"
    )
    # And it must have asked for bytes: text mode is what re-introduces the code page.
    assert not seen.get("text"), "the probe must not request text mode"
    assert "encoding" not in seen, (
        "an encoding= here is the trap this test exists for: utf-8 raises on OEM bytes and "
        "the surrounding except: turns that into 'not running'"
    )


def test_absent_process_is_still_reported_absent(monkeypatch, _no_psutil):
    """Anti-vacuity: the probe must not simply answer True."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        targets.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=b"INFO: No tasks are running which match the criteria.\r\n", stderr=b""
        ),
    )
    assert targets.is_running("chrome") is False


def test_the_fixture_bytes_really_are_undecodable_as_utf8() -> None:
    """The premise of this file, asserted rather than assumed.

    If someone 'tidies' the fixture into ASCII, both tests above keep passing while
    proving nothing at all.
    """
    with pytest.raises(UnicodeDecodeError):
        _OEM_TASKLIST.decode("utf-8")
    # ...and it IS decodable by the two code pages actually in play, which is why the old
    # text=True code appeared to work.
    assert "chrome.exe" in _OEM_TASKLIST.decode("cp866").lower()
    assert "chrome.exe" in _OEM_TASKLIST.decode("cp1251").lower()


def test_module_exposes_subprocess_for_patching() -> None:
    """Guards the seam the tests above rely on."""
    assert isinstance(targets.subprocess, types.ModuleType)
