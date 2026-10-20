"""Every ``navig-*`` package a core extra names must exist on PyPI.

`pip install "navig[notes]"` is a promise: if that package is not on PyPI, the install dies
with a 404 naming a package the user has never heard of — and nothing in the repo catches it,
because `check_plugins_publishable.py` validates extras against `plugins/` on DISK, not against
the index an installer actually talks to.

So this test asks PyPI the same question pip would. Two failure modes, both fail:

  * a package an extra names is absent from PyPI (the promise is broken), and
  * a name sits in ``PENDING`` while the package IS on PyPI (the exemption has rotted —
    delete the entry; keeping it would silently permit a future break).

Skips only when the index cannot be reached at all, and says so: an offline run cannot answer
this question, and a silent green tick over an unasked question is the failure mode this repo
keeps writing tests about.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import tomllib

CORE = Path(__file__).resolve().parents[2]

#: Packages that are published-but-behind-PyPI's new-project rate limit (HTTP 429). Each entry
#: is dated and self-invalidates: once the package is on PyPI the stale-entry check fails and
#: the entry must go. Empty is the goal.
PENDING: dict[str, str] = {}


def _named_packages() -> dict[str, list[str]]:
    """``{package: [extras that name it]}`` for every ``navig-*`` requirement in core extras."""
    data = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"].get("optional-dependencies") or {}
    out: dict[str, list[str]] = {}
    for extra, reqs in extras.items():
        for req in reqs:
            name = req.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip()
            if name.lower().startswith("navig-"):
                out.setdefault(name, []).append(extra)
    return out


def _on_pypi(name: str) -> bool:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=10) as r:
            json.load(r)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def test_every_extra_package_exists_on_pypi() -> None:
    named = _named_packages()
    assert named, "no navig-* packages in core extras — the extras moved, check this test"

    missing: list[str] = []
    present: list[str] = []
    try:
        for name in sorted(named):
            (present if _on_pypi(name) else missing).append(name)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.skip(f"PyPI unreachable ({exc}) — extras cannot be verified offline")

    unexpected = [n for n in missing if n not in PENDING]
    assert not unexpected, (
        "core extras name packages that are NOT on PyPI, so "
        + ", ".join(f"`pip install navig[{e}]`" for n in unexpected for e in named[n])
        + " would 404. Publish them (`node scripts/publish-plugins.mjs <pkg> --publish`) or "
        f"remove the extra until they are published. Offenders: {unexpected}"
    )

    stale = [n for n in sorted(PENDING) if n in present]
    assert not stale, (
        f"{stale} are in PENDING but ARE on PyPI — delete those entries, they now permit a "
        "future break silently"
    )
