"""Console-tool output must be decoded with the console code page, not the ANSI one.

The fixture bytes below are a real ``whoami /groups`` fragment captured on this machine
(Windows 11, GetACP=1251, GetOEMCP=GetConsoleOutputCP=866). They are the whole point of the
module: the same bytes decode three different ways, and only one of them is the group name
Windows actually reported.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from navig.core.proc_text import console_encoding, decode_console_output

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
    """
    enc = console_encoding()
    assert decode_console_output(OEM_GROUP_LINE) == OEM_GROUP_LINE.decode(
        enc, errors="replace"
    ), f"decode did not use console_encoding() (it reported {enc!r})"
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


@pytest.mark.skipif(os.name != "nt", reason="Windows branch")
def test_windows_matches_the_live_console_code_page() -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    live = kernel32.GetConsoleOutputCP() or kernel32.GetOEMCP()
    expected = "utf-8" if live == 65001 else f"cp{live}"
    assert console_encoding() in (expected, "oem")


@pytest.mark.skipif(os.name != "nt", reason="needs a Windows console tool")
def test_end_to_end_against_a_real_console_tool() -> None:
    """The whole class in one assertion: ANSI mode and console mode disagree, and the
    console-mode read is the one that round-trips back to the bytes the tool emitted."""
    argv = ["whoami", "/groups"]
    try:
        raw = subprocess.run(argv, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - tool absent
        pytest.skip("whoami unavailable")
    if not any(b > 127 for b in raw):
        pytest.skip("this Windows install reports ASCII-only group names")

    decoded = decode_console_output(raw)
    assert decoded.encode(console_encoding(), errors="replace") == raw

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
    out = decode_console_result(subprocess.CompletedProcess([], 0, OEM_GROUP_LINE, b""))
    assert out.stdout == OEM_GROUP_LINE.decode(console_encoding(), errors="replace")


def test_decode_console_result_tolerates_uncaptured_streams() -> None:
    """capture_output=False leaves both None; callers still index them."""
    from navig.core.proc_text import decode_console_result

    out = decode_console_result(subprocess.CompletedProcess([], 0, None, None))
    assert out.stdout == "" and out.stderr == ""


def test_console_encoding_is_cached() -> None:
    """It is consulted once per subprocess call; the ctypes lookup must not repeat."""
    console_encoding()
    hits_before = console_encoding.cache_info().hits
    console_encoding()
    assert console_encoding.cache_info().hits == hits_before + 1


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
