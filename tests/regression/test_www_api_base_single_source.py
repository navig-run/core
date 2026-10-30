"""Every navig.run page that calls the NAVIG API goes through ``lib/api-base.ts``.

``API_BASE`` honours ``NEXT_PUBLIC_API_BASE`` so a local or staging build of the site
talks to its own Worker. The enterprise enquiry form hardcoded
``https://api.navig.run/api/enterprise-lead`` instead, so a site built against a local
or staging API still posted enquiries into the PRODUCTION lead table — and the form
could never be exercised against the backend under test.

This scans the site's page and component sources for a hardcoded production API
origin in a string literal. ``lib/api-base.ts`` is the one place allowed to name it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WWW = Path(__file__).resolve().parents[3] / "web" / "www"
_SCANNED_DIRS = ("app", "components", "lib")
_ALLOWED = {"lib/api-base.ts"}
# The origin inside a JS/TS string or template literal — not in a comment or prose.
_HARDCODED = re.compile(r"""['"`]https://api\.navig\.run""")


def _sources() -> list[Path]:
    out: list[Path] = []
    for d in _SCANNED_DIRS:
        root = _WWW / d
        if root.is_dir():
            out.extend(p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"})
    return out


def test_scan_has_a_floor() -> None:
    """An empty scan passes vacuously; make sure there is something to scan."""
    if not _WWW.is_dir():
        pytest.skip("web/www is not in this checkout")
    assert len(_sources()) > 20


def test_no_page_hardcodes_the_production_api_origin() -> None:
    if not _WWW.is_dir():
        pytest.skip("web/www is not in this checkout")
    offenders: list[str] = []
    for path in _sources():
        rel = path.relative_to(_WWW).as_posix()
        if rel in _ALLOWED or "node_modules" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _HARDCODED.search(line):
                offenders.append(f"web/www/{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "build API URLs from API_BASE (web/www/lib/api-base.ts), not a hardcoded origin:\n"
        + "\n".join(offenders)
    )
