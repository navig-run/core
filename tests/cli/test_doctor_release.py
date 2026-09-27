"""`navig doctor` → Release: the tree, the newest tag and PyPI agree on what is shipped.

Three places each answer "what version is navig?" and nothing compared them. Measured
2026-09-19: pyproject.toml 3.25.0, PyPI 3.25.0, newest origin tag v3.24.0 — 3.25.0 had
been twine-uploaded by hand with no tag (the org's Actions is billing-blocked, so the tag
workflow never fires) and that stayed invisible for two weeks. The judgement is a pure
function of four facts, so every disagreement shape is pinned without git or network; the
rows that read the machine are tested for scoping (dev checkout only) and for the one
property every doctor row must keep: never green over an unknown.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from navig.commands import doctor


def _by_label(rows) -> dict[str, doctor.CheckResult]:
    return {r.label: r for r in rows}


# ── the judgement ────────────────────────────────────────────────────────────


def test_all_three_agree_is_green() -> None:
    rows = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.25.0", "3.25.0"))
    assert all(r[1] is True for r in rows.values()), rows
    assert rows["Release · tag"].detail == "v3.25.0 tagged"
    assert rows["Release · PyPI"].detail == "3.25.0 published"


def test_the_measured_shape_published_but_never_tagged() -> None:
    rows = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.24.0", "3.25.0"))
    tag = rows["Release · tag"]
    assert tag[0] == doctor._WARN and tag[1] is False
    assert "newest tag is v3.24.0" in tag.detail and "tools/release.sh 3.25.0" in tag.detail
    assert rows["Release · PyPI"][1] is True


def test_a_tree_ahead_of_pypi_names_the_publish_command() -> None:
    rows = _by_label(doctor._judge_release("3.26.0", "3.26.0", "3.26.0", "3.25.0"))
    pypi = rows["Release · PyPI"]
    assert pypi[0] == doctor._WARN
    assert "PyPI has 3.25.0" in pypi.detail and "release.sh 3.26.0 --publish" in pypi.detail


def test_pypi_ahead_of_the_tree_is_an_error_not_a_nudge() -> None:
    rows = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.25.0", "3.26.0"))
    pypi = rows["Release · PyPI"]
    assert pypi[0] == doctor._ERR and "published from elsewhere" in pypi.detail


def test_a_tag_over_a_tree_of_another_version_is_an_error() -> None:
    """release.sh 3.26.0 on a 3.25.0 tree used to produce exactly this."""
    rows = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.26.0", "3.25.0"))
    tag = rows["Release · tag"]
    assert tag[0] == doctor._ERR and "points at a tree of another version" in tag.detail


def test_manifest_drift_is_an_error_and_a_missing_manifest_a_warn() -> None:
    drift = _by_label(doctor._judge_release("3.25.0", "3.24.0", "3.25.0", "3.25.0"))["Release · tree"]
    assert drift[0] == doctor._ERR and "latest.json says 3.24.0" in drift.detail and "_version_sync" in drift.detail
    missing = _by_label(doctor._judge_release("3.25.0", None, "3.25.0", "3.25.0"))["Release · tree"]
    assert missing[0] == doctor._WARN and "missing or unreadable" in missing.detail


def test_pypi_unreachable_is_a_warn_that_says_why_never_a_tick() -> None:
    rows = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.25.0", None, pypi_error="URLError"))
    pypi = rows["Release · PyPI"]
    assert pypi[0] == doctor._WARN and pypi[1] is False
    assert "not checked (URLError)" in pypi.detail


def test_no_tag_at_all_is_a_warn_that_suggests_fetching() -> None:
    tag = _by_label(doctor._judge_release("3.25.0", "3.25.0", None, "3.25.0"))["Release · tag"]
    assert tag[0] == doctor._WARN and "git fetch --tags" in tag.detail


def test_pending_changelog_work_is_counted_on_the_pypi_row() -> None:
    rows = _by_label(doctor._judge_release(
        "3.25.0", "3.25.0", "3.25.0", "3.25.0", pending_entries=55, pending_fragments=5,
    ))
    assert "55 [Unreleased] entries + 5 fragments await a release" in rows["Release · PyPI"].detail
    one = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.25.0", "3.25.0", pending_entries=1, pending_fragments=1))
    assert "1 [Unreleased] entry + 1 fragment await" in one["Release · PyPI"].detail
    none = _by_label(doctor._judge_release("3.25.0", "3.25.0", "3.25.0", "3.25.0"))
    assert "await" not in none["Release · PyPI"].detail


def test_semver_compares_numerically_not_lexically() -> None:
    """3.10.0 is newer than 3.9.0; a string compare says otherwise."""
    rows = _by_label(doctor._judge_release("3.10.0", "3.10.0", "3.9.0", "3.9.0"))
    assert "not tagged yet" in rows["Release · tag"].detail
    assert "unreleased" in rows["Release · PyPI"].detail


# ── the readers ──────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_check_release_is_silent_outside_a_navig_checkout(tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text('[project]\nname = "something-else"\nversion = "1.0.0"\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_invocation_repo_root", lambda: other)
    assert doctor.check_release() == []
    monkeypatch.setattr(doctor, "_invocation_repo_root", lambda: None)
    assert doctor.check_release() == []


def test_check_release_reads_the_tree_the_tags_and_the_pending_work(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "navig"
    core = root / "core"
    (core / "changelog.d").mkdir(parents=True)
    (core / "pyproject.toml").write_text('[project]\nname = "navig"\nversion = "3.26.0"\n', encoding="utf-8")
    (core / "latest.json").write_text('{"version": "3.26.0"}\n', encoding="utf-8")
    (core / "CHANGELOG.md").write_text(
        "# C\n\n## [Unreleased]\n\n### Added\n- **A.** a\n- **B.** b\n\n## [3.25.0] x\n- **old.**\n", encoding="utf-8",
    )
    (core / "changelog.d" / "README.md").write_text("# r\n", encoding="utf-8")
    (core / "changelog.d" / "x.fixed.md").write_text("- **X.**\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "base", cwd=root)
    _git("tag", "-a", "v3.9.0", "-m", "old", cwd=root)
    _git("tag", "-a", "v3.25.0", "-m", "x", cwd=root)
    _git("tag", "not-a-version", cwd=root)
    monkeypatch.setattr(doctor, "_invocation_repo_root", lambda: root)
    monkeypatch.setattr(doctor, "_pypi_latest", lambda *a, **k: ("3.25.0", None))

    rows = _by_label(doctor.check_release())
    assert rows["Release · tree"][1] is True
    assert "newest tag is v3.25.0, tree is 3.26.0" in rows["Release · tag"].detail, "v:refname sort, not lexical"
    assert "PyPI has 3.25.0" in rows["Release · PyPI"].detail
    assert "2 [Unreleased] entries + 1 fragment await" in rows["Release · PyPI"].detail


def test_pypi_offline_switch_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_VERSION_SYNC_OFFLINE", "1")
    assert doctor._pypi_latest() == (None, "NAVIG_VERSION_SYNC_OFFLINE")


# ── hard first-party deps: the next wheel must be installable (2026-09-27) ──
#
# Core has required navig-contacts>=0.1.0 since #1306; it was never uploaded (404), so the
# next core wheel could not be installed by anyone while every Release row stayed green.

PYPROJECT = '''[project]
name = "navig"
version = "3.25.0"
dependencies = [
  "rich>=13",
  # navig-audio is an extra, and this comment names navig-fake>=9.9.9 in prose
  "navig-vault>=0.6.0",
  "navig-contacts>=0.1.0",
]

[project.optional-dependencies]
audio = ["navig-audio>=0.2.0"]
'''


def test_hard_deps_are_read_from_required_dependencies_only() -> None:
    assert doctor._hard_first_party_deps(PYPROJECT) == [
        ("navig-vault", "0.6.0"), ("navig-contacts", "0.1.0"),
    ], "extras and comment prose are not hard dependencies"
    assert doctor._hard_first_party_deps('[project]\nname = "navig"\n') == []


def test_a_hard_dep_missing_from_pypi_is_an_error_naming_the_fix() -> None:
    row = doctor._judge_hard_deps([
        ("navig-vault", "0.6.0", "0.6.2", None),
        ("navig-contacts", "0.1.0", None, doctor._NOT_ON_PYPI),
    ])
    assert row[0] == doctor._ERR and row[1] is False
    assert "navig-contacts not on PyPI" in row.detail
    assert "uninstallable" in row.detail
    assert "publish-plugins.mjs navig-contacts --publish" in row.detail


def test_a_hard_dep_behind_its_floor_is_an_error() -> None:
    row = doctor._judge_hard_deps([("navig-vault", "0.6.0", "0.5.9", None)])
    assert row[0] == doctor._ERR and "PyPI has navig-vault 0.5.9 < 0.6.0" in row.detail


def test_an_unreachable_pypi_is_a_warn_not_an_accusation() -> None:
    row = doctor._judge_hard_deps([("navig-contacts", "0.1.0", None, "URLError")])
    assert row[0] == doctor._WARN and "not checked: navig-contacts (URLError)" in row.detail
    assert "uninstallable" not in row.detail


def test_all_hard_deps_published_is_green_and_none_is_silent() -> None:
    row = doctor._judge_hard_deps([("navig-vault", "0.6.0", "0.6.2", None)])
    assert row[0] == doctor._OK and "navig-vault 0.6.2 published" in row.detail
    assert doctor._judge_hard_deps([]) is None


def test_a_pypi_404_is_reported_as_not_on_pypi(monkeypatch) -> None:
    import io
    import urllib.error
    import urllib.request

    def raise_404(*_a, **_k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO())

    monkeypatch.delenv("NAVIG_VERSION_SYNC_OFFLINE", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", raise_404)
    assert doctor._pypi_latest("navig-contacts") == (None, doctor._NOT_ON_PYPI)


def test_check_release_appends_the_hard_deps_row(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "navig"
    (root / "core").mkdir(parents=True)
    (root / "core" / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "core" / "latest.json").write_text('{"version": "3.25.0"}\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_invocation_repo_root", lambda: root)
    monkeypatch.setattr(doctor, "_newest_local_tag", lambda _r: "3.25.0")
    published = {"navig": "3.25.0", "navig-vault": "0.6.2"}
    monkeypatch.setattr(
        doctor, "_pypi_latest",
        lambda pkg="navig", **_k: (published[pkg], None) if pkg in published else (None, doctor._NOT_ON_PYPI),
    )
    rows = _by_label(doctor.check_release())
    assert "navig-contacts not on PyPI" in rows["Release · hard deps"].detail
