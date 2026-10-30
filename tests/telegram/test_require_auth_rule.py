"""One rule for every `telegram.require_auth` reader: empty means LOCKED.

`coerce_bool("")` returns **False** — measured — so a reader that coerces the
raw value straight opens the bot and the Mini App on a value nobody wrote as
"off". #2037 routed every reader through `require_auth_enabled()`
(`navig/telegram/config.py`), which keeps sign-in on for empty/missing and only
honours an explicit off.

Two guarantees:
* the helper's own truth table;
* a §10 sweep — a rule spread over nine files must stay greppable, not local.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_empty_and_missing_mean_unset_locked():
    from navig.telegram.config import require_auth_enabled

    assert require_auth_enabled("") is True
    assert require_auth_enabled("   ") is True
    assert require_auth_enabled(None) is True
    assert require_auth_enabled("false") is False
    assert require_auth_enabled("0") is False
    assert require_auth_enabled(False) is False
    assert require_auth_enabled("nope") is True  # unknown -> default on
    assert require_auth_enabled(True) is True


# A `require_auth` value that is NOT the telegram. / gateway. config key, so the
# helper does not apply. Every entry carries why — an exclusion without a verdict
# is how a raw reader hides in a baseline.
NOT_TELEGRAM_CONFIG = {
    "modes/manager.py": (
        "a work-mode record's own boolean field (data.get(..., False), beside "
        "description/color) — never the telegram config key, and its default "
        "means 'no PIN for this mode', which the helper would invert"
    ),
}

READ_PATTERN = re.compile(
    r"""get\(\s*["']require_auth["']\s*,"""
    r"""|coerce_bool\([^)]*require_auth[^)]*\)"""
    r"""|_bool_icon\([^)]*require_auth[^)]*\)"""
)


def test_every_require_auth_reader_routes_through_the_helper():
    root = Path(__file__).resolve().parents[2] / "navig"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in READ_PATTERN.finditer(text):
            if rel in NOT_TELEGRAM_CONFIG:
                continue
            window = text[max(0, match.start() - 300) : match.end() + 120]
            if "require_auth_enabled" in window:
                continue
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line}")
    assert not offenders, f"raw require_auth reads: {offenders}"
