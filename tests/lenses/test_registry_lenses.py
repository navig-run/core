"""The starter Lenses in registry/lenses/ — what `navig install add lens:…` will fetch.

Every one must parse with the real store parser, keep the id its index promises, be
narrow (no `<all_urls>` — a starter that touches every site is the opposite of a safe
default), and agree with registry.json. Wired as an INVARIANT_GUARDS edge in
scripts/ci-local.mjs, so editing a Lens or the index runs this test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.lenses import parse_manifest
from navig.lenses.compile import check_source_syntax

REGISTRY = Path(__file__).resolve().parents[3] / "registry" / "lenses"
INDEX = json.loads((REGISTRY / "registry.json").read_text(encoding="utf-8"))
ENTRIES = INDEX["lenses"]


def test_the_index_was_actually_read() -> None:
    assert len(ENTRIES) >= 4  # a vacuity floor: a broken path must not pass with zero Lenses
    on_disk = sorted(p.parent.name for p in REGISTRY.glob("*/lens.user.js"))
    assert sorted(e["id"] for e in ENTRIES) == on_disk, (
        "every Lens folder is indexed, and only those"
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_each_lens_parses_and_matches_its_index_entry(entry: dict) -> None:
    source = (REGISTRY / entry["file"]).read_text(encoding="utf-8")
    m = parse_manifest(source)
    assert m.id == entry["id"], "@navig-id is the id the install command and updates rely on"
    assert m.name == entry["name"] and m.version == entry["version"]
    assert m.description == entry["description"]
    assert sorted(m.grants) == sorted(entry["grants"])
    assert m.unsupported_grants == [] and m.warnings == []
    assert m.runs_anywhere, "a starter Lens must run somewhere"
    assert "<all_urls>" not in m.matches and not m.includes, "starters stay narrow"
    assert m.capability is None, "starter Lenses are free"
    url = m.extra.get("downloadURL", [""])[0]
    assert (
        url == f"https://raw.githubusercontent.com/navig-run/registry/main/lenses/{entry['file']}"
    )
    assert entry["install"] == f"navig install add lens:navig-run/registry/lenses/{entry['id']}"
    ok, message = check_source_syntax(source)
    assert ok is not False, message
