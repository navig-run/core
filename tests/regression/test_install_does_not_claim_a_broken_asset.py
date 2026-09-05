"""`navig install` must not print ✓ for the parts that did not happen.

Three sites in one path, each ending in an unqualified green tick:

* a `SKILL.md` that RAISED while parsing printed ``✓ Installed`` and dropped the
  exception — while the branch directly above it, for the milder "parsed to nothing",
  correctly warned. Parsing is the only validation this install performs, so the one
  case where it blew up was the one case that claimed success.
* a failed lockfile write was a ``ch.dim`` aside — the quietest sink available — and
  what it loses is the block's TAMPER EVIDENCE: without the pinned digest a later
  ``navig apply`` cannot tell that the block on disk is the one that was installed.
* a failed skill-shim write was a silent ``pass``. The shim is what makes an installed
  block appear in the skill surfaces, so without it the block is installed and
  invisible — which looks exactly like a failed install.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands import install as install_mod


class _Recorder:
    """Captures what each console sink was told, so a test can assert which one
    carried the message — the whole defect was using the wrong sink."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.info: list[str] = []
        self.warning: list[str] = []
        self.dim: list[str] = []
        for sink in ("info", "warning", "dim"):
            monkeypatch.setattr(
                install_mod.ch, sink, lambda m, *a, _s=sink, **k: getattr(self, _s).append(m)
            )

    @property
    def ticks(self) -> list[str]:
        return [m for m in self.info if "✓" in m]


class _Block:
    id = "demo-block"
    name = "Demo Block"
    version = "1.0.0"
    digest = "sha256:abc"


def _install_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    lock_raises: bool = False,
    shim_raises: bool = False,
) -> _Recorder:
    """Drive the real `_finalize_block_install` with the two fallible steps stubbed."""
    import navig.blocks.loader as loader_mod
    import navig.blocks.policy as policy_mod
    import navig.platform.paths as paths_mod

    (tmp_path / "BLOCK.md").write_text("# demo\n", encoding="utf-8")

    monkeypatch.setattr(loader_mod, "parse_block_file", lambda p: _Block())
    monkeypatch.setattr(paths_mod, "find_app_root", lambda: tmp_path)

    def _write_lock(*a: object, **k: object) -> None:
        if lock_raises:
            raise OSError("lockfile is read-only")

    def _write_shim(*a: object, **k: object) -> None:
        if shim_raises:
            raise OSError("permission denied")

    monkeypatch.setattr(policy_mod, "write_lock_entry", _write_lock)
    monkeypatch.setattr(loader_mod, "write_skill_shim", _write_shim)

    rec = _Recorder(monkeypatch)
    install_mod._finalize_block_install(tmp_path, "demo-block")
    return rec


def test_a_clean_block_install_still_reports_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The partner assertion — a change that always warned would satisfy every test
    below while destroying the normal path."""
    rec = _install_block(monkeypatch, tmp_path)

    assert rec.ticks, "a fully successful block install must still print a ✓"
    assert not rec.warning


def test_a_failed_lockfile_pin_is_not_a_dim_aside_under_a_green_tick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _install_block(monkeypatch, tmp_path, lock_raises=True)

    assert not rec.ticks, "claimed a clean install while the digest was not pinned"
    assert any("tamper evidence" in m for m in rec.warning), (
        "the message must say what was lost, not just that a write failed"
    )


def test_a_failed_skill_shim_is_not_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _install_block(monkeypatch, tmp_path, shim_raises=True)

    assert not rec.ticks
    assert any("skill surfaces" in m for m in rec.warning)


def test_both_failures_are_reported_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Collecting into a list rather than returning early is the point: an install can
    lose its tamper evidence AND its discoverability, and the user needs both."""
    rec = _install_block(monkeypatch, tmp_path, lock_raises=True, shim_raises=True)

    assert not rec.ticks
    assert any("tamper evidence" in m for m in rec.warning)
    assert any("skill surfaces" in m for m in rec.warning)


def test_the_block_name_is_still_reported_when_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A degraded install must still say WHAT was installed and where — dropping the
    identifying line would trade one unusable message for another."""
    rec = _install_block(monkeypatch, tmp_path, lock_raises=True)

    assert any("demo-block" in m and "Demo Block" in m for m in rec.warning)


# ── the skill branch: the one that actually printed ✓ over an exception ──────


def _install_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, parse_result: object
) -> _Recorder:
    """Drive the real `install_asset` for a skill, with only the network stubbed.

    `parse_result` is either a skill-ish object, ``None`` (the mild case that always
    warned), or an Exception instance to raise (the case that claimed success).
    """
    import navig.skills.loader as loader_mod

    monkeypatch.setattr(
        install_mod, "_download_subtree", lambda *a, **k: {"added": 1, "refreshed": 0,
                                                          "preserved": 0}
    )

    def _parse(_path: Path) -> object:
        if isinstance(parse_result, Exception):
            raise parse_result
        return parse_result

    monkeypatch.setattr(loader_mod, "parse_skill_file", _parse)

    rec = _Recorder(monkeypatch)
    install_mod.install_asset(
        "github:navig-run/community/cli-skills/demo",
        force=True,
        install_root=tmp_path,
    )
    return rec


class _Skill:
    name = "Demo Skill"


def test_a_skill_that_parses_reports_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _install_skill(monkeypatch, tmp_path, parse_result=_Skill())

    assert any("Demo Skill" in m for m in rec.ticks)
    assert not rec.warning


def test_a_skill_whose_parse_RAISES_does_not_report_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect. Parsing SKILL.md is the only validation this install performs, so
    the one case where it blew up was also the one case that printed ✓ — while the
    branch directly above, for the milder 'parsed to nothing', warned correctly."""
    rec = _install_skill(
        monkeypatch, tmp_path, parse_result=UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
    )

    assert not rec.ticks, "printed a green tick over a SKILL.md that could not be read"
    assert any("could not be read" in m for m in rec.warning)


def test_a_skill_that_parses_to_nothing_still_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The neighbouring branch, pinned so the two stay consistent — it was already
    right, and it is the reason the exception branch was so clearly wrong."""
    rec = _install_skill(monkeypatch, tmp_path, parse_result=None)

    assert not rec.ticks
    assert any("did not parse" in m for m in rec.warning)
