"""Tests for navig.plans.corpus_scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.corpus_scanner import CorpusScanner


@pytest.fixture()
def corpus_tree(tmp_path: Path) -> Path:
    """Create .navig/ tree with tasks and decisions for scanning."""
    active = tmp_path / ".navig" / "plans" / "tasks" / "active"
    active.mkdir(parents=True)
    decisions = tmp_path / ".navig" / "plans" / "decisions"
    decisions.mkdir(parents=True)
    inbox = tmp_path / ".navig" / "inbox"
    inbox.mkdir(parents=True)

    # Two tasks with similar titles (should be duplicate)
    (active / "setup_auth.md").write_text(
        "---\ntitle: Setup authentication\n---\n\nAdd auth module to the application.\n",
        encoding="utf-8",
    )
    (active / "auth_setup.md").write_text(
        "---\ntitle: Setup authentication module\n---\n\nAuthenticate users via OAuth.\n",
        encoding="utf-8",
    )

    # A task with unique title (no duplicate)
    (active / "deploy.md").write_text(
        "---\ntitle: Deploy to production\n---\n\nDeploy via CI.\n",
        encoding="utf-8",
    )

    # Two items with conflicting assertions
    (decisions / "use_postgres.md").write_text(
        "---\ntitle: Use PostgreSQL\n---\n\n"
        "We should use postgresql as our primary database."
        " This is the best choice for our services.\n",
        encoding="utf-8",
    )
    (decisions / "no_postgres.md").write_text(
        "---\ntitle: Avoid PostgreSQL\n---\n\n"
        "Not use postgresql as our primary database."
        " Use Redis instead for simplicity.\n",
        encoding="utf-8",
    )

    return tmp_path


def test_scan_duplicates(corpus_tree: Path) -> None:
    scanner = CorpusScanner(corpus_tree)
    dups = scanner.scan_for_duplicates()
    # "setup authentication" is a substring of "setup authentication module"
    assert len(dups) >= 1
    paths = [(d.file_a.name, d.file_b.name) for d in dups]
    # Either order depending on sort
    found = any(
        ("setup_auth.md" in str(a) and "auth_setup.md" in str(b))
        or ("auth_setup.md" in str(a) and "setup_auth.md" in str(b))
        for a, b in paths
    )
    assert found, f"Expected duplicate pair, got {paths}"


def test_scan_no_false_positive_dup(corpus_tree: Path) -> None:
    scanner = CorpusScanner(corpus_tree)
    dups = scanner.scan_for_duplicates()
    # "deploy to production" should not duplicate "setup authentication"
    for d in dups:
        pair = {d.file_a.name, d.file_b.name}
        assert "deploy.md" not in pair, f"False positive: {pair}"


def test_scan_conflicts(corpus_tree: Path) -> None:
    scanner = CorpusScanner(corpus_tree)
    conflicts = scanner.scan_for_conflicts()
    assert len(conflicts) >= 1
    names = [(c.file_a.name, c.file_b.name) for c in conflicts]
    found = any(
        "use_postgres.md" in str(a) + str(b) and "no_postgres.md" in str(a) + str(b)
        for a, b in names
    )
    assert found, f"Expected conflict pair, got {names}"


def test_scan_no_conflicts_clean(tmp_path: Path) -> None:
    active = tmp_path / ".navig" / "plans" / "tasks" / "active"
    active.mkdir(parents=True)
    (active / "a.md").write_text(
        "---\ntitle: Feature A\n---\n\nBuild the alpha feature.\n",
        encoding="utf-8",
    )
    (active / "b.md").write_text(
        "---\ntitle: Feature B\n---\n\nBuild the beta feature.\n",
        encoding="utf-8",
    )

    scanner = CorpusScanner(tmp_path)
    conflicts = scanner.scan_for_conflicts()
    assert len(conflicts) == 0


def test_full_scan(corpus_tree: Path) -> None:
    scanner = CorpusScanner(corpus_tree)
    dups, conflicts = scanner.full_scan()
    assert isinstance(dups, list)
    assert isinstance(conflicts, list)


def test_empty_corpus(tmp_path: Path) -> None:
    scanner = CorpusScanner(tmp_path)
    dups = scanner.scan_for_duplicates()
    conflicts = scanner.scan_for_conflicts()
    assert dups == []
    assert conflicts == []
