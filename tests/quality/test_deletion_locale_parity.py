"""The deletion digest speaks the operator's language, in every shipped locale.

A tree-scanning guard (it reads every `locales/*.json`), so it lives here and runs
in the fast gate — `tests for changed modules` can never select it from a locale
edit, which is the one change most likely to break it.

Why it matters here specifically: this account runs in Russian. A missing key
falls back to English silently, which is how a localized surface rots one string
at a time.
"""

from __future__ import annotations

import navig.telegram.deletions as d


def test_every_locale_carries_the_digest_copy():
    """The card and its buttons reach the operator in THEIR language — this account
    runs in Russian. A missing key falls back to English silently, which is how a
    localized surface rots one string at a time."""
    import json
    from pathlib import Path

    keys = {
        "deletions.digest.head", "deletions.digest.chats", "deletions.digest.tail",
        "deletions.button.show", "deletions.button.quiet",
        "deletions.toast.off", "deletions.off.note",
    }
    locales = Path(d.__file__).resolve().parents[1] / "locales"
    files = sorted(locales.glob("*.json"))
    assert len(files) >= 3, f"expected the shipped locales, found {[f.name for f in files]}"
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        missing = sorted(keys - set(data))
        assert not missing, f"{f.name} is missing {missing}"
        # A count placeholder that a translation dropped renders "Show" with no
        # number — the one thing the button has to tell the operator.
        assert "{n}" in data["deletions.button.show"], f"{f.name}: show button lost {{n}}"
        assert "{n}" in data["deletions.digest.head"], f"{f.name}: digest head lost {{n}}"
