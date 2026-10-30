"""Console-tool output must be decoded with the console code page, not the ANSI one.

The fixture bytes below are a real ``whoami /groups`` fragment captured on this machine
(Windows 11, GetACP=1251, GetOEMCP=GetConsoleOutputCP=866). They are the whole point of the
module: the same bytes decode three different ways, and only one of them is the group name
Windows actually reported.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import sys

import pytest

from navig.core.proc_text import console_encoding, decode_console_output, oem_encoding

# b'BUILTIN\\' + "Администраторы" in cp866. Kept as bytes, never as a source literal in the
# decoded form — a mangled literal saved into a source file is its own separate defect
# (tests/quality/test_no_mojibake_in_source.py).
OEM_GROUP_LINE = b"BUILTIN\\\x80\xa4\xac\xa8\xad\xa8\xe1\xe2\xe0\xa0\xe2\xae\xe0\xeb"
EXPECTED = "BUILTIN\\" + "".join(
    chr(c) for c in (0x410, 0x434, 0x43C, 0x438, 0x43D, 0x438, 0x441,
                     0x442, 0x440, 0x430, 0x442, 0x43E, 0x440, 0x44B)
)


def test_the_fixture_really_is_undecodable_as_utf8() -> None:
    """Anti-vacuity: if this ever became plain ASCII the rest of the file proves nothing."""
    with pytest.raises(UnicodeDecodeError):
        OEM_GROUP_LINE.decode("utf-8")
    assert sum(1 for b in OEM_GROUP_LINE if b > 127) == 14


def test_the_ansi_codepage_gets_it_wrong_silently() -> None:
    """The defect being guarded: cp1251 succeeds and returns the wrong string."""
    wrong = OEM_GROUP_LINE.decode("cp1251")
    assert wrong != EXPECTED
    assert sum(1 for a, b in zip(wrong, EXPECTED) if a != b) == 14


def test_the_fixture_decodes_to_the_real_group_name_under_cp866() -> None:
    """Fixture integrity, with the code page named explicitly.

    Environment-free on purpose: it says what these bytes ARE, so the behavioural test
    below can be about the function rather than about the machine it runs on.
    """
    assert OEM_GROUP_LINE.decode("cp866") == EXPECTED


@pytest.mark.skipif(os.name != "nt", reason="OEM code pages are a Windows concept")
def test_decode_console_output_falls_back_to_the_live_console_page() -> None:
    """Non-UTF-8 bytes are decoded with whatever the CONSOLE code page currently is.

    Asserted against `console_encoding()` rather than against a fixed string. The fixture
    was captured at cp866, and this originally asserted the literal Cyrillic name — which
    is only true while the console is on an OEM page. Under the full suite it failed with
    twelve U+FFFD, meaning `GetConsoleOutputCP()` had reported 65001 in that worker; on a
    UTF-8 console a tool really would write UTF-8, so the function was right and the
    assertion was measuring the environment. The contract — "UTF-8 first, the live console
    page second" — holds either way, and that is what is pinned.

    The expectation mirrors the WHOLE contract, not just `console_encoding()`: when the
    console page is UTF-8 and these bytes are not valid UTF-8, `decode_console_output`
    falls back to the OEM page (its documented 65001 case), so the expected text is
    `decode(oem_encoding())`, not `decode('utf-8', errors='replace')` — the latter is
    twelve U+FFFD and fails exactly when the gate runs under a UTF-8 console (measured
    2026-10-21 under `conhost --headless`; the sibling test had the same gap).
    """
    enc = console_encoding()
    want = oem_encoding() if enc == "utf-8" else enc
    assert decode_console_output(OEM_GROUP_LINE) == OEM_GROUP_LINE.decode(
        want, errors="replace"
    ), f"decode did not use the contract page (console {enc!r}, want {want!r})"
    if enc.startswith("cp") and enc != "cp65001":
        # On an OEM console the recovered text must be the real name, not replacements.
        assert decode_console_output(OEM_GROUP_LINE) == EXPECTED, (
            f"console page is {enc!r}, so these bytes should read as the localized name"
        )


def test_utf8_is_preferred_when_the_bytes_are_valid_utf8() -> None:
    """A tool that writes UTF-8 regardless of the code page is still read correctly."""
    assert decode_console_output("héllo ✓".encode()) == "héllo ✓"


def test_str_and_empty_pass_through() -> None:
    assert decode_console_output("already text") == "already text"
    assert decode_console_output(b"") == ""
    assert decode_console_output(None) == ""


def test_decode_never_raises_on_undecodable_bytes() -> None:
    """Every caller wraps this in a best-effort path; raising would become 'not found'."""
    assert isinstance(decode_console_output(bytes(range(256))), str)


def test_console_encoding_is_a_real_codec() -> None:
    import codecs

    assert codecs.lookup(console_encoding())


@pytest.mark.skipif(os.name == "nt", reason="POSIX branch")
def test_posix_uses_utf8() -> None:
    assert console_encoding() == "utf-8"


def test_a_stand_in_for_windows_cannot_poison_the_posix_answer(monkeypatch) -> None:
    """The cache is keyed by platform, so a one-off substitution cannot stick.

    It used to be `lru_cache(maxsize=1)` over a no-argument function: whichever caller ran
    first answered for the whole process, and in a long xdist worker that caller could be a
    test standing in for Windows — after which a POSIX process decoded child output with
    `"oem"` (`LookupError: unknown encoding: oem`).
    """
    from navig.core import proc_text

    monkeypatch.setattr(proc_text.sys, "platform", "win32")
    standing_in = console_encoding()  # the Windows branch, resolved for "win32"
    monkeypatch.undo()
    assert standing_in  # some codec name, not a crash
    assert console_encoding() == "utf-8"


@pytest.mark.skipif(os.name != "nt", reason="Windows branch")
def test_windows_matches_the_live_console_code_page() -> None:
    """`console_encoding()` derives from the console's CURRENT output code page.

    ⚠ It is `lru_cache`d for the process lifetime (by design — a console's code page
    does not normally change under a running program). This test reads the live code
    page fresh, so in a long-lived xdist worker the two sides can observe different
    moments: any earlier test that spawned `powershell.exe` on the shared console (which
    sets its output encoding) leaves the cache holding cp866 while the live read says
    65001 — a red that has nothing to do with this code and appears only in the full
    suite. Clear the cache first so both sides see the same console.
    """
    import ctypes

    console_encoding.cache_clear()
    kernel32 = ctypes.windll.kernel32
    live = kernel32.GetConsoleOutputCP() or kernel32.GetOEMCP()
    expected = "utf-8" if live == 65001 else f"cp{live}"
    assert console_encoding() in (expected, "oem")


@pytest.mark.skipif(os.name != "nt", reason="needs a Windows console tool")
def test_end_to_end_against_a_real_console_tool() -> None:
    """The whole class in one assertion: ANSI mode and console mode disagree, and the
    console-mode read is the one that round-trips back to the bytes the tool emitted."""
    import ctypes

    k32 = ctypes.windll.kernel32
    argv = ["whoami", "/groups"]
    # The page must be the SAME one from before the child to after the read: `raw` is only
    # evidence about the page it was written under. Reproduced 2026-09-27, byte-for-byte
    # the full-suite failure — another worker's install test sets 65001 (see
    # tests/install/test_install_scripts.py::_restore_console_code_page), `whoami` writes
    # UTF-8, that worker's teardown restores 866, and re-encoding through 866 yields
    # a single-byte 0x80 where raw holds the UTF-8 lead byte 0xD0. Both sides are right
    # about different consoles; the premise broke, not the code.
    cp_before = k32.GetConsoleOutputCP()
    try:
        raw = subprocess.run(argv, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - tool absent
        pytest.skip("whoami unavailable")
    cp_after_child = k32.GetConsoleOutputCP()
    if not any(b > 127 for b in raw):
        pytest.skip("this Windows install reports ASCII-only group names")

    # Same moment for both sides — see test_windows_matches_the_live_console_code_page.
    console_encoding.cache_clear()
    decoded = decode_console_output(raw)
    encoding = console_encoding()
    cp_at_read = k32.GetConsoleOutputCP()

    # Sampled around the CHILD, not only at the end: the flip is TRANSIENT, so comparing
    # just the first and last reads sees 866 both times and reports a broken premise as a
    # failure.
    if len({cp_before, cp_after_child, cp_at_read}) != 1:
        pytest.skip(
            f"the console code page moved while this test ran ({cp_before} -> "
            f"{cp_after_child} -> {cp_at_read}); raw was written under one page and "
            "re-encoded through another, which is not evidence either way"
        )

    # The samples cannot see a flip that starts AND ends while the child runs (866 -> 65001
    # -> 866 inside `whoami`): all three read 866 while `raw` was written as UTF-8. So judge
    # by the bytes too -- a mismatch on non-UTF-8 output that is itself strictly valid UTF-8
    # is that race, not a decoding bug.
    roundtrip = decoded.encode(encoding, errors="replace")
    if roundtrip != raw and codecs.lookup(encoding).name != "utf-8":
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            pytest.skip(
                f"raw is UTF-8 although the console read {encoding} before, after and at "
                "the read: the page flipped and flipped back while the child ran"
            )

    assert roundtrip == raw

    import locale

    ansi = raw.decode(locale.getpreferredencoding(False), errors="replace")
    assert ansi != decoded, (
        "the ANSI and console code pages agree on this machine, so this test cannot "
        "distinguish them — it is not evidence either way"
    )


def test_decode_console_result_decodes_both_streams() -> None:
    from navig.core.proc_text import decode_console_result

    # b'\xc3\xa9' is U+00E9 in UTF-8, and a perfectly decodable (different) pair in cp866 —
    # so this asserts that the UTF-8 attempt wins, not merely that something decoded.
    raw = subprocess.CompletedProcess(["x"], 3, OEM_GROUP_LINE, "é".encode())
    out = decode_console_result(raw)
    assert out.returncode == 3 and out.args == ["x"]
    assert isinstance(out.stdout, str) and isinstance(out.stderr, str)
    assert out.stderr == "é"
    assert b"\xc3\xa9".decode("cp866") != "é", "fixture no longer distinguishes the codecs"


@pytest.mark.skipif(os.name != "nt", reason="OEM code pages are a Windows concept")
def test_decode_console_result_reads_console_bytes() -> None:
    from navig.core.proc_text import decode_console_result

    # Against the live console page, for the reason given on the sibling test above: the
    # fixture is cp866 and the console is not guaranteed to be.
    #
    # The expectation follows the DOCUMENTED contract (decode_console_output): when the
    # console page is UTF-8 (chcp 65001, or another process on this shared console moved it
    # — tests/install/test_install_scripts.py does, and xdist workers share one console)
    # and the bytes are not valid UTF-8, the OEM page is the only candidate. Expecting
    # `console_encoding()` alone turned that legitimate state into a failure: measured 4 of
    # 25 runs when this test ran beside the install suite, 0 of 25 alone.
    out = decode_console_result(subprocess.CompletedProcess([], 0, OEM_GROUP_LINE, b""))
    from navig.core.proc_text import oem_encoding

    enc = console_encoding()
    if enc == "utf-8":
        enc = oem_encoding()
    assert out.stdout == OEM_GROUP_LINE.decode(enc, errors="replace")


def test_decode_console_result_tolerates_uncaptured_streams() -> None:
    """capture_output=False leaves both None; callers still index them."""
    from navig.core.proc_text import decode_console_result

    out = decode_console_result(subprocess.CompletedProcess([], 0, None, None))
    assert out.stdout == "" and out.stderr == ""


def test_console_encoding_is_cached() -> None:
    """It is consulted once per subprocess call; the ctypes lookup must not repeat.

    The public function is a thin wrapper so the cache can be keyed by platform; the
    cached call is `_console_encoding_for(sys.platform)`.
    """
    from navig.core.proc_text import _console_encoding_for

    console_encoding()
    hits_before = _console_encoding_for.cache_info().hits
    console_encoding()
    assert _console_encoding_for.cache_info().hits == hits_before + 1


def test_module_imports_ctypes_lazily() -> None:
    """`navig help` budget: importing this module must not drag ctypes in on POSIX."""
    source = (
        __import__("pathlib").Path(sys.modules["navig.core.proc_text"].__file__)
    ).read_text(encoding="utf-8")
    import_lines = [
        ln.strip() for ln in source.splitlines()
        if ln.startswith("import ") or ln.startswith("from ")
    ]
    assert not any("ctypes" in ln for ln in import_lines), import_lines


def test_decode_console_result_accepts_a_duck_typed_stand_in() -> None:
    """It must not demand attributes it does not use.

    Call sites are wrapped around `subprocess.run(...)`, and a large number of tests fake
    that with a bare `SimpleNamespace(returncode=0)`. Requiring `args` broke eight of them
    and requiring `stdout` three more — an AttributeError deep inside a decode helper, for
    fields it only passes through. Missing streams decode to "" exactly as an uncaptured
    stream does, so the result keeps its shape either way.
    """
    from types import SimpleNamespace

    from navig.core.proc_text import decode_console_result

    out = decode_console_result(SimpleNamespace(returncode=3))
    assert out.returncode == 3
    assert out.stdout == "" and out.stderr == ""
    assert out.args is None

    partial = decode_console_result(SimpleNamespace(returncode=0, stdout=b"hi"))
    assert partial.stdout == "hi" and partial.stderr == ""


# ── a UTF-8 console does not make a native tool write UTF-8 (2026-09-21) ─────
#
# Found through the gate: `install.ps1 -DryRun` sets `[Console]::OutputEncoding = UTF8`,
# which is `SetConsoleOutputCP(65001)` on the ONE console every xdist worker shares — and
# it stays. A later worker saw GetConsoleOutputCP() == 65001, cached "utf-8", and decoded
# `icacls C:\Windows` — which still wrote cp866, console page or not — into U+FFFD. The
# same thing happens in production to anyone who ran `chcp 65001` for a nicer terminal.


def test_under_a_utf8_console_non_utf8_bytes_fall_back_to_the_oem_page(monkeypatch) -> None:
    from navig.core import proc_text

    monkeypatch.setattr(proc_text, "console_encoding", lambda: "utf-8")
    monkeypatch.setattr(proc_text, "oem_encoding", lambda: "cp866")
    assert proc_text.decode_console_output(OEM_GROUP_LINE) == EXPECTED
    assert "\ufffd" not in proc_text.decode_console_output(OEM_GROUP_LINE)


def test_under_a_utf8_console_valid_utf8_is_still_utf8(monkeypatch) -> None:
    from navig.core import proc_text

    monkeypatch.setattr(proc_text, "console_encoding", lambda: "utf-8")
    monkeypatch.setattr(proc_text, "oem_encoding", lambda: "cp866")
    assert proc_text.decode_console_output("Администраторы".encode()) == "Администраторы"


@pytest.mark.skipif(os.name != "nt", reason="OEM code pages are a Windows concept")
def test_oem_encoding_is_a_real_codec_and_is_the_oem_page() -> None:
    import codecs
    import ctypes

    from navig.core.proc_text import oem_encoding

    enc = oem_encoding()
    codecs.lookup(enc)
    live = ctypes.windll.kernel32.GetOEMCP()
    assert enc == f"cp{live}" or enc == "oem", (enc, live)


def test_posix_oem_encoding_is_utf8(monkeypatch) -> None:
    from navig.core import proc_text

    monkeypatch.setattr(proc_text.sys, "platform", "linux")
    assert proc_text.oem_encoding() == "utf-8"
