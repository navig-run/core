"""The shipped check-in copy never tells the operator how much to take.

Its subject is the locale DATA (``navig/locales/*.json``), which no module name can
select — so it is wired into the always-run source guards in ``scripts/ci-local.mjs``.
"""

from __future__ import annotations

import json


def test_no_locale_string_gives_dose_advice():
    """Grep the shipped copy: nothing may tell the operator how much to take."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "navig" / "locales"
    banned = re.compile(
        r"(increase|decrease|double the dose|take (more|less|another)"
        r"|augmente|diminue|doubl(ez|er) la dose|prene[sz] (plus|moins)"
        r"|увелич|уменьш|удво|примите (больше|меньше))",
        re.I,
    )
    assert not banned.search("Already marked taken at 08:05. Record another dose for this time?")
    for lang in ("en", "fr", "ru"):
        data = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith("checkins.") and isinstance(value, str):
                assert not banned.search(value), (key, value)
