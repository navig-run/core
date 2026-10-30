"""navig.run/schema/v1.json must be the navig-menu schema the runtime actually accepts.

Every generated ``.navig/menu.json`` names ``"$schema": "https://navig.run/schema/v1.json"``.
The copy served from ``web/www/public/schema/v1.json`` was a stale snapshot of
``plugins/navig-menu/schema/v1.json``: it lacked ``groups``, ``overrides``, ``plugins``,
``ui``, ``extra[].cwd`` and the ``purple`` accent, while its root sets
``additionalProperties: false`` -- so editors validating against the published URL
rejected valid menus, including every newly generated one (they all carry ``ui``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CANONICAL = _REPO / "plugins" / "navig-menu" / "schema" / "v1.json"
_PUBLISHED = _REPO / "web" / "www" / "public" / "schema" / "v1.json"
_REPO_MENU = _REPO / ".navig" / "menu.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(_REPO).as_posix()} is not in this checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def test_published_schema_is_the_canonical_plugin_schema() -> None:
    # Compared as parsed JSON: a Windows checkout may hold either file with CRLF.
    assert _load(_PUBLISHED) == _load(_CANONICAL), (
        "web/www/public/schema/v1.json drifted from plugins/navig-menu/schema/v1.json -- "
        "copy the plugin schema over the published one"
    )


def test_published_schema_accepts_the_repo_menus_top_level_keys() -> None:
    schema = _load(_PUBLISHED)
    menu = _load(_REPO_MENU)
    declared = set(schema.get("properties", {}))
    undeclared = sorted(k for k in menu if k not in declared)
    assert schema.get("additionalProperties") is not False or not undeclared, (
        f"navig.run/schema/v1.json rejects keys .navig/menu.json uses: {undeclared}"
    )
