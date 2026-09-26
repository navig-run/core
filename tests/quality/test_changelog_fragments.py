"""`core/changelog.d/` fragments: every one on disk is valid, and the assembler is exact.

Fragments exist so that two changes never touch the same changelog line (see
core/changelog.d/README.md). That only holds if the assembler that folds them in is
deterministic, idempotent and creates sections in the canonical order — and if a
malformed fragment is refused at the gate rather than folded in as garbage. Both halves
are pinned here; the tool is stdlib-only and imported by path, like the other core/tools.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[2]
_TOOL = _CORE / "tools" / "changelog_assemble.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("changelog_assemble", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve `from __future__ import annotations` strings through
    # sys.modules[cls.__module__]; a path-loaded module must be registered first.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


BASE = """# Changelog

## [Unreleased]

<!-- header comment -->

### Added
- **Existing added entry.** stays first-of-old

### Fixed
- **Existing fix.** unchanged

## [3.25.0] — 2026-09-03

### Added
- **old release entry.**
"""


def _core(tmp_path: Path, frags: dict[str, str], changelog: str = BASE) -> Path:
    core = tmp_path / "core"
    (core / "changelog.d").mkdir(parents=True)
    (core / "changelog.d" / "README.md").write_text("# readme\n", encoding="utf-8")
    (core / "CHANGELOG.md").write_text(changelog, encoding="utf-8", newline="\n")
    for name, body in frags.items():
        (core / "changelog.d" / name).write_text(body, encoding="utf-8", newline="\n")
    return core


# ── the real directory ───────────────────────────────────────────────────────


def test_every_fragment_on_disk_is_valid(tool) -> None:
    frags, problems = tool.load_fragments(_CORE)
    assert not problems, "\n".join(f"{p.path.name}: {p.why}" for p in problems)
    assert (_CORE / "changelog.d" / "README.md").is_file(), "the README is the contract"
    for f in frags:
        assert f.kind in tool.KINDS


def test_the_real_changelog_still_assembles(tool) -> None:
    """The live file's Unreleased block must be one the assembler can address."""
    text = (_CORE / "CHANGELOG.md").read_text(encoding="utf-8")
    start, end = tool._unreleased_span(text)
    assert "### " in text[start:end]


# ── assembling ───────────────────────────────────────────────────────────────


def test_fragments_fold_into_their_sections_newest_first_and_are_deleted(tool, tmp_path) -> None:
    core = _core(tmp_path, {
        "zed-thing.added.md": "- **Zed.** z\n",
        "alpha-thing.added.md": "- **Alpha.** a\n  continued\n",
        "boom.fixed.md": "- **Boom fixed.** b\n",
    })
    rc = tool.main(["--core", str(core)])
    assert rc == 0
    out = (core / "CHANGELOG.md").read_text(encoding="utf-8")
    added = out.index("### Added")
    fixed = out.index("### Fixed")
    release = out.index("## [3.25.0]")
    # ordered by slug, above the pre-existing entry, inside Unreleased only
    assert added < out.index("- **Alpha.**") < out.index("- **Zed.**") < out.index("- **Existing added entry.**") < fixed
    assert fixed < out.index("- **Boom fixed.**") < out.index("- **Existing fix.**") < release
    assert out.count("- **old release entry.**") == 1 and out.index("- **old release entry.**") > release
    left = sorted(p.name for p in (core / "changelog.d").iterdir())
    assert left == ["README.md"], "fragments are consumed"


def test_a_missing_section_is_created_in_keep_a_changelog_order(tool, tmp_path) -> None:
    core = _core(tmp_path, {
        "sec.security.md": "- **Sec.** s\n",
        "chg.changed.md": "- **Chg.** c\n",
        "rem.removed.md": "- **Rem.** r\n",
    })
    assert tool.main(["--core", str(core)]) == 0
    out = (core / "CHANGELOG.md").read_text(encoding="utf-8")
    block = out[out.index("## [Unreleased]"): out.index("## [3.25.0]")]
    order = [h for h in ("### Added", "### Changed", "### Removed", "### Fixed", "### Security") if h in block]
    assert order == ["### Added", "### Changed", "### Removed", "### Fixed", "### Security"]
    assert block.count("### Changed") == 1 and "- **Chg.** c" in block


def test_assembly_is_idempotent_and_a_no_op_without_fragments(tool, tmp_path) -> None:
    core = _core(tmp_path, {"one.added.md": "- **One.** 1\n"})
    assert tool.main(["--core", str(core)]) == 0
    first = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core)]) == 0
    assert (core / "CHANGELOG.md").read_bytes() == first, "a second run must not touch the file"
    assert tool.assemble_text(first.decode("utf-8"), []) == first.decode("utf-8")


def test_dry_run_and_check_write_nothing(tool, tmp_path) -> None:
    core = _core(tmp_path, {"one.added.md": "- **One.** 1\n"})
    before = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core), "--dry-run"]) == 0
    assert tool.main(["--core", str(core), "--check"]) == 0
    assert (core / "CHANGELOG.md").read_bytes() == before
    assert (core / "changelog.d" / "one.added.md").exists()


def test_crlf_in_a_fragment_or_the_changelog_never_reaches_the_output(tool, tmp_path) -> None:
    core = _core(tmp_path, {"win.fixed.md": "- **Win.** w\r\n  more\r\n"}, changelog=BASE.replace("\n", "\r\n"))
    assert tool.main(["--core", str(core)]) == 0
    assert b"\r" not in (core / "CHANGELOG.md").read_bytes()


# ── refusing malformed fragments ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "body", "why"),
    [
        ("Bad_Name.added.md", "- **x.**\n", "name must be"),
        ("thing.md", "- **x.**\n", "name must be"),
        ("thing.bugfix.md", "- **x.**\n", "not one of"),
        ("thing.fixed.md", "\n\n", "empty"),
        ("thing.fixed.md", "Plain prose\n", "must start with"),
        ("thing.fixed.md", "- **x.**\nunindented continuation\n", "indented two spaces"),
        ("thing.fixed.md", "- **x.**\n<<<<<<< HEAD\n", "conflict marker"),
    ],
)
def test_check_refuses_a_malformed_fragment_and_names_why(tool, tmp_path, name, body, why) -> None:
    core = _core(tmp_path, {name: body})
    before = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core), "--check"]) == 1
    assert tool.main(["--core", str(core)]) == 1, "an invalid set is never partially folded"
    assert (core / "CHANGELOG.md").read_bytes() == before
    _, problems = tool.load_fragments(core)
    assert problems and why in problems[0].why


def test_a_changelog_without_an_unreleased_heading_is_refused_not_mangled(tool, tmp_path) -> None:
    core = _core(tmp_path, {"one.added.md": "- **One.** 1\n"}, changelog="# Changelog\n\n## [1.0.0]\n")
    before = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core)]) == 1
    assert (core / "CHANGELOG.md").read_bytes() == before
    assert (core / "changelog.d" / "one.added.md").exists()


# ── release rotation ─────────────────────────────────────────────────────────
#
# The 3.25.0 release rotated [Unreleased] under the version heading BY HAND in the release
# commit; version_bump.py touched only the manifests. --release is that rotation as a tool,
# and these pin the shape the hand edit had: fragments folded first, every section moved
# under "## [X.Y.Z] — date" (the em dash byte-identical to the existing headings), a fresh
# [Unreleased] whose `git log v<X.Y.Z>..HEAD` hint names the version just released, the
# older releases untouched — and the two refusals that keep the heading honest.

EM_DASH = "—"


def _released(tool, tmp_path, frags, changelog=BASE, version="3.26.0", date="2026-09-19"):
    core = _core(tmp_path, frags, changelog=changelog)
    rc = tool.main(["--core", str(core), "--release", version, "--date", date])
    return core, rc, (core / "CHANGELOG.md").read_text(encoding="utf-8")


def test_release_folds_fragments_then_rotates_unreleased_under_the_version(tool, tmp_path) -> None:
    core, rc, out = _released(tool, tmp_path, {"late.fixed.md": "- **Late fix.** l\n"})
    assert rc == 0
    heading = f"## [3.26.0] {EM_DASH} 2026-09-19"
    assert heading in out
    unreleased = out.index("## [Unreleased]")
    released = out.index(heading)
    old = out.index("## [3.25.0]")
    assert unreleased < released < old, "newest first: fresh [Unreleased], then the release, then history"
    fresh = out[unreleased:released]
    assert "### " not in fresh, "the fresh [Unreleased] carries no sections"
    assert "git log v3.26.0..HEAD" in fresh, "the template hint names the version just released"
    block = out[released:old]
    assert "- **Late fix.** l" in block and "- **Existing added entry.**" in block
    assert "- **Existing fix.** unchanged" in block
    assert out[old:].count("- **old release entry.**") == 1, "history is untouched"
    assert sorted(p.name for p in (core / "changelog.d").iterdir()) == ["README.md"]


def test_release_is_idempotent(tool, tmp_path) -> None:
    core, rc, first = _released(tool, tmp_path, {"one.added.md": "- **One.** 1\n"})
    assert rc == 0
    rc2 = tool.main(["--core", str(core), "--release", "3.26.0", "--date", "2026-09-19"])
    assert rc2 == 0, "a re-run with an empty [Unreleased] is a no-op, not an error"
    assert (core / "CHANGELOG.md").read_text(encoding="utf-8") == first


def test_release_refuses_an_empty_release(tool, tmp_path) -> None:
    empty = "# C\n\n## [Unreleased]\n\n<!-- t -->\n\n## [3.25.0] " + EM_DASH + " 2026-09-03\n\n### Added\n- **old.**\n"
    core, rc, out = _released(tool, tmp_path, {}, changelog=empty)
    assert rc == 1
    assert "## [3.26.0]" not in out, "a version heading over nothing is a lie"


def test_release_refuses_a_version_already_present_while_unreleased_has_entries(tool, tmp_path) -> None:
    core, rc, out = _released(tool, tmp_path, {"one.added.md": "- **One.** 1\n"}, version="3.25.0")
    assert rc == 1
    assert out.count("## [3.25.0]") == 1
    assert (core / "changelog.d" / "one.added.md").exists(), "refused means nothing consumed"


@pytest.mark.parametrize("bad", ["3.26", "v3.26.0", "3.26.0-rc1", ""])
def test_release_refuses_a_malformed_version(tool, tmp_path, bad) -> None:
    core, rc, out = _released(tool, tmp_path, {"one.added.md": "- **One.** 1\n"}, version=bad)
    assert rc != 0 and "## [" + bad + "]" not in out


def test_release_dry_run_writes_nothing(tool, tmp_path) -> None:
    core = _core(tmp_path, {"one.added.md": "- **One.** 1\n"})
    before = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core), "--release", "3.26.0", "--date", "2026-09-19", "--dry-run"]) == 0
    assert (core / "CHANGELOG.md").read_bytes() == before
    assert (core / "changelog.d" / "one.added.md").exists()


def test_release_refuses_a_malformed_fragment_before_touching_anything(tool, tmp_path) -> None:
    core = _core(tmp_path, {"good.added.md": "- **Good.** g\n", "bad.bugfix.md": "- **Bad.** b\n"})
    before = (core / "CHANGELOG.md").read_bytes()
    assert tool.main(["--core", str(core), "--release", "3.26.0", "--date", "2026-09-19"]) == 1
    assert (core / "CHANGELOG.md").read_bytes() == before
    assert (core / "changelog.d" / "good.added.md").exists()


def test_the_live_changelog_can_be_rotated(tool, tmp_path) -> None:
    """The real file's [Unreleased] must be one --release can address, today — with a
    version the file does not already hold, against a COPY."""
    live = (_CORE / "CHANGELOG.md").read_text(encoding="utf-8")
    text, rotated = tool.release_text(live, "999.0.0", "2026-01-01")
    assert rotated
    assert text.index("## [999.0.0]") < text.index("## [3.25.0]")


# ── the GitHub Release body: one writer, never over GitHub's limit ───────────
#
# tools/release.sh and .github/workflows/release.yml used to compose the release body
# separately (a raw changelog block vs an install snippet + auto notes). Both now print
# `--release-notes`. GitHub refuses a body over 125,000 characters — 3.25.0's block is
# 288 KB — so an oversize block becomes its headline digest with a link to the full notes.

RELEASED = (
    "# C\n\n## [Unreleased]\n\n<!-- t -->\n\n"
    f"## [3.26.0] {EM_DASH} 2026-09-19\n\n### Added\n- **Alpha shipped.** long detail\n  more\n"
    "- **Beta shipped.** d\n\n### Fixed\n- **Gamma fixed.** d\n\n"
    f"## [3.25.0] {EM_DASH} 2026-09-03\n\n### Added\n- **old.**\n"
)


def test_release_notes_are_the_install_block_plus_the_full_section_when_it_fits(tool) -> None:
    body = tool.release_notes(RELEASED, "3.26.0")
    assert body.startswith("## Install / Update\n")
    assert "pip install navig==3.26.0" in body
    assert body.index("## What changed") < body.index("### Added") < body.index("- **Alpha shipped.** long detail\n  more")
    assert "- **old.**" not in body, "only this version's block"
    assert "headlines only" not in body


def test_release_notes_fall_back_to_the_headline_digest_when_over_the_limit(tool) -> None:
    body = tool.release_notes(RELEASED, "3.26.0", limit=200)
    assert len(body.encode("utf-8")) < 125_000
    assert "- Alpha shipped." in body and "- Beta shipped." in body and "- Gamma fixed." in body
    assert "long detail" not in body, "the digest is headlines, not bodies"
    assert body.index("### Added") < body.index("- Alpha shipped.") < body.index("### Fixed") < body.index("- Gamma fixed.")
    assert "_3 entries" in body and "releases" not in body.split("_3 entries")[0]
    assert "https://github.com/navig-run/core/blob/v3.26.0/CHANGELOG.md" in body
    assert "\n\n\n" not in body, "one blank line between sections, not two"


def test_the_live_changelog_never_yields_an_oversize_release_body(tool) -> None:
    live = (_CORE / "CHANGELOG.md").read_text(encoding="utf-8")
    import re as _re

    for version in _re.findall(r"^## \[(\d+\.\d+\.\d+)\]", live, _re.M)[:4]:
        body = tool.release_notes(live, version)
        assert len(body.encode("utf-8")) <= 125_000, f"{version}: {len(body.encode())} bytes"


def test_release_notes_refuse_a_version_the_changelog_does_not_hold(tool, tmp_path, capsys) -> None:
    with pytest.raises(ValueError, match=r"no '## \[9\.9\.9\]' block"):
        tool.release_notes(RELEASED, "9.9.9")
    core = _core(tmp_path, {}, changelog=RELEASED)
    assert tool.main(["--core", str(core), "--release-notes", "9.9.9"]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_release_notes_cli_writes_utf8_bytes_whatever_the_console_codec(tool, tmp_path, monkeypatch) -> None:
    """The body carries the changelog's em dashes; a cp1251 stdout must not mangle them."""
    import io as _io
    import sys as _sys

    core = _core(tmp_path, {}, changelog=RELEASED)
    buf = _io.BytesIO()
    fake = _io.TextIOWrapper(buf, encoding="cp1251", errors="strict")
    monkeypatch.setattr(_sys, "stdout", fake)
    assert tool.main(["--core", str(core), "--release-notes", "3.26.0"]) == 0
    fake.flush()
    out = buf.getvalue().decode("utf-8")
    assert "pip install navig==3.26.0" in out and "- **Alpha shipped.**" in out


def test_every_print_in_the_release_tools_is_ascii(tool) -> None:
    """A release tool must not depend on the operator's code page: `--release` died on this
    machine's cp1251 console with UnicodeEncodeError on a check mark — AFTER it had rotated
    the changelog and deleted the fragments, leaving the release half-done."""
    for name in ("changelog_assemble.py", "version_bump.py", "_version_sync.py"):
        src = (_CORE / "tools" / name).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if "print(" in line:
                assert all(ord(c) < 128 for c in line), f"{name}:{i}: non-ASCII in a print(): {line.strip()[:70]}"
