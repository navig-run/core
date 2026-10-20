"""The first-party registry repository — the ONE place that names it.

Free Spaces, Skills, Personas, Blocks and Lenses install from a public GitHub repository
(`navig install add …`, `navig space install …`). It was ``navig-run/community`` until
2026-10-16, when it became ``navig-run/registry``; the old address no longer exists.

Every reader goes through this module instead of spelling the address:

* :data:`OWNER` / :data:`REPO` build new specs and URLs;
* :func:`canonical_repo` / :func:`normalize_spec` map the old name onto the new one, so a
  ``github:navig-run/community/…`` command copied from older docs still installs;
* :func:`is_first_party` is the trust question Blocks and the Bay paywall ask — it must
  answer the same for both names, or a stale spec would be treated as third party (Blocks:
  refused; paywall: priced items installed ungated).

No imports: this is read on every install and must stay free.
"""

from __future__ import annotations

OWNER = "navig-run"
REPO = "registry"
#: Names the repository had before; specs using them are rewritten to :data:`REPO`.
LEGACY_REPOS = frozenset({"community"})

GITHUB_URL = f"https://github.com/{OWNER}/{REPO}"
RAW_URL = f"https://raw.githubusercontent.com/{OWNER}/{REPO}"
API_CONTENTS_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents"


def canonical_repo(owner: str, repo: str) -> str:
    """``repo`` as it is called today (a legacy name of OUR repository → :data:`REPO`)."""
    if owner.lower() == OWNER and repo.lower() in LEGACY_REPOS:
        return REPO
    return repo


def is_first_party(owner: str | None, repo: str | None) -> bool:
    """True for the first-party registry under its current OR a legacy name."""
    if not owner or not repo:
        return False
    return owner.lower() == OWNER and canonical_repo(owner, repo).lower() == REPO


def normalize_spec(spec: str) -> str:
    """Rewrite a legacy ``navig-run/<old>`` reference in an install spec or URL to today's name.

    Matches the owner/repo pair only as whole path segments, so ``navig-run/community-x``
    (a different repository) is left alone.
    """
    out = spec
    for old in LEGACY_REPOS:
        for sep in ("/", "@", "]", ""):
            needle = f"{OWNER}/{old}{sep}"
            if sep == "":
                if out.endswith(f"{OWNER}/{old}"):
                    out = out[: -len(old)] + REPO
                continue
            out = out.replace(needle, f"{OWNER}/{REPO}{sep}")
    return out
