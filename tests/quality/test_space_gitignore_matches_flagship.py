"""A space's private/committable split may be stricter than the flagship's — never looser.

The scaffold every `navig space init` writes decides what a space commits. That
decision is anchored to the flagship repo's own `.gitignore`: whatever it keeps off the
record under `.navig/` must be private in every space too, and the root `/plans` link is
ignored on both sides so `.navig/plans/` is the ONE canonical committed path on every OS
(the flagship tracks ~155 files there). This guard reads the flagship's `.gitignore`, so
it is an outside-file edge: editing that file runs this test (INVARIANT_GUARDS), and it
is in `sourceGuardArgs` so it runs on every change regardless.

The behavioural half — real `git check-ignore` verdicts on a scaffolded space, the
through-junction regression, the legacy-head migration — lives in
`tests/spaces/test_space_gitignore.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from navig.spaces import gitignore as gi

REPO = Path(__file__).resolve().parents[3]
ROOT_GITIGNORE = REPO / ".gitignore"

# Structural anchor: a miscomputed root makes every count below meaningless.
assert (REPO / "core" / "navig").is_dir(), REPO
assert ROOT_GITIGNORE.is_file(), ROOT_GITIGNORE


def _rules() -> list[str]:
    return [
        line.strip()
        for line in ROOT_GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_every_navig_path_the_flagship_ignores_is_private_in_a_space() -> None:
    flagship_navig = [r for r in _rules() if r.startswith(".navig/")]
    assert len(flagship_navig) >= 5, flagship_navig  # scan floor
    missing = [r for r in flagship_navig if r not in gi.PRIVATE_NAVIG_PATHS]
    assert not missing, (
        "the flagship ignores these under .navig/ but a scaffolded space would COMMIT "
        f"them — add each to navig.spaces.gitignore.PRIVATE_NAVIG_PATHS: {missing}"
    )


def test_the_flagship_ignores_the_root_plans_link_and_commits_the_plans() -> None:
    """The decision the scaffold encodes is the flagship's own — keep them agreeing."""
    assert "/plans" in _rules(), "the flagship no longer ignores the root plans link"
    assert "/plans" in gi.ROOT_LINKS
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", ".navig/plans"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    assert any(p.endswith("CURRENT_PHASE.md") for p in tracked), (
        "the flagship stopped committing .navig/plans — the scaffold's default no longer "
        "mirrors the product's own practice; decide again rather than let them drift"
    )
