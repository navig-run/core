"""The piped-install scripts must never carry a UTF-8 BOM.

`irm https://navig.run/install.ps1 | iex` and `curl … | bash` are NAVIG's documented install
commands. Both hand the script to an interpreter as *content*, and a BOM at the front of that
content is fatal on both platforms — for different reasons:

  * **PowerShell.** `irm` decodes the HTTP response, so the BOM survives into the string as
    U+FEFF and `iex` tries to run it. Measured on Windows PowerShell 5.1 with a BOM added to
    the real installer:  `The term '?#' is not recognized as the name of a cmdlet…`
  * **sh.** A BOM sits *before* `#!`, so the shebang is no longer at byte 0. Measured:
    `install.sh: line 1: ﻿#!/bin/sh: No such file or directory`.

This guard exists because the obvious "fix" for a *different* problem is to add one, and that
trade is strictly bad. Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI, which used
to break running a DOWNLOADED copy from disk (`.\\install.ps1`) — also a documented usage.
Adding a BOM does fix that, and breaks the path everyone actually uses. Both measured:

    BOM + run-from-disk under 5.1 : OK
    BOM + irm|iex     under 5.1 : FAILED   ("The term '?#' is not recognized")

The from-disk break was fixed the correct way instead — by removing the three characters that
actually caused it (see the second half of this file) — so BOTH paths now work on 5.1 and the
BOM stays off. `pwsh` 7+ reads BOM-less UTF-8 correctly and was never affected either way.

Scoped as an explicit list rather than "every file named install.*": the property that matters
is *"this file is piped into an interpreter"*, which is not visible in a filename.
`scripts/install.ps1` and the space-local installers are run from disk, where a BOM would
HELP — banning it there would be wrong. The list is kept honest by
:func:`test_every_listed_installer_exists` below, so it cannot rot into a rubber stamp.

That reasoning is still right, and it is not sufficient on its own: a list only covers what
someone remembered to list, and `plugins/navig-mini/install.sh` — piped to `sh` by the
command its README documents — was missing from it and shipped with a BOM. This file knew
the failure by heart and was pointed four files away from it. So the list is paired with
``tests/quality/test_no_bom_before_shebang.py``, which derives its scope from the SHAPE of
the file (does a `#!` line follow a BOM?) rather than from a path, and therefore covers the
installer nobody adds here. The two check different properties and both are wanted: only
the list can say "this .ps1 must HAVE a BOM" or "this file must still exist".
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: The scripts served for `irm | iex` / `curl | bash`, source and published copy.
PIPED_INSTALLERS = (
    "core/install.ps1",
    "core/install.sh",
    "web/www/public/install.ps1",
    "web/www/public/install.sh",
    # navig-mini's installer, served at get.navig.run/mini and piped to `sh` by the command
    # its README documents. It was NOT on this list and shipped with a BOM for exactly as
    # long as it existed -- this file described that failure precisely while watching four
    # files, and a fifth piped installer went out broken beside it. Adding a piped installer
    # anywhere in the tree means adding it here; `test_no_bom_before_shebang.py` is the
    # tree-wide floor that catches the one someone forgets.
    "plugins/navig-mini/install.sh",
)

_UTF8_BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize("rel", PIPED_INSTALLERS)
def test_installer_has_no_utf8_bom(rel: str) -> None:
    path = REPO / rel
    head = path.read_bytes()[:3]
    assert head != _UTF8_BOM, (
        f"{rel} starts with a UTF-8 BOM.\n\n"
        "That breaks the documented install command: `iex` receives the BOM as a literal "
        "U+FEFF and fails with \"The term '?#' is not recognized\"; for install.sh the BOM "
        "pushes `#!` off byte 0 and the shebang stops working.\n\n"
        "If you added it to make a DOWNLOADED copy run under Windows PowerShell 5.1, that "
        "trade is backwards — it fixes the rare path by breaking the common one. Make the "
        "script ASCII-only instead, or require pwsh 7."
    )


@pytest.mark.parametrize("rel", [p for p in PIPED_INSTALLERS if p.endswith(".sh")])
def test_shell_installer_starts_with_its_shebang(rel: str) -> None:
    """The behaviour the BOM check protects, asserted directly.

    A byte-0 shebang is the actual requirement; "no BOM" is one way to violate it. Pinning
    the requirement means any other leading-byte mistake (a stray blank line, a stripped
    shebang) is caught too.
    """
    assert (REPO / rel).read_bytes().startswith(b"#!"), (
        f"{rel} does not begin with `#!` at byte 0, so `curl … | bash` has no interpreter line"
    )


@pytest.mark.parametrize("rel", [p for p in PIPED_INSTALLERS if p.endswith(".sh")])
def test_piped_shell_installers_have_no_carriage_returns(rel: str) -> None:
    r"""A CR in a piped `.sh` is the same failure as a BOM, one line further in.

    Measured against the live URL on GNU bash 5.3.9, `https://navig.run/install.sh` was
    36,317 bytes of CRLF and did not survive parsing::

        install.sh: line 28: syntax error near unexpected token `$'{\r''

    -- so `curl -fsSL https://navig.run/install.sh | bash`, the documented install command,
    failed for every Linux and macOS user before executing a single line.

    Nothing in the repo was wrong, which is why no check saw it. The committed blob was LF
    and byte-identical to `core/install.sh`; the CR was injected at CHECKOUT by git's Windows
    default (`core.autocrlf=true`), because `core/.gitattributes` pinned `core/` and nothing
    governed anywhere else. `next build` then copied `web/www/public/` verbatim and wrangler
    uploaded those bytes -- so the artifact was broken when deployed from Windows and fine
    when deployed from Linux.

    This reads the WORKING TREE on purpose. The index is not what ships; the file on disk is.
    """
    raw = (REPO / rel).read_bytes()
    assert b"\r" not in raw, (
        f"{rel} contains carriage returns in the working tree. This is the copy a build "
        "publishes, and real bash rejects a CRLF script with a syntax error on its first "
        "brace -- before running anything.\n\n"
        "It is almost certainly a CHECKOUT artifact rather than an edit: git on Windows "
        "converts LF to CRLF unless a .gitattributes says otherwise. Re-materialise the "
        f"file instead of editing it:\n\n    rm {rel} && git checkout -- {rel}\n\n"
        "If the CRs come back, the root .gitattributes no longer pins this path -- add it "
        "there, next to the other piped installers."
    )


def test_piped_shell_installers_are_pinned_to_lf() -> None:
    """The BYTES are only correct until the next checkout; the attribute is what keeps them.

    Checking bytes alone passes on a machine that happens to be Linux, or right after someone
    normalised by hand, and goes on passing until the next clone re-breaks it. Assert the
    mechanism as well as its current result.
    """
    import subprocess  # noqa: PLC0415 - only this test shells out

    shells = [p for p in PIPED_INSTALLERS if p.endswith(".sh")]
    proc = subprocess.run(
        ["git", "check-attr", "eol", "--", *shells],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:  # no git, or not a checkout -- do not invent a verdict
        pytest.skip(f"git check-attr unavailable: {proc.stderr.strip() or proc.returncode}")

    unpinned = [
        line.rsplit(":", 2)[0]
        for line in proc.stdout.splitlines()
        if line.strip() and not line.rstrip().endswith(": lf")
    ]
    assert not unpinned, (
        f"these piped shell installers have no `eol=lf` attribute: {unpinned}.\n\n"
        "Without it a Windows checkout writes CRLF and a build publishes those bytes -- "
        "which is exactly how navig.run/install.sh came to serve a script bash refuses to "
        "parse. Add the path to the root .gitattributes beside the others."
    )


#: canonical -> the copy `web/www` serves at navig.run. Same script, two places in one repo.
PUBLISHED_COPIES = (
    ("core/install.sh", "web/www/public/install.sh"),
    ("core/install.ps1", "web/www/public/install.ps1"),
)


@pytest.mark.parametrize(("canonical", "published"), PUBLISHED_COPIES)
def test_published_installer_matches_its_canonical_source(canonical: str, published: str) -> None:
    """`web/www/public/` is a COPY, and nothing local compared it to the original.

    `scripts/published-installer.mjs` documents three copies and checks one hop of the
    chain -- canonical against the PUBLIC REPO. The third copy is the one navig.run
    actually serves, it lives in this repo, and the workflow that was supposed to sync it
    is dead by location (it sits under `web/www/.github/workflows/`, a subdirectory, and
    GitHub only runs workflows at a repository root). So a fix to `core/install.sh` could
    land, pass every check, and leave the served file on the old content -- which is
    exactly how the published copy kept a machine-wide `pkill -f 'navig'` for weeks after
    it was fixed here.

    This is the cheap half of that problem and the half a diff can actually fix: both
    files are in this repo, so "did you update both" is answerable at commit time.

    Compared with carriage returns removed, not byte-for-byte. Only the `.sh` copies are
    pinned to LF (a CR there is an outage); the `.ps1` pair is deliberately left to the
    platform, so their raw bytes differ by exactly one per line while the SCRIPT is the
    same. Comparing raw bytes would fail for a difference that does not exist.
    """
    a = (REPO / canonical).read_bytes().replace(b"\r", b"")
    b = (REPO / published).read_bytes().replace(b"\r", b"")
    assert a == b, (
        f"{published} has drifted from {canonical}.\n\n"
        "That file is what navig.run serves, so the fix you just made is not the script "
        "users download. The sync workflow for this hop does not run -- update the copy in "
        f"the same change:\n\n    cp {canonical} {published}\n\n"
        "(then re-materialise it if your checkout injects CRs: "
        f"rm {published} && git checkout -- {published})"
    )


def test_every_listed_installer_exists() -> None:
    """Anti-vacuity: a parametrised test over paths that moved would silently pass nothing.

    This is the failure mode that made a sibling guard read 1898 of 6429 files while printing
    a green tick (eab560145) — a hardcoded path list in a repo that moves paths.
    """
    missing = [rel for rel in PIPED_INSTALLERS if not (REPO / rel).is_file()]
    assert not missing, (
        f"listed piped-install scripts do not exist: {missing}. They moved or were renamed, "
        "so this guard has been checking less than it claims — follow them."
    )


# ─────────────────────────────────────────────────────────────────────────────
# The OTHER half of the same encoding trap, and the reason a BOM is not the fix.
#
# Windows PowerShell 5.1 reads a BOM-less .ps1 from disk as the ANSI codepage. A UTF-8
# char whose bytes include 0x94 or 0x93 then decodes to `”` / `“` — and PowerShell honours
# smart quotes as STRING DELIMITERS. So one em dash inside a double-quoted string ends the
# string early, the rest of the line becomes code, and the file stops parsing:
#
#     Write-NavHint "fzf optional — install manually: …"     (install.ps1, 3 sites)
#     -> ANSI: 6 parse errors -> `.\install.ps1` was dead on 5.1, a DOCUMENTED usage
#        ("Windows (PowerShell 5.1+)" ... ".\install.ps1 [-Version …]")
#
# Replacing those three em dashes with `-` fixed it (ANSI parse errors 6 -> 0) and left
# every box-drawing character in place: a `─` in a COMMENT is harmless, because a comment
# runs to end of line no matter what it decodes to.

_MANGLES_TO_A_QUOTE = "\u201c\u201d"


def _code_part(line: str) -> str:
    """*line* with any trailing comment removed, quote-aware.

    A plain `line.split("#")` would drop half of `"a # b"`, and treating the whole line as
    code flags the trailing `# ─` legends next to the glyph table — 4 false positives on
    install.ps1, all provably harmless. Only a `#` outside quotes starts a comment.
    """
    out: list[str] = []
    in_single = in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_double and ch == "`":  # backtick escape inside "..."
            out.append(ch)
            i += 1
            if i < len(line):
                out.append(line[i])
                i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
        i += 1
    return "".join(out)


@pytest.mark.parametrize("rel", [p for p in PIPED_INSTALLERS if p.endswith(".ps1")])
def test_no_quote_mangling_char_outside_comments(rel: str) -> None:
    """Only .ps1: bash is byte-transparent, so install.sh is unaffected by all of this."""
    offenders = []
    for lineno, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
        for ch in _code_part(line):
            if ord(ch) < 128:
                continue
            if any(q in ch.encode("utf-8").decode("cp1252", "replace") for q in _MANGLES_TO_A_QUOTE):
                offenders.append(f"  line {lineno}: {ch!r} in {line.strip()[:70]}")
                break
    assert not offenders, (
        f"{rel} has a non-ASCII character outside a comment whose ANSI mis-decoding yields a "
        "PowerShell smart quote, which terminates the string early and breaks the parse when "
        "the file is run from disk under Windows PowerShell 5.1:\n"
        + "\n".join(offenders)
        + "\n\nUse an ASCII equivalent ('-' for an em dash). Do NOT add a BOM to fix it — see "
        "test_installer_has_no_utf8_bom; that breaks `irm | iex`, which is the common path."
    )


def test_the_scanner_sees_a_string_but_not_a_comment() -> None:
    """Anti-vacuity for the scanner itself — it is the whole precision of the test above.

    Both directions, because getting either wrong is silent: treating comments as code
    cries wolf on the glyph legends, and treating code as comments misses the real bug.
    """
    assert "\u2014" in _code_part('Write-Host "fzf optional \u2014 install"')
    assert "\u2014" not in _code_part('$x = 1    # dash \u2014 here')
    assert "\u2014" not in _code_part('# leading comment \u2014 here')
    # a `#` inside a string must not be mistaken for a comment
    assert _code_part('Write-Host "a # b"').endswith('"a # b"')
