"""The generated model catalog must equal what the Python tables say.

The UI's model picker shows a price, a context window and a max-output figure for
every model it can call. Those numbers come from ONE generated file
(``apps/os/packages/shared/src/config/model-catalog.generated.json``) rendered by
``core/tools/gen_model_catalog.py`` from ``BUILTIN_PROVIDERS``, ``ALL_PROVIDERS``
and ``usage_tracker.PRICE_TABLE``.

This file is the contract that keeps it true. Without it, the classic failure
returns: a model is added to a provider, the JSON is not regenerated, and the
picker either hides the model or shows a price from three releases ago.

Regenerate with::

    py -3.13 core/tools/gen_model_catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_ROOT = _REPO_ROOT / "core"
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))

_CATALOG_PATH = (
    _REPO_ROOT
    / "apps"
    / "os"
    / "packages"
    / "shared"
    / "src"
    / "config"
    / "model-catalog.generated.json"
)


def _generator():
    sys.path.insert(0, str(_REPO_ROOT / "core" / "tools"))
    import gen_model_catalog  # noqa: PLC0415 — a tool, imported on demand

    return gen_model_catalog


def _committed() -> dict:
    assert _CATALOG_PATH.exists(), (
        f"generated catalog missing at {_CATALOG_PATH} — run `py -3.13 core/tools/gen_model_catalog.py`"
    )
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def test_generated_file_matches_the_python_tables():
    """The committed artifact is byte-identical to a fresh render."""
    generator = _generator()
    expected = generator.render(generator.build_catalog())
    actual = _CATALOG_PATH.read_text(encoding="utf-8")
    if actual != expected:
        # Report WHAT drifted before the byte diff, so the message is actionable.
        committed = json.loads(actual)
        fresh = json.loads(expected)
        declared_now = {(m["publisher"], m["id"]) for m in fresh["models"]}
        declared_then = {(m["publisher"], m["id"]) for m in committed["models"]}
        added = sorted(declared_now - declared_then)
        removed = sorted(declared_then - declared_now)
        changed = sorted(
            f"{publisher}::{model_id}"
            for publisher, model_id in declared_now & declared_then
            if _row(fresh, publisher, model_id) != _row(committed, publisher, model_id)
        )
        pytest.fail(
            "model-catalog.generated.json is stale — run "
            "`py -3.13 core/tools/gen_model_catalog.py`\n"
            f"  added:   {added[:10]}\n"
            f"  removed: {removed[:10]}\n"
            f"  changed: {changed[:10]}"
        )


def _row(catalog: dict, publisher: str, model_id: str) -> dict | None:
    for row in catalog["models"]:
        if row["publisher"] == publisher and row["id"] == model_id:
            return row
    return None


def test_every_declared_provider_model_is_in_the_catalog():
    """A provider model the picker cannot price is a model it cannot show."""
    from navig.providers.types import BUILTIN_PROVIDERS

    catalog = _committed()
    present = {(row["publisher"], row["id"]) for row in catalog["models"]}
    missing = [
        f"{provider}::{model.id}"
        for provider, config in BUILTIN_PROVIDERS.items()
        for model in config.models
        if (provider, model.id) not in present
    ]
    assert missing == [], f"declared but not in the generated catalog: {missing}"


def test_every_priced_model_is_in_the_catalog():
    from navig.agent.usage_tracker import PRICE_TABLE

    catalog = _committed()
    priced = set(catalog["prices"])
    missing = sorted(set(PRICE_TABLE) - priced)
    assert missing == [], f"missing from the catalog's price map: {missing}"


def test_price_rows_match_the_price_table_exactly():
    """The picker must not round, re-band or 'helpfully' adjust a price."""
    from navig.agent.usage_tracker import PRICE_TABLE

    catalog = _committed()
    for model_id, (inp, out, cache_read, cache_write) in PRICE_TABLE.items():
        assert catalog["prices"][model_id] == [inp, out, cache_read, cache_write], (
            f"{model_id}: catalog price disagrees with PRICE_TABLE"
        )


def test_free_is_only_claimed_for_a_zero_cost_row():
    """``free`` drives a badge in the picker — it may never be a guess."""
    for row in _committed()["models"]:
        zero = row.get("input", 0) == 0 and row.get("output", 0) == 0
        assert row["free"] is zero, f"{row['publisher']}::{row['id']} free flag disagrees with cost"


def test_match_rules_are_exported_from_the_price_matcher():
    """The TS port of ``model_price_key`` reads these, so they cannot drift."""
    from navig.agent import usage_tracker

    rules = _committed()["matchRules"]
    assert tuple(rules["variantSeparators"]) == usage_tracker._VARIANT_SEPARATORS
    assert tuple(rules["sizeSuffixes"]) == usage_tracker._SIZE_SUFFIXES
    assert rules["capacitySuffixPattern"] == usage_tracker._CAPACITY_SUFFIX.pattern


def test_publishers_carry_a_label_and_a_tier():
    catalog = _committed()
    assert catalog["publishers"], "no publishers in the catalog"
    for publisher_id, meta in catalog["publishers"].items():
        assert meta["label"].strip(), f"{publisher_id} has no display label"
        assert meta["tier"] in {"cloud", "local", "proxy"}, publisher_id

    # Provider ids in ALL_PROVIDERS must all be known, or the rail would show a
    # publisher with no name.
    from navig.providers.registry import ALL_PROVIDERS

    unknown = [m.id for m in ALL_PROVIDERS if m.id not in catalog["publishers"]]
    assert unknown == [], f"providers missing from the catalog's publisher table: {unknown}"
