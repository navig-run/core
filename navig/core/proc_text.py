"""Canonical decoding for the output of a **console** subprocess.

**Why this exists.** ``subprocess.run(cmd, text=True)`` decodes the child's stdout with
``locale.getpreferredencoding(False)`` — the **ANSI** code page on Windows. Windows console
tools (``tasklist``, ``icacls``, ``whoami``, ``sc``, ``netstat``, ``powershell`` …) do not
write the ANSI code page. They write the **console output** code page, which is the OEM one.
On a stock Russian-locale Windows those are two different encodings, and neither is UTF-8:

    GetACP() = 1251        GetOEMCP() = GetConsoleOutputCP() = 866

Measured against a real ``whoami /groups`` on this machine (2026-08-10, CPython 3.13)::

    raw     b'BUILTIN\\\\\\x80\\xa4\\xac\\xa8\\xad\\xa8\\xe1\\xe2\\xe0\\xa0\\xe2\\xae\\xe0\\xeb'
    oem     'BUILTIN\\\\Администраторы'      <- correct
    text=True (cp1251)                       <- 14 wrong characters, NO exception
    utf-8   UnicodeDecodeError at byte 1000  <- the "obvious tidy-up" is a CRASH

Both wrong answers are dangerous in different ways. The ANSI decode never raises, because
cp1251 maps almost every byte to *some* character — so the corruption is silent and gets
written back out, logged, or handed to the model. Forcing UTF-8 turns that silence into an
exception in code paths whose ``except`` clauses do not list ``UnicodeDecodeError``.

**Do not "fix" a console-tool site by naming ``encoding="utf-8"``.** That is the sibling
trap documented in ``tests/quality/test_git_subprocess_encoding.py``: git's contract *is*
UTF-8, a Windows console tool's contract is the console code page, and the two guards
deliberately point in opposite directions.

Usage — either shape works, pick whichever the call site already has:

    from navig.core.proc_text import console_encoding, decode_console_output

    subprocess.run(cmd, capture_output=True,
                   encoding=console_encoding(), errors="replace")   # text mode

    r = subprocess.run(cmd, capture_output=True)                    # bytes mode
    text = decode_console_output(r.stdout)

``errors="replace"`` belongs with it, and is not decorative. Measured: the single-byte OEM
pages map all 256 values and cannot raise (cp866 · cp850 · cp437 · cp852 — 0 undecodable
bytes each), but the CJK ones can — cp932 has 60 undecodable single bytes, cp936 and cp949
have 128, and all three raise on a lone trail byte. Console output truncated at a timeout is
exactly that shape, so on a Japanese, Chinese or Korean Windows a strict decode is a latent
crash. This matches the house convention for foreign process output already used in
``commands/miniapp.py`` and navig-explore.

⚠ A needle that is pure ASCII needs no decoding at all. When the only question is *"does
this substring appear"*, compare **bytes** and skip this module entirely — that is what
``browser/targets.py`` and the two plugin process probes do.
"""

from __future__ import annotations

import codecs
import functools
import os
import subprocess

__all__ = ["console_encoding", "decode_console_output", "decode_console_result"]

# Python registers the "oem" codec alias on Windows only; it resolves to GetOEMCP(). It is
# the correct default here and the fallback when the console code page cannot be read.
_WINDOWS_FALLBACK = "oem"


@functools.lru_cache(maxsize=1)
def console_encoding() -> str:
    """The codec a Windows console tool's output is actually written in.

    ``GetConsoleOutputCP()`` is preferred over ``GetOEMCP()`` because it is what a console
    application queries before writing, and it is what changes when a user runs ``chcp``.
    Someone who has run ``chcp 65001`` for a UTF-8 terminal gets a UTF-8-writing child, and
    decoding that as cp866 would be the same bug one layer down. It returns 0 when the
    process has no console at all (``pythonw``, a service) — there is no console code page
    to inherit, the child falls back to the OEM one, and so do we.

    Cached: the code page is a property of the process, and this is called per subprocess.

    Non-Windows returns ``"utf-8"``: POSIX console tools honour the locale, which is UTF-8
    on every platform navig supports, and ``"oem"`` does not exist as a codec there.
    """
    if os.name != "nt":
        return "utf-8"

    code_page = 0
    try:
        import ctypes  # noqa: PLC0415 — Windows-only, and only on the first call

        kernel32 = ctypes.windll.kernel32
        code_page = int(kernel32.GetConsoleOutputCP() or kernel32.GetOEMCP() or 0)
    except (AttributeError, OSError, ValueError):
        return _WINDOWS_FALLBACK

    if code_page == 65001:
        return "utf-8"
    if not code_page:
        return _WINDOWS_FALLBACK

    # Not every Windows code page has a Python codec (cp708 and friends). Verify rather
    # than hand subprocess a name that raises LookupError at the call site.
    candidate = f"cp{code_page}"
    try:
        codecs.lookup(candidate)
    except LookupError:
        return _WINDOWS_FALLBACK
    return candidate


def decode_console_output(raw: bytes | str | None) -> str:
    """Decode console-tool output to ``str``. Never raises.

    UTF-8 is tried first and strictly, so a tool that writes UTF-8 regardless of the code
    page (a Go or Rust binary on PATH, or anything under ``chcp 65001``) is read correctly.
    That attempt is safe because real console text is not valid UTF-8 — measured across 67
    encodable word/code-page pairs (Russian, French, German and Spanish console strings in
    cp866 · cp850 · cp437 · cp852 · cp1251 · cp932): **zero** decoded as valid UTF-8. It is
    the byte *distribution* that saves it rather than any single rule: 11.7% of arbitrary
    two-byte high sequences are valid UTF-8, but they correspond to cp866 box-drawing and
    Cyrillic mixtures that do not occur in words.

    ``str`` passes through unchanged, so a caller that already used text mode can route
    through here without a type check of its own.
    """
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return bytes(raw).decode(console_encoding(), errors="replace")


def decode_console_result(
    result: subprocess.CompletedProcess,
) -> subprocess.CompletedProcess:
    """A ``CompletedProcess`` captured in **bytes**, with its streams decoded to ``str``.

    For a runner that executes an arbitrary *user-supplied* command there is no single
    correct codec, because the shell does not normalise what its child wrote. Measured on
    this machine, `cmd.exe` (``shell=True``) and ``powershell -Command`` BOTH pass child
    bytes through untouched::

        git log -1 --format=%s     ->  b'... clean main \\xe2\\x80\\x94 and ...'   (UTF-8)
        whoami /groups             ->  b'BUILTIN\\\\\\x80\\xa4\\xac...'              (cp866)

    So a fixed ``encoding=`` is wrong for one of them whichever you pick — naming UTF-8
    mangles the console tool, naming the console page mangles git. Only the two-step
    :func:`decode_console_output` reads both correctly, and it cannot be expressed as a
    ``subprocess`` kwarg. Hence: capture bytes, decode here.

    A call site that runs ONE known command does not need this — name the codec its
    contract requires (``encoding="utf-8"`` for git, ``console_encoding()`` for a console
    tool) and let the sibling guards enforce it.
    """
    # `args` via getattr: a real CompletedProcess always carries it, but this is handed
    # duck-typed stand-ins too — test doubles that fake `subprocess.run` with a plain
    # SimpleNamespace(returncode, stdout, stderr) are the common case, and eight of them
    # failed with AttributeError when this required the attribute. Decoding streams is
    # this function's job; the command line is passed through when it happens to be there.
    # Everything via getattr. A real CompletedProcess carries all four, but this is handed
    # duck-typed stand-ins too — test doubles that fake `subprocess.run` with a plain
    # SimpleNamespace(returncode=0) are the common case, and eight of them failed with
    # AttributeError when this required `args`, then three more when it required `stdout`.
    # Decoding the streams is this function's only job; missing ones decode to "" exactly
    # as an uncaptured stream already does, so nothing about the result changes shape.
    return subprocess.CompletedProcess(
        getattr(result, "args", None),
        getattr(result, "returncode", 0),
        decode_console_output(getattr(result, "stdout", None)),
        decode_console_output(getattr(result, "stderr", None)),
    )
