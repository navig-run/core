"""PowerShell that NAVIG puts on a user's disk must carry a UTF-8 BOM when it has non-ASCII.

This is the exact opposite of :mod:`test_installer_no_bom`, and both are right — the rule
follows how the file reaches its interpreter:

    piped   (`irm … | iex`, `curl … | bash`)   ->  NO BOM. The BOM survives HTTP decoding
                                                   into the string and `iex` fails on it.
    on disk (`.\\install_navig_windows.ps1`)    ->  BOM REQUIRED. Windows PowerShell 5.1
                                                   reads a BOM-less UTF-8 .ps1 as the ANSI
                                                   codepage and mangles every non-ASCII char.

The surface is everything NAVIG ships: `core/navig/**` (the wheel packages it via
`package-data navig = ["**/*"]`) and `core/installers/**` (whose README opens "the scripts
*you* run"). Nothing in either is ever piped - checked, no `irm`/`iwr`/`curl` reference to any
of them anywhere in the repo - so every one is read from disk, where a BOM is required.

WHAT THIS WAS HIDING. Seven of these failed to parse at all under 5.1 — the default Windows
shell — including `navig-statusbar.psm1`, which the README says the Windows installers
*import*. A char whose UTF-8 bytes contain 0x94/0x93 decodes to `”`/`“` under ANSI, and
PowerShell honours smart quotes as STRING DELIMITERS, so one box-drawing character inside a
string ends it early and the parse cascades:

    install_navig_windows.ps1          10 parse errors
    mount_remote_drives.ps1             5
    install_navig_windows_enhanced.ps1  4
    navig_windows_remote_deploy.ps1     4
    navig-statusbar.psm1                4
    fix_windows_network_sharing.ps1     3
    navig_quick_setup.ps1               2

A BOM fixes all seven with zero content change (verified: 0 parse errors afterwards), and
also fixes the cosmetic mojibake in the three that parsed but render glyphs wrong
(`navig-icons.psm1` alone has 272 non-ASCII characters — it exists to draw them).

The two pure-ASCII scripts deliberately have no BOM: nothing to mis-decode, so adding one
would be noise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: Every tree whose PowerShell NAVIG puts on a user's disk. Not "every .ps1 in the repo":
#:
#:   core/navig/    — the PACKAGED tree. pyproject declares package-data `navig = ["**/*"]`,
#:                    so every file under it ships in the wheel to every user.
#:   core/installers/ — the documented user-run surface ("the scripts *you* run", its README).
#:
#: Deliberately OUT, each for a reason rather than an oversight:
#:   registry/      — a MIRROR of the upstream navig-run/community repo. Editing it here would
#:                    not reach users and would diverge from upstream; it belongs there.
#:   scripts/, core/tools/ — maintainer tooling, excluded from every published artifact.
#:   apps/*/scripts, .github/ — build and CI tooling, run by us on machines with pwsh 7.
#:   tools/forge/.lab/ — vendored reference corpus, never shipped.
#:
#: This started life scoped to `core/installers/` alone, which missed three files the wheel
#: ships — two of them (`mesh/bootstrap/*.ps1`) dead on 5.1. Scope follows what a file IS,
#: not the folder it happens to sit in.
SHIPPED_ROOTS = ("core/navig", "core/installers")

_UTF8_BOM = b"\xef\xbb\xbf"

#: Below this, the scan found nothing and would pass vacuously — the failure mode that let a
#: sibling guard read 1898 of 6429 files while printing green (eab560145).
_MIN_SCRIPTS = 12


def _powershell_files() -> list[Path]:
    return sorted(
        p
        for root in SHIPPED_ROOTS
        for p in (REPO / root).rglob("*")
        if p.is_file() and p.suffix.lower() in {".ps1", ".psm1"}
    )


def test_the_scan_finds_the_shipped_scripts() -> None:
    """Anti-vacuity: an empty scan is green and means nothing."""
    found = _powershell_files()
    assert len(found) >= _MIN_SCRIPTS, (
        f"found only {len(found)} PowerShell file(s) under {list(SHIPPED_ROOTS)} — a tree "
        f"moved or was emptied, so this guard is checking nothing."
    )


@pytest.mark.parametrize("path", _powershell_files(), ids=lambda p: p.name)
def test_non_ascii_shipped_script_has_a_bom(path: Path) -> None:
    raw = path.read_bytes()
    if not any(b > 127 for b in raw):
        pytest.skip("pure ASCII — nothing to mis-decode, so a BOM would be noise")
    assert raw.startswith(_UTF8_BOM), (
        f"{path.relative_to(REPO).as_posix()} contains non-ASCII but has no UTF-8 BOM.\n\n"
        "NAVIG ships this file and it is read FROM DISK — either from the wheel "
        "(core/navig/**) or from a clone (core/installers/**). Windows PowerShell 5.1 reads "
        "a BOM-less UTF-8 .ps1 as the ANSI codepage, so "
        "every box-drawing character and glyph is mangled — and because a mis-decoded byte "
        "0x94 becomes `”`, which PowerShell treats as a string delimiter, that usually "
        "means the file no longer PARSES at all.\n\n"
        "Save it as UTF-8 with BOM. Note this is the opposite of the rule for the piped "
        "installers (test_installer_no_bom.py) — there a BOM breaks `irm | iex`. The rule "
        "follows how the file reaches its interpreter."
    )


def test_the_two_rules_do_not_overlap() -> None:
    """No file may be subject to both rules, because they demand opposite things.

    If a script here ever becomes piped (or a piped one moves into this directory), the two
    guards would deadlock — one requiring a BOM and the other forbidding it. Better to fail
    here, with an explanation, than to have someone chase a contradictory pair of failures.
    """
    from tests.packaging.test_installer_no_bom import PIPED_INSTALLERS

    piped = {(REPO / rel).resolve() for rel in PIPED_INSTALLERS}
    overlap = sorted(p.name for p in _powershell_files() if p.resolve() in piped)
    assert not overlap, (
        f"{overlap} is covered by BOTH the piped-installer rule (no BOM) and the from-disk "
        "rule (BOM required). Those cannot both hold — decide how the file is consumed and "
        "list it in exactly one place."
    )
